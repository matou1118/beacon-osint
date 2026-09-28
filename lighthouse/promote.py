"""把复探成功的源从 blocked 改回 enabled。

为什么不用 yaml.safe_dump 覆写
------------------------------
``scripts/fix_sources.py:57`` 就是这么干的，代价是整个 sources.yaml 的注释、
人工填写的审核字段（auth_scope / terms_checked_at / notes）和排版全部丢失——
145 个源的人工登记信息一次写回就没了。所以这里做行级手术：只在目标源
自己的那一段里改 status 和 enabled 两个值，其余字节一个不动。

    python lighthouse/promote.py --ids sc_kjt,sc_gxt --apply     # 先看再写
    python lighthouse/promote.py --from-report                     # 用最新影子报告自动挑
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
SOURCES = _ROOT / "config" / "sources.yaml"
BACKUP_DIR = _ROOT / "data" / "runtime"

_ID_LINE = re.compile(r"^- id:\s*(\S+)\s*$")


def parse_blocks(text: str) -> list[tuple[str, int, int]]:
    """切成 (source_id, 起��行号, 结束行号)，行号 0-based，区间为 [start, end)。"""
    lines = text.splitlines(keepends=True)
    starts: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        match = _ID_LINE.match(line)
        if match:
            starts.append((match.group(1).strip("\"'"), index))
    blocks: list[tuple[str, int, int]] = []
    for order, (source_id, start) in enumerate(starts):
        end = starts[order + 1][1] if order + 1 < len(starts) else len(lines)
        blocks.append((source_id, start, end))
    return blocks


def recover(text: str, wanted: set[str]) -> tuple[str, list[dict[str, str]]]:
    lines = text.splitlines(keepends=True)
    changes: list[dict[str, str]] = []
    for source_id, start, end in parse_blocks(text):
        if source_id not in wanted:
            continue
        # 先确认当前状态，避免把 pending_reprobe 之类误标成「从 blocked 恢复」
        before = {"status": "", "enabled": ""}
        for index in range(start, end):
            body = lines[index]
            for key in ("status", "enabled"):
                found = re.match(rf"^  {key}:\s*(\S+)\s*$", body)
                if found:
                    before[key] = found.group(1)
        if before["status"] == "enabled" and before["enabled"] == "true":
            continue
        for index in range(start, end):
            body = lines[index]
            if re.match(r"^  status:\s*\S+\s*$", body):
                lines[index] = re.sub(r"^  status:.*$", "  status: enabled",
                                      body.rstrip("\r\n")) + body[len(body.rstrip("\r\n")):]
            elif re.match(r"^  enabled:\s*\S+\s*$", body):
                lines[index] = re.sub(r"^  enabled:.*$", "  enabled: true",
                                      body.rstrip("\r\n")) + body[len(body.rstrip("\r\n")):]
        changes.append({"source_id": source_id,
                        "from": f"status={before['status']}/enabled={before['enabled']}",
                        "to": "status=enabled/enabled=true"})
    return "".join(lines), changes


def ids_from_report(report: str, min_records: int) -> list[str]:
    """从影子报告里挑出「采到了东西」的源。"""
    data = json.loads(Path(report).read_text(encoding="utf-8"))
    return [s["source_id"] for s in data.get("sources", []) if s.get("records", 0) >= min_records]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把复探成功的源恢复为 enabled")
    parser.add_argument("--ids", default="", help="逗号分隔的源 id")
    parser.add_argument("--from-report", action="store_true", help="用最新影子报告自动挑")
    parser.add_argument("--min-records", type=int, default=1, help="报告里至少采到几条才算成功")
    parser.add_argument("--apply", action="store_true", help="真的写文件（默认只预览）")
    args = parser.parse_args(argv)

    wanted: list[str] = [s.strip() for s in args.ids.split(",") if s.strip()]
    if args.from_report:
        reports = sorted(glob.glob(str(_ROOT / "data" / "runtime" / "lighthouse" / "shadow-*.json")),
                         key=os.path.getmtime)
        if not reports:
            print("没有影子报告，先跑 --dry-run")
            return 1
        report = reports[-1]
        wanted = ids_from_report(report, args.min_records)
        print(f"用报告 {Path(report).name} 挑出 {len(wanted)} 个源：{wanted}")
    if not wanted:
        print("没有指定源 id，也没给 --from-report")
        return 1

    text = SOURCES.read_text(encoding="utf-8")
    updated, changes = recover(text, set(wanted))
    if not changes:
        print("没有需要改的源（可能已经是 enabled，或者 id 不存在）")
        return 0

    print(f"将修改 {len(changes)} 个源：")
    for change in changes:
        print(f"  {change['source_id']:<16} {change['from']}  ->  {change['to']}")

    if not args.apply:
        print("\n预览模式。要写入加 --apply")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = BACKUP_DIR / f"sources.yaml.{stamp}.bak"
    shutil.copy2(SOURCES, backup)
    SOURCES.write_text(updated, encoding="utf-8")
    print(f"\n已写入 {SOURCES.name}（{len(changes)} 个源），备份 {backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
