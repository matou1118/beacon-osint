"""按**实测增量**调整 ``archive_pages``，不靠拍脑袋。

为什么需要
----------
2026-10-05 实测：库 14110 条，10-04 入库 **0 条**，10-05 只有 16 条，
135/143 个源卡在退避里。第一反应是「源枯竭了，只能加新源」——
但 ``measure_pagination_headroom.py`` 量出来不是这样：

    源          库内   depth 12 未入库   depth 30 未入库
    sc_nx_gov     -            -                399
    js_gxt       484          316               316   （卡在 800 条上限）
    moa_gov      290            0               205
    cq_fgw       432            0               203
    mem_gov        -            -               175
    cq_ny         -            -               170
    sd_rst       723           77                77   （卡在 800 条上限）
    mct_gov       59           63                63

所以真相是**深度不够，不是没有数据**。而 143 个源全部在 sources.yaml 里
显式写了 ``archive_pages``（3/6/12 三档），于是命令行 ``--depth 30``
被 ``with_defaults`` 的「显式值优先」规则挡住，**从来没生效过**。

这不是 bug，是 10-02 那次修复的既定语义（B/C 分档不许被命令行覆盖）——
所以要改深度就只能改 YAML，而改之前必须先有证据。这个脚本就是那条证据链：
读一份测量报告，只给**确实有增量**的源抬深度，其余一律不动。

只调「有实测增量」的源，还把单源耗时一起写进报告：深度换数据是有成本的，
没人知道代价就会一路加到轮次跑不完。

用法：
    python scripts/apply_depth_headroom.py data/runtime/lighthouse/depth_headroom.json --apply
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "config" / "sources.yaml"
DEFAULT_REPORT = ROOT / "data" / "runtime" / "lighthouse" / "depth_headroom.json"


def load_report(path: Path) -> dict:
    if not path.exists():
        raise SystemExit("找不到报告 %s —— 先跑 measure_pagination_headroom.py" % path)
    return json.loads(path.read_text(encoding="utf-8"))


def plan(report: dict, min_gain: int, max_pages: int,
          max_seconds: float) -> dict[str, dict]:
    """决定每个源该调到多少页。只处理有实测增量的源。"""
    out: dict[str, dict] = {}
    for sid, row in (report.get("sources") or {}).items():
        best_gain, best_pages = 0, None
        best_secs = None
        for probe in row.get("probes") or []:
            gain = int(probe.get("fresh") or 0)
            secs = float(probe.get("seconds") or 0.0)
            pages = int(probe.get("depth") or 0)
            if gain < min_gain:
                continue
            # 同一产出量下取最浅的深度 —— 深度是成本，不能白花
            if best_pages is None or pages < best_pages:
                best_gain, best_pages, best_secs = gain, pages, secs
        if best_pages is None:
            continue
        if best_secs is not None and best_secs > max_seconds:
            # 单源耗时超预算：只在「更深也没多拿」时才值得，见 report 里
            # 更深一档的 fresh 是否更大。保守起见先跳过并记录原因。
            deeper = [p for p in row["probes"]
                      if int(p.get("depth") or 0) > best_pages
                      and int(p.get("fresh") or 0) > best_gain]
            if not deeper:
                continue
        cur = int(row.get("current_archive_pages") or 0)
        target = min(best_pages, max_pages)
        if target > cur:
            out[sid] = {"current": cur, "target": target,
                        "gain": best_gain, "seconds": best_secs}
    return out


def apply_plan(plan_map: dict[str, dict]) -> int:
    import yaml

    path = SOURCES
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    changed = 0
    for src in data.get("sources") or []:
        sid = str(src.get("id") or "")
        item = plan_map.get(sid)
        if not item:
            continue
        src["archive_pages"] = int(item["target"])
        note = src.get("notes")
        tag = ("深度 %s→%s（实测多 %s 条未入库）"
               % (item["current"], item["target"], item["gain"]))
        if isinstance(note, str) and note:
            src["notes"] = note
        src["depth_headroom_at"] = "2026-10-05"
        changed += 1
    if changed:
        # 用 safe_dump 会丢掉 yaml 里的注释和字段顺序，而 sources.yaml 每条都带
        # 人工填的 auth_scope / terms_checked_at —— scripts/fix_sources.py:57 踩过。
        # 所以这里**不做整体重写**，而是逐行替换 archive_pages 的值。
        _patch_in_place(raw, plan_map)
    return changed


def _patch_in_place(raw: str, plan_map: dict[str, dict]) -> None:
    """原地按行改 archive_pages，保住注释和字段顺序。"""
    lines = raw.splitlines(keepends=True)
    current_id = None
    out = []
    pending: list[tuple[int, int]] = []      # (行号, 目标值)
    for i, line in enumerate(lines):
        m_id = None
        for key in ("- id:", "id:"):
            if key in line:
                m_id = line.split(":", 1)[1].strip().strip("\"'")
                break
        if m_id:
            current_id = m_id
        if current_id in plan_map and line.lstrip().startswith("archive_pages:"):
            target = plan_map[current_id]["target"]
            indent = line[:len(line) - len(line.lstrip())]
            out.append("%sarchive_pages: %d        # %d→%d 实测多 %s 条未入库\n"
                       % (indent, target, plan_map[current_id]["current"], target,
                          plan_map[current_id]["gain"]))
            continue
        out.append(line)
    SOURCES.write_text("".join(out), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report", nargs="?", default=str(DEFAULT_REPORT))
    ap.add_argument("--apply", action="store_true", help="真的写 sources.yaml")
    ap.add_argument("--min-gain", type=int, default=20,
                    help="少于这个数的增量不值得加深（要花的秒数不划算）")
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--max-seconds", type=float, default=200.0,
                    help="单源耗时上限；更深的档若产出不更多就放弃")
    args = ap.parse_args()

    report = load_report(Path(args.report))
    plan_map = plan(report, args.min_gain, args.max_pages, args.max_seconds)

    if not plan_map:
        print("没有源达到 min_gain=%d，不改配置。" % args.min_gain)
        return 0

    print("按实测增量拟调整 %d 个源：" % len(plan_map))
    print("=" * 74)
    print("%-18s %8s %8s %8s %9s" % ("source", "当前", "建议", "增量", "单源秒"))
    for sid, it in sorted(plan_map.items(), key=lambda kv: -kv[1]["gain"]):
        print("%-18s %8d %8d %8d %9.1f"
              % (sid, it["current"], it["target"], it["gain"], it["seconds"] or 0))
    total_gain = sum(i["gain"] for i in plan_map.values())
    print("-" * 74)
    print("合计潜在增量：%d 条" % total_gain)

    if not args.apply:
        print("\n预览模式。加 --apply 写入 %s" % SOURCES)
        return 0
    changed = apply_plan(plan_map)
    print("\n已写入 %d 个源 → %s" % (changed, SOURCES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
