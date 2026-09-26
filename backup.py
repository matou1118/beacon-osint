from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def backup_database(
    db_path: str | Path,
    backup_dir: str | Path,
    retention_days: int = 30,
    now: datetime | None = None,
) -> dict[str, object]:
    source_path = Path(db_path)
    if not source_path.exists():
        return {"created": False, "reason": "database_not_found", "path": None, "removed": 0}
    current = now or datetime.now(timezone.utc)
    directory = Path(backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"beacon-{current.strftime('%Y%m%dT%H%M%S%fZ')}.db"
    temporary = target.with_suffix(".db.tmp")
    source = sqlite3.connect(str(source_path))
    destination = sqlite3.connect(str(temporary))
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    temporary.replace(target)
    cutoff = current - timedelta(days=max(1, int(retention_days)))
    removed = 0
    for candidate in directory.glob("beacon-*.db"):
        if candidate == target:
            continue
        modified = datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc)
        if modified < cutoff:
            candidate.unlink()
            removed += 1
    return {"created": True, "path": str(target), "removed": removed, "retention_days": int(retention_days)}
