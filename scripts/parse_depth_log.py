"""从测量**进度日志**解析出 JSON 报告，供 ``apply_depth_headroom.py`` 消费。

为什么改用进度行
----------------
第一版解析源标题行和 depth 明细行，解析出 0 个源。两个原因叠在一起：

1. 明细行**只在全部跑完时才打印**（它们在 ``main`` 末尾统一输出）。
   2026-10-05 跑了 69/276 就掐掉，那 69 个源的结果**一行都没落盘**。
2. 重定向到文件的 stdout 按**系统 ANSI 代码页**（本机 GBK）写，
   即使父进程设了 ``PYTHONUTF8=1`` 也不走 UTF-8。用 UTF-8 读满屏 ``??``。

所以改成只依赖**进度行**——它是 ``pool.map`` 每完成一个任务就 flush 的，
边跑边有，且格式只有三个字段：

    [8/276] cq_fgw 635 URL / 新 203

深度信息不在行里，但任务提交顺序是确定的（``for sid in ids: for d in depths``），
所以**每个源在日志里连续出现两次**，第一次 = depths[0]、第二次 = depths[1]。
配对即可还原出逐深度的结果，而且中断也不丢已测部分。

编码也一并处理：UTF-8 / GBK / mbcs 逐个试，用能解出中文的那个。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "runtime" / "lighthouse" / "measure_depth.log"
REPORT = ROOT / "data" / "runtime" / "lighthouse" / "depth_headroom.json"
DEFAULT_DEPTHS = [12, 30]

RE_PROG = re.compile(r"^\s*\[\d+/\d+\]\s+(\S+)\s+(\d+)\s+URL\s*/\s*"
                     r"(?:\u65b0\s+(\d+)|\u5931\u8d25:?(.*))$")


def _read_lines(path: Path) -> list[str]:
    raw = path.read_bytes()
    for enc in ("utf-8", "gbk", "mbcs"):
        try:
            lines = raw.decode(enc).splitlines()
        except (UnicodeDecodeError, LookupError):
            continue
        if any("\u65b0" in l or "URL" in l for l in lines):
            return lines
    return raw.decode("utf-8", errors="replace").splitlines()


def _current_depths() -> dict[str, int]:
    return _current_field("archive_pages")


def _current_field(key: str) -> dict[str, int]:
    import yaml

    data = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8")) or {}
    out: dict[str, int] = {}
    for s in data.get("sources") or []:
        sid = str(s.get("id") or "")
        if sid:
            out[sid] = int(s.get(key) or 0)
    return out


def parse(path: Path, depths: list[int] | None = None) -> dict[str, dict]:
    depths = depths or DEFAULT_DEPTHS
    if not path.exists():
        raise SystemExit("找不到测量日志 %s" % path)

    current = _current_depths()
    # max_items 也要记：`total == max_items` 就是「被条数上限截断」的信号，
    # 这是 2026-10-05 实测发现的第二道闸（js_gxt 800->999 URL、sd_rst 800->1003，
    # 只调 archive_pages 对它们完全无效）。不记这个字段就没法事后扫出被截断的源。
    caps = _current_field("max_items")
    out: dict[str, dict] = {}
    seen: dict[str, int] = {}

    for line in _read_lines(path):
        m = RE_PROG.match(line)
        if not m:
            continue
        sid, total, fresh, err = (m.group(1), int(m.group(2)),
                                  m.group(3), m.group(4))
        # 同一源在日志里连续出现 len(depths) 次，第 k 次对应 depths[k]
        slot = seen.get(sid, 0)
        seen[sid] = slot + 1
        if slot >= len(depths):
            continue
        row = out.setdefault(sid, {"current_archive_pages": current.get(sid),
                                   "max_items": caps.get(sid),
                                   "probes": []})
        depth = depths[slot]
        if err:
            row["probes"].append({"depth": depth, "error": err.strip()[:120]})
        else:
            row["probes"].append({"depth": depth, "total": total,
                                  "fresh": int(fresh or 0), "seconds": None})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(LOG))
    ap.add_argument("--report", default=str(REPORT))
    ap.add_argument("--depths", default="12,30")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min-gain", type=int, default=20)
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--max-seconds", type=float, default=200.0)
    args = ap.parse_args()

    depths = [int(d) for d in args.depths.split(",") if d.strip()]
    data = parse(Path(args.log), depths)

    print("从日志解析出 %d 个已测源（深度 %s）" % (len(data), depths))
    print("%-18s %6s  %s" % ("source", "当前", "逐深度：总URL/未入库"))
    for sid, v in sorted(data.items()):
        cells = "  ".join("d%d:%d/%d" % (p["depth"], p.get("total", 0), p.get("fresh", 0))
                          for p in v["probes"] if "error" not in p)
        print("%-18s %6s  %s" % (sid, v.get("current_archive_pages"), cells))

    payload = {"generated_at": datetime.now().isoformat(timespec="seconds"),
               "parsed_from_log": str(args.log), "depths": depths,
               "sources": data}
    Path(args.report).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print("\n报告已写入 %s" % args.report)

    if args.apply:
        sys.path.insert(0, str(ROOT / "scripts"))
        import apply_depth_headroom as adh

        plan_map = adh.plan(payload, args.min_gain, args.max_pages, args.max_seconds)
        if not plan_map:
            print("没有源达到 min_gain=%d，不改配置。" % args.min_gain)
            return 0
        print("\n拟调整 %d 个源：" % len(plan_map))
        print("%-18s %6s %6s %8s" % ("source", "当前", "建议", "增量"))
        for sid, it in sorted(plan_map.items(), key=lambda kv: -kv[1]["gain"]):
            print("%-18s %6d %6d %8d" % (sid, it["current"], it["target"], it["gain"]))
        print("\n合计潜在增量 %d 条" % sum(i["gain"] for i in plan_map.values()))
        changed = adh.apply_plan(plan_map)
        print("已写入 %d 个源" % changed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
