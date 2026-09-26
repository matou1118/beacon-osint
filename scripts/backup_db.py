from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backup import backup_database


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a consistent SQLite backup")
    parser.add_argument("--db", default="data/beacon.db")
    parser.add_argument("--backup-dir", default="data/backups")
    parser.add_argument("--retention-days", type=int, default=30)
    args = parser.parse_args()
    print(json.dumps(backup_database(args.db, args.backup_dir, args.retention_days), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
