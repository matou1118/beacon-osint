"""给未启用源批量补选择器：探测 → 预览 diff → 应用。

为什么需要
  93 个闲置源里 36 个是 list_selector=None：页面完全可达，只是从来没配过选择器。
  手工一个个翻 HTML 找选择器既慢又不可复现，所以用 propose_source_config.py
  从页面结构反推，再在这里统一落盘。

三道闸（因为这是在改生产配置，不能一把梭）
  1. 只处理探测结果"可信"的：list_selector 非空、link_count >= 4、
     detail_ratio >= 0.3。达不到就跳过并记录原因，不硬写。
  2. 先出 diff 预览，--apply 才落盘。
  3. 落盘只改该源的键，不重排、不重写其他源。写完跑一次源体检复核。

用法
  python scripts/apply_source_fixes.py                # 只预览
  python scripts/apply_source_fixes.py --apply        # 落盘
  python scripts/apply_source_fixes.py --apply --enable   # 顺带启用探测通过的源
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

from propose_source_config import propose  # noqa: E402  - 同目录脚本

ROOT = Path(__file__).resolve().parents[1]

MIN_LINKS = 4
MIN_DETAIL_RATIO = 0.3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", default="blocked,pending_reprobe")
    parser.add_argument("--apply", action="store_true", help="真正写入 sources.yaml")
    parser.add_argument("--enable", action="store_true", help="把探测通过的源改为 enabled")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    import yaml

    path = ROOT / "config" / "sources.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    sources = data["sources"] if isinstance(data, dict) else data
    wanted = {s.strip() for s in args.status.split(",") if s.strip()}
    targets = [s for s in sources if s.get("status") in wanted
               and not s.get("list_selector")]
    if args.limit:
        targets = targets[:args.limit]

    print(f"待补选择器 {len(targets)} 个（无选择器的 blocked/pending_reprobe）\n", flush=True)
    patches: list[tuple[dict, dict]] = []
    skipped: list[tuple[str, str]] = []
    for index, source in enumerate(targets, 1):
        result = propose(source.get("url", ""), source)
        sid = source.get("id")
        reason = result.get("error") or ""
        good = (
            result.get("list_selector")
            and (result.get("link_count") or 0) >= MIN_LINKS
            and (result.get("detail_ratio") or 0) >= MIN_DETAIL_RATIO
        )
        if not good:
            if not reason:
                reason = (f"探测结果不可信（links={result.get('link_count')}, "
                          f"detail_ratio={result.get('detail_ratio')}）")
            skipped.append((sid, reason))
            print(f"[{index}/{len(targets)}] {sid:<18} 跳过   {reason[:66]}", flush=True)
            continue
        patch = {
            "list_selector": result["list_selector"],
            "link_selector": result["link_selector"],
            "detail_pattern": result["detail_pattern"],
        }
        patches.append((source, patch))
        print(f"[{index}/{len(targets)}] {sid:<18} 可用   {patch['list_selector']:<24} "
              f"links={result['link_count']} ratio={result['detail_ratio']}", flush=True)

    print("\n" + "=" * 66)
    print(f"可用 {len(patches)} 个，跳过 {len(skipped)} 个")
    for sid, reason in skipped[:12]:
        print(f"  跳过 {sid:<18} {reason[:80]}")
    if not patches:
        return 0

    print("\n--- 变更预览 ---")
    for source, patch in patches:
        print(f"  {source['id']}")
        for key, value in patch.items():
            if source.get(key) != value:
                print(f"    {key}: {source.get(key)!r} -> {value!r}")
    if not args.apply:
        print("\n（预览模式，未写入。加 --apply 落盘）")
        return 0

    touched = 0
    for source, patch in patches:
        changed = False
        for key, value in patch.items():
            if value and source.get(key) != value:
                source[key] = value
                changed = True
        if args.enable and changed:
            source["status"] = "enabled"
        if changed:
            touched += 1
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=200),
        encoding="utf-8",
    )
    print(f"\n已写入 {path}：{touched} 个源"
          f"{'（并置为 enabled）' if args.enable else ''}")
    report = {"patched": [s["id"] for s, _ in patches],
              "skipped": [{"id": i, "reason": r} for i, r in skipped]}
    (ROOT / "data" / "source_fixes.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
