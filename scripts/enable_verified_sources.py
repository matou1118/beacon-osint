"""按新鲜探测证据，把实际能采的源重新启用。

为什么需要
  config/sources.yaml 里有 27 个源的 notes 写着
  "It looks like you are using Playwright Sync API inside the asyncio loop"，
  于是它们 status=blocked / enabled=False。那是**当时的瞬时故障**，不是站点拦我们。
  2026-09-29 重测：重庆 7 个源里 5 个 reachable，正文 495~18866 字，采集完全正常 ——
  等于白扔了五个重点地区的源。

  scripts/apply_source_fixes.py 救不了这批：它只在「补上 list_selector」时才 --enable，
  而这 5 个源选择器本来就没问题，只是被误禁用了。

判据（保守）
  - 必须重测出 reason == reachable，且 found >= MIN_FOUND（默认 4）
  - robots 拒绝、HTTP 412/5xx、正文过短、只抽到栏目页 —— 一律不启用
    （AGENTS.md §3.2：robots 拒绝不绕过）
  - 只动 status/enabled 两个字段，notes 里追加本次证据，保留原始原因
  - 先备份 config/sources.yaml；--apply 才落盘，默认预览

用法
  python scripts/enable_verified_sources.py --ids cq_gov,cq_fgw,cq_kjt,cq_ny,cq_rst
  python scripts/enable_verified_sources.py --ids cq_gov --apply
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "config" / "sources.yaml"
EVIDENCE = ROOT / "data" / "source_audit.json"
MIN_FOUND = 4


def _load_audit():
    spec = importlib.util.spec_from_file_location(
        "_audit_sources", ROOT / "scripts" / "audit_sources.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids", required=True, help="逗号分隔的源 id")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--min-found", type=int, default=MIN_FOUND)
    args = parser.parse_args()

    wanted = [s.strip() for s in args.ids.split(",") if s.strip()]
    if not wanted:
        print("没有指定源 id")
        return 1

    data = yaml.safe_load(SOURCES.read_text(encoding="utf-8"))
    sources = data["sources"] if isinstance(data, dict) else data
    by_id = {str(s.get("id")): s for s in sources}
    missing = [i for i in wanted if i not in by_id]
    if missing:
        print(f"不在注册表：{missing}")
        return 1

    audit = _load_audit()
    print(f"重测 {len(wanted)} 个源（robots 检查 + 3s 限速，串行，需数分钟）\n", flush=True)

    results = []
    for index, sid in enumerate(wanted, 1):
        out = audit.classify(dict(by_id[sid]))
        out["id"] = sid
        print(f"[{index}/{len(wanted)}] {sid:<18} {out['reason']:<16} "
              f"{out['detail'][:78]}", flush=True)
        results.append(out)

    enable = [r for r in results
              if r["reason"] == "reachable" and int(r.get("found") or 0) >= args.min_found]
    keep_off = [r for r in results if r not in enable]
    print("\n" + "=" * 66)
    print(f"可启用 {len(enable)} 个：{[r['id'] for r in enable] or '无'}")
    for r in keep_off:
        print(f"  保持禁用 {r['id']:<16} {r['reason']}: {r['detail'][:64]}")

    if not enable:
        print("\n没有可启用的源，不写盘。")
        return 0

    print("\n--- 变更预览 ---")
    for r in enable:
        src = by_id[r["id"]]
        print(f"  {r['id']:<16} status={src.get('status')!r} -> 'enabled'   "
              f"enabled={src.get('enabled')!r} -> True")

    if not args.apply:
        print("\n（预览模式，未写入。加 --apply 落盘）")
        return 0

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    backup = SOURCES.with_suffix(f".yaml.bak-{datetime.now():%Y%m%dT%H%M%S}")
    shutil.copy2(SOURCES, backup)
    print(f"\n已备份 {backup.name}")

    for r in enable:
        src = by_id[r["id"]]
        # status 和 enabled 是同一概念的两个字段，必须一起改：只改 status 会留下
        # status=enabled / enabled=False 的矛盾态，采集器按 enabled 过滤，
        # 配置看上去"已启用"但一个都没真跑。
        src["status"] = "enabled"
        src["enabled"] = True
        # 保留原始失败原因，把新证据追加进去，别覆盖掉排查历史。
        old = str(src.get("notes") or "").strip()
        src["notes"] = (old + f" | {stamp} 重测 reachable（{r['detail'][:60]}），"
                              f"此前 blocked 源于 Playwright Sync API 瞬时故障，非站点限制").strip()

    SOURCES.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=200),
        encoding="utf-8")

    evidence = []
    if EVIDENCE.exists():
        try:
            evidence = json.loads(EVIDENCE.read_text(encoding="utf-8")) or []
        except ValueError:
            evidence = []
    for r in results:
        evidence.append({"at": stamp, **r})
    EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"已启用 {len(enable)} 个源，证据写入 {EVIDENCE.name}（共 {len(evidence)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
