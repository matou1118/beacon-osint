"""放开被 ``max_items`` 卡住的天花板，并把这个约束固化进测量工具。

2026-10-05 实测发现的**第二道闸**
--------------------------------
``archive_pages`` 放开之后，``js_gxt`` / ``sd_rst`` 在 depth 12 和 30 都恰好
返回 **800** 条链接——这个整数太齐了，不像站点上限。查下来是我们的
``max_items``（``collectors/html_list.py:fetch_list`` 里 ``links[:cap]``）。

只调深度对这两个源完全无效，因为深度再深也被 ``max_items`` 截断。实测：

    js_gxt   max_items 800  →  800 URL，未入库 316
             max_items 5000 →  999 URL，未入库 515   （+199）
    sd_rst   max_items 800  →  800 URL，未入库  77
             max_items 5000 → 1003 URL，未入库 280   （+203）

两个源加起来 402 条真实新公告，白白卡在天花板上。

所以「调深翻页」这件事其实有**两个**独立约束，之前只解决了一个：
  * ``archive_pages`` —— 能翻多少页
  * ``max_items``     —— 一页里最多取多少条链接
任一先到上限，结果就被截断。只看 ``total == max_items`` 就知道是后者卡住了。

做法
----
只动**实测证明被截断**的源，不动其余的。写成脚本是为了留痕——
这两道闸的取值都会影响单轮耗时，事后要看当时凭什么调的。

用法：
    python scripts/lift_max_items.py --sources js_gxt,sd_rst --apply
    python scripts/lift_max_items.py --scan-measurements     # 从测量报告找被截断的源
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "config" / "sources.yaml"
REPORT = ROOT / "data" / "runtime" / "lighthouse" / "depth_headroom.json"

#: 实测证明「站点还能给出更多、只是被 max_items 截断」的源 -> 新的 max_items。
#: 依据见模块 docstring 的实测表。
LIFTED: dict[str, int] = {
    "js_gxt": 5000,   # 800 -> 999 URL，未入库 316 -> 515
    "sd_rst": 5000,   # 800 -> 1003 URL，未入库  77 -> 280
}


def _read_sources() -> str:
    return SOURCES.read_text(encoding="utf-8")


def find_truncated(report_path: Path) -> list[tuple[str, int, int]]:
    """从测量报告里找出 ``total == max_items`` 的源（即被 max_items 截断）。"""
    if not report_path.exists():
        return []
    data = json.loads(report_path.read_text(encoding="utf-8"))
    out: list[tuple[str, int, int]] = []
    for sid, row in (data.get("sources") or {}).items():
        cap = row.get("max_items")
        if not cap:
            continue
        for probe in row.get("probes") or []:
            if probe.get("total") == cap:
                out.append((sid, int(cap), int(probe.get("fresh") or 0)))
                break
    return out


def apply(sources: dict[str, int]) -> int:
    """原地按行改 ``max_items``，保住注释和字段顺序。

    刻意**不用** ``yaml.safe_dump`` 重写整个文件：sources.yaml 每条都带人工填的
    ``auth_scope`` / ``terms_checked_at`` / ``notes``，而 scripts/fix_sources.py:57
    曾经用 safe_dump 批量覆写把这些全丢了（那是有记录的事故）。
    """
    import re

    lines = _read_sources().splitlines(keepends=True)
    current = None
    changed = 0
    for i, line in enumerate(lines):
        m = re.search(r"^\s*-?\s*id:\s*[\"']?([\w.\-]+)", line)
        if m:
            current = m.group(1)
        if current in sources and re.match(r"^\s*max_items:\s*\d+", line):
            indent = line[:len(line) - len(line.lstrip())]
            new = int(sources[current])
            lines[i] = ("%smax_items: %d        # %s->%d，实测 total==max_items 被截断，"
                        "放开后多出未入库链接\n" % (indent, new, line.strip().split(":")[1].strip(), new))
            changed += 1
    if changed:
        # write_bytes 而不是 write_text：Windows 上 write_text 会做行尾转换，
        # 把原本的 CRLF 变成 LF（或者更糟，出现 \r\r\n），整个文件在 git 里全红。
        SOURCES.write_bytes("".join(lines).encode("utf-8"))
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="", help="逗号分隔的源 id")
    ap.add_argument("--max-items", type=int, default=5000)
    ap.add_argument("--scan-measurements", action="store_true",
                    help="从测量报告里找出被 max_items 截断的源")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if args.scan_measurements:
        rows = find_truncated(REPORT)
        if not rows:
            print("测量报告里没有 total==max_items 的源（或报告不存在）")
            print("报告路径：%s" % REPORT)
            return 0
        print("被 max_items 截断的源：")
        for sid, cap, fresh in rows:
            print("   %-18s max_items=%-5d 未入库 %d" % (sid, cap, fresh))
        return 0

    targets: dict[str, int] = dict(LIFTED)
    if args.sources:
        targets.update({s.strip(): args.max_items
                        for s in args.sources.split(",") if s.strip()})

    print("拟放开 max_items 的源：")
    for sid, val in sorted(targets.items()):
        print("   %-18s -> %d" % (sid, val))
    if not args.apply:
        print("\n预览模式。加 --apply 写入 %s" % SOURCES)
        return 0
    changed = apply(targets)
    print("\n已写入 %d 个源" % changed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())