"""打包一次源码快照 + 运行元数据。

不碰 data/ 下的生成物（markdown 1300+ 个文件、影子报告、日志），
只打包能重建系统的那部分：工具代码、配置、测试、状态元数据。
数据库单独用 SQLite 在线备份 API 处理（运行中直接复制文件会撕出坏库）。

    python lighthouse/backup.py
    python lighthouse/backup.py --note "修完翻页深度后"
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sqlite3
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "data" / "runtime" / "lighthouse"
BACKUPS = ROOT / "data" / "backups"

# 能重建整个采集系统的文件
SOURCES = [
    "lighthouse",                       # 工具本体
    "lighthouse_view.py",               # 工作台视图
    "app.py",                           # 导航接线
    "config/sources.yaml",
    "config/web_collector_config.yaml",
    "config/rules.yaml",
    "config/scheduler.yaml",
    "tests",
    "requirements.txt",
    "AGENTS.md",
    "README.md",
]


def backup_db() -> Path:
    """用 SQLite 在线备份 API。调度器正在写的时候直接 shutil.copy 会撕坏库。"""
    src_path = ROOT / "data" / "beacon.db"
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out = BACKUPS / f"beacon-{stamp}.db"
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(out))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    check = sqlite3.connect(str(out))
    try:
        rows = check.execute("select count(*) from announcements").fetchone()[0]
        ok = check.execute("pragma integrity_check").fetchone()[0]
    finally:
        check.close()
    print(f"  数据库 {out.name}  {rows} 条  完整性 {ok}")
    return out


def backup_sources(stamp: str, note: str) -> Path | None:
    out = BACKUPS / f"lighthouse-src-{stamp}.zip"
    present = [p for p in SOURCES if (ROOT / p).exists()]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for item in present:
            path = ROOT / item
            if path.is_file():
                zf.write(path, item)
                continue
            for child in sorted(path.rglob("*")):
                if not child.is_file():
                    continue
                # 不打 __pycache__ 和测试缓存
                if "__pycache__" in child.parts or child.suffix == ".pyc":
                    continue
                zf.write(child, str(child.relative_to(ROOT)).replace("\\", "/"))
    print(f"  源码   {out.name}  {out.stat().st_size/1024:.0f} KB  {len(present)} 项")
    return out


def backup_meta(stamp: str, note: str) -> Path:
    status = None
    if (RUNTIME / "status.json").exists():
        try:
            status = json.loads((RUNTIME / "status.json").read_text(encoding="utf-8"))
        except ValueError:
            status = {"error": "status.json 解析失败"}
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                            cwd=str(ROOT), capture_output=True, text=True).stdout.strip()
    tags = subprocess.run(["git", "tag", "--points-at", "HEAD"],
                          cwd=str(ROOT), capture_output=True, text=True).stdout.split()
    records = 0
    try:
        con = sqlite3.connect("file:data/beacon.db?mode=ro", uri=True)
        records = con.execute("select count(*) from announcements").fetchone()[0]
        con.close()
    except Exception:                                          # noqa: BLE001
        pass
    out = BACKUPS / f"snapshot-{stamp}.json"
    out.write_text(json.dumps({
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "git_commit": commit,
        "git_tags": tags,
        "announcements": records,
        "status": status,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  元数据 {out.name}  commit={commit} tags={tags} 库={records}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="灯塔源码与数据备份")
    parser.add_argument("--note", default="", help="这一版改了什么")
    args = parser.parse_args(argv)

    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    print(f"备份开始 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
          f"{'  ' + args.note if args.note else ''}")
    backup_db()
    backup_sources(stamp, args.note)
    backup_meta(stamp, args.note)
    print(f"备份完成，目录 {BACKUPS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
