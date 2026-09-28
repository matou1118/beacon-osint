"""旁路 JSONL → announcements 表。业务流的最后一环。

为什么需要这一步
----------------
``data/markdown/`` 是**单向导出**：``main.py`` 写库时顺手导一份 md 给人看，
没有任何程序把 md 读回数据库。所以「输出完整的 md，项目可以提取使用」这句话
当时只对了一半——项目消费的是库，不是 md。lighthouse 采完写完 md 就停，
工作台、对比、API 全都看不到新数据。

为什么不从 markdown 反解析
--------------------------
markdown 是给人看的排版（标题、`## 规则摘要`、字段表格）。拿它当机器契约，
改一次模板就断一次。所以采集时另外落一份 JSONL（``collected-<ts>.jsonl``），
字段全留、格式稳定，这里直接读它。markdown 继续只当交付物。

去重
----
``storage/db.py:143 upsert_announcement`` 本身就按 ``(source_id, source_url)``
做 upsert，所以重跑同一个 jsonl 不会产生重复行——可以放心反复执行。

    python lighthouse/ingest.py                          # 吃最新一份 collected-*.jsonl
    python lighthouse/ingest.py path/to/collected-x.jsonl
    python lighthouse/ingest.py --dry-run               # 只说会写多少，不写
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterator

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DB = _ROOT / "data" / "beacon.db"
JSONL_GLOB = str(_ROOT / "data" / "runtime" / "lighthouse" / "collected-*.jsonl")


def log(message: str) -> None:
    print(message, flush=True)


def _mtime(path: str) -> float:
    import os

    return os.path.getmtime(path)


def latest_jsonl() -> str | None:
    files = glob.glob(JSONL_GLOB)
    return max(files, key=_mtime) if files else None


def read_records(path: str) -> Iterator[dict[str, Any]]:
    """逐行读。JSONL 的好处是内存恒定，几万条也不会把进程撑爆。"""
    with open(path, "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError as exc:
                # 单行坏掉不能毁掉整批。记下来继续，最后一起报。
                log(f"  第 {number} 行 JSON 解析失败，已跳过：{exc}")


def ingest(path: str | None = None, *, db_path: str = str(DB), batch: int = 50,
           dry_run: bool = False) -> int:
    target = path or latest_jsonl()
    if not target or not Path(target).exists():
        log("没有可入库的 collected-*.jsonl（先跑 python lighthouse/run.py，且不要 --dry-run）")
        return 1

    from storage.db import Database

    total = inserted = updated = failed = 0
    started = time.time()
    db = Database(db_path)
    before = db.count_announcements()

    log(f"入库 {Path(target).name} → {Path(db_path).name}（当前 {before} 条）")
    for record in read_records(target):
        total += 1
        if dry_run:
            continue
        try:
            db.upsert_announcement(record)
            inserted += 1
        except Exception as exc:                        # noqa: BLE001
            failed += 1
            log(f"  失败 {record.get('source_url', '?')[:80]}: {type(exc).__name__}: {exc}")

    after = db.count_announcements()
    if dry_run:
        log(f"[dry-run] 待写入 {total} 条，当前 {before} 条，预计写入后 {before + total} 条")
        return 0

    log(f"处理 {total} 条 | 成功 {inserted} | 失败 {failed} | "
        f"库 {before} → {after}（净增 {after - before}） | {round(time.time()-started, 1)}s")
    if after - before < inserted:
        # 差值 = 已被 upsert 判为同一条的，正常现象（重跑同一批就是这样）。
        # 不是错误，但要说清楚，否则「成功 267 条但库只多了 3 条」看着像 bug。
        log(f"（{inserted - (after - before)} 条命中已有记录，是重复采集，upsert 已跳过）")
    return 1 if failed and not inserted else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 lighthouse 采到的数据写进 announcements")
    parser.add_argument("path", nargs="?", default=None,
                        help="collected-*.jsonl，省略则取最新一份")
    parser.add_argument("--db", default=str(DB))
    parser.add_argument("--batch", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true", help="只报数量，不写库")
    args = parser.parse_args(argv)
    return ingest(args.path, db_path=args.db, batch=args.batch, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
