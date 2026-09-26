from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

_SECRET_PATTERN = re.compile(r"(?i)(cookie|authorization|set-cookie|token|password|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+")
_REDACTED = "[REDACTED]"


def scrub_text(value: str) -> str:
    return _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}={_REDACTED}", value)


def cleanup_runtime_artifacts(
    runtime_dirs: Iterable[str | Path],
    debug_log_files: Iterable[str | Path] = (),
    preserve_audit: bool = True,
) -> dict[str, int]:
    removed_files = 0
    removed_bytes = 0
    for raw_path in runtime_dirs:
        path = Path(raw_path)
        if not path.exists() or not path.is_dir() or path.resolve() == Path(path.anchor).resolve():
            continue
        for item in sorted(path.rglob("*"), reverse=True):
            if item.is_file():
                removed_bytes += item.stat().st_size
                item.unlink()
                removed_files += 1
            elif item.is_dir():
                item.rmdir()
        try:
            path.rmdir()
        except OSError:
            pass
    scrubbed_files = 0
    for raw_path in debug_log_files:
        path = Path(raw_path)
        if not path.exists() or not path.is_file() or (preserve_audit and "audit" in path.parts):
            continue
        original = path.read_text(encoding="utf-8", errors="replace")
        cleaned = scrub_text(original)
        if cleaned != original:
            path.write_text(cleaned, encoding="utf-8")
            scrubbed_files += 1
    return {"removed_files": removed_files, "removed_bytes": removed_bytes, "scrubbed_debug_logs": scrubbed_files}
