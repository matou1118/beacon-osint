from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backup import backup_database


class BackupTests(unittest.TestCase):
    def test_backup_uses_sqlite_backup_and_removes_expired_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "beacon.db"
            connection = sqlite3.connect(database)
            connection.execute("create table sample (value text)")
            connection.execute("insert into sample values ('ok')")
            connection.commit()
            connection.close()
            backup_dir = root / "backups"
            old = backup_dir / "beacon-20200101T000000000000Z.db"
            backup_dir.mkdir()
            old.write_bytes(b"old")
            import os

            # Anchor mtime to the same clock the call uses, otherwise the age
            # comparison is against a different day and the fixture drifts.
            now = datetime(2026, 9, 25, tzinfo=timezone.utc)
            old_time = (now - timedelta(days=31)).timestamp()
            os.utime(old, (old_time, old_time))
            result = backup_database(database, backup_dir, retention_days=30, now=now)
            self.assertTrue(result["created"])
            self.assertTrue(Path(str(result["path"])).exists())
            self.assertFalse(old.exists())
            check = sqlite3.connect(str(result["path"]))
            self.assertEqual(check.execute("select value from sample").fetchone()[0], "ok")
            check.close()


if __name__ == "__main__":
    unittest.main()
