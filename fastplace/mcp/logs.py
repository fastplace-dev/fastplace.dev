"""Log-file readers backing ``read-log-entries`` and ``last-error``.

Ported from the reference design: chunked backward tailing (64 KB growing to
1 MB) so memory stays bounded on any file size, PSR-3-style timestamp entry
splitting so stack traces stay attached to their record, and JSON-lines
awareness. The framework's default format is
``2026-10-03 10:00:00 LEVEL name [request_id=… job_id=…] message`` — a
timestamp at line start with no brackets.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_CHUNK_START = 64 * 1024
_CHUNK_MAX = 1024 * 1024

# The framework's asctime format at line start marks a new entry.
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
_ENTRY_SPLIT_RE = re.compile(rf"(?={_TIMESTAMP_RE.pattern})")
_ERROR_ENTRY_RE = re.compile(rf"^{_TIMESTAMP_RE.pattern}\s+(ERROR|CRITICAL)\b")


def resolve_log_file(root: Path, channel: str | None = None) -> Path:
    """Resolve the active log file under ``root/storage/logs/``.

    ``single`` and unknown channels map to ``fastplace.log``; ``daily``
    prefers today's file, then the newest dated file, then today's path so a
    not-yet-created file still reports a sane target.
    """
    log_dir = root / "storage" / "logs"
    base = log_dir / "fastplace.log"

    if channel != "daily":
        return base

    import datetime

    today = datetime.date.today().isoformat()
    today_file = log_dir / f"fastplace-{today}.log"
    if today_file.exists():
        return today_file

    dated = sorted(log_dir.glob("fastplace-????-??-??.log"))
    return dated[-1] if dated else today_file


def _is_json_log_format(content: str) -> bool:
    first_line = content.split("\n", 1)[0].strip()
    if not first_line.startswith("{"):
        return False
    try:
        json.loads(first_line)
    except ValueError:
        return False
    return True


def is_error_entry(entry: str) -> bool:
    """True when the entry reports ERROR/CRITICAL, text or JSON."""
    stripped = entry.strip()
    if stripped.startswith("{"):
        try:
            decoded = json.loads(stripped)
        except ValueError:
            return False
        if not isinstance(decoded, dict):
            return False
        level = decoded.get("level") or decoded.get("level_name") or ""
        try:
            return str(level).upper() in ("ERROR", "CRITICAL") or int(level) >= 40
        except (TypeError, ValueError):
            return False

    return _ERROR_ENTRY_RE.match(stripped) is not None


def _scan_chunk(log_file: Path, chunk_size: int) -> list[str]:
    """Return complete entries from the last ``chunk_size`` bytes, oldest first."""
    try:
        size = log_file.stat().st_size
    except OSError:
        return []

    offset = max(size - chunk_size, 0)
    with log_file.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()

    # Align to a line boundary: discard the partial first line.
    if offset > 0:
        newline = data.find(b"\n")
        if newline == -1:
            return []
        data = data[newline + 1 :]

    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        content = data.decode("utf-8", errors="replace")

    if not content.strip():
        return []

    if _is_json_log_format(content):
        return [line for line in content.splitlines() if line.strip()]

    entries = [e for e in _ENTRY_SPLIT_RE.split(content) if e.strip()]

    # A chunk that starts mid-entry leaves a fragment first; drop it unless
    # it begins a proper timestamped record.
    if entries and offset > 0 and _TIMESTAMP_RE.match(entries[0]) is None:
        entries.pop(0)
    return entries


def _read_entries_growing(log_file: Path, want: int) -> list[str]:
    """Grow the tail window until ``want`` entries are visible or capped."""
    chunk_size = _CHUNK_START
    entries: list[str] = []
    while True:
        entries = _scan_chunk(log_file, chunk_size)
        if len(entries) >= want or chunk_size >= _CHUNK_MAX:
            return entries[-want:] if entries else []
        chunk_size *= 2


def read_last_entries(log_file: Path, count: int) -> list[str]:
    """Return the last ``count`` complete log entries, oldest first."""
    if count <= 0 or not log_file.exists():
        return []
    return _read_entries_growing(log_file, count)


def read_last_error_entry(log_file: Path) -> str | None:
    """Return the most recent ERROR/CRITICAL entry in the tail window."""
    if not log_file.exists():
        return None

    chunk_size = _CHUNK_START
    while True:
        entries = _scan_chunk(log_file, chunk_size)
        for entry in reversed(entries):
            if is_error_entry(entry):
                return entry.strip()
        if chunk_size >= _CHUNK_MAX:
            return None
        chunk_size *= 2
