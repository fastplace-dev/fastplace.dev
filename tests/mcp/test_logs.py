"""Log-file tools: chunked backward tail and last-error scan.

Entries are split on fastplace's leading timestamp (``YYYY-MM-DD HH:MM:SS``);
JSON-lines logs are detected and kept one-entry-per-line. Errors mirror the
framework's ``ERROR``/``CRITICAL`` level names and Python level numbers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastplace.mcp.logs import (
    is_error_entry,
    read_last_entries,
    read_last_error_entry,
    resolve_log_file,
)

# ------------------------------------------------------------------ fixtures


@pytest.fixture()
def log_file(tmp_path: Path) -> Path:
    path = tmp_path / "fastplace.log"
    lines = [
        "2026-10-03 10:00:00 INFO app.started [request_id= job_id=] boot",
        "2026-10-03 10:00:05 DEBUG db [request_id=r1 job_id=] query",
        "2026-10-03 10:00:10 ERROR http [request_id=r2 job_id=] boom",
        "Traceback (most recent call last):",
        '  File "app.py", line 1',
        "ValueError: boom",
        "2026-10-03 10:00:20 INFO http [request_id=r3 job_id=] done",
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


# ------------------------------------------------------- resolve_log_file


def test_resolve_log_file_single_channel(tmp_path: Path) -> None:
    assert (
        resolve_log_file(tmp_path, channel="single")
        == tmp_path / "storage" / "logs" / "fastplace.log"
    )


def test_resolve_log_file_daily_prefers_today(tmp_path: Path) -> None:
    log_dir = tmp_path / "storage" / "logs"
    log_dir.mkdir(parents=True)
    today = resolve_log_file(tmp_path, channel="daily").name
    assert today.startswith("fastplace-")

    dated = log_dir / f"fastplace-{today[len('fastplace-') :]}"
    dated.write_text("")
    assert resolve_log_file(tmp_path, channel="daily") == dated


def test_resolve_log_file_daily_falls_back_to_latest(tmp_path: Path) -> None:
    log_dir = tmp_path / "storage" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "fastplace-2026-09-01.log").write_text("")
    (log_dir / "fastplace-2026-09-15.log").write_text("")

    assert resolve_log_file(tmp_path, channel="daily") == log_dir / "fastplace-2026-09-15.log"


def test_resolve_log_file_unknown_channel_falls_back(tmp_path: Path) -> None:
    assert resolve_log_file(tmp_path, channel="stderr").name == "fastplace.log"


# -------------------------------------------------------- entry splitting


def test_read_last_entries_respects_count(log_file: Path) -> None:
    entries = read_last_entries(log_file, 2)

    assert len(entries) == 2
    assert entries[-1].startswith("2026-10-03 10:00:20 INFO")
    assert entries[0].startswith("2026-10-03 10:00:10 ERROR")


def test_multiline_entry_stays_together(log_file: Path) -> None:
    entries = read_last_entries(log_file, 100)

    error_entry = next(e for e in entries if "boom" in e)
    assert "Traceback" in error_entry
    assert "ValueError: boom" in error_entry


def test_partial_leading_entry_dropped(log_file: Path) -> None:
    # First timestamp line is the chunk-boundary victim; the stack-trace
    # lines of a broken entry must not leak in as their own entry.
    entries = read_last_entries(log_file, 3)

    assert all(e[4:5] == "-" and e[:4].isdigit() for e in entries)


def test_chunked_reading_on_large_file(tmp_path: Path) -> None:
    path = tmp_path / "big.log"
    # 3000 one-line entries ≈ 200 KB — forces more than one 64 KB chunk pass.
    lines = [
        f"2026-10-03 {i // 60:02d}:{i % 60:02d}:00 INFO app [request_id= job_id=] line {i}"
        for i in range(3000)
    ]
    path.write_text("\n".join(lines) + "\n")

    entries = read_last_entries(path, 5)

    assert len(entries) == 5
    assert "line 2999" in entries[-1]
    assert "line 2995" in entries[0]


def test_empty_and_missing_files(tmp_path: Path) -> None:
    assert read_last_entries(tmp_path / "missing.log", 10) == []

    empty = tmp_path / "empty.log"
    empty.write_text("")
    assert read_last_entries(empty, 10) == []


# --------------------------------------------------------------- errors


def test_is_error_entry_text_format() -> None:
    assert is_error_entry("2026-10-03 10:00:10 ERROR http [request_id= job_id=] boom") is True
    assert is_error_entry("2026-10-03 10:00:10 CRITICAL http boom") is True
    assert is_error_entry("2026-10-03 10:00:10 WARNING http meh") is False
    assert is_error_entry("Traceback (most recent call last):") is False


def test_is_error_entry_json_format() -> None:
    assert is_error_entry(json.dumps({"level": "ERROR", "message": "x"})) is True
    assert is_error_entry(json.dumps({"level_name": "CRITICAL", "message": "x"})) is True
    assert is_error_entry(json.dumps({"level": 45, "message": "x"})) is True
    assert is_error_entry(json.dumps({"level": "INFO", "message": "x"})) is False
    assert is_error_entry("{not json") is False


def test_read_last_error_entry_finds_newest(log_file: Path) -> None:
    found = read_last_error_entry(log_file)

    assert found is not None
    assert found.startswith("2026-10-03 10:00:10 ERROR")
    assert "Traceback" in found


def test_read_last_error_entry_none_when_clean(tmp_path: Path) -> None:
    path = tmp_path / "clean.log"
    path.write_text("2026-10-03 10:00:00 INFO app [request_id= job_id=] fine\n")

    assert read_last_error_entry(path) is None


def test_read_last_error_entry_json_log(tmp_path: Path) -> None:
    path = tmp_path / "json.log"
    records = [
        json.dumps({"level": "INFO", "message": "fine"}),
        json.dumps({"level": 40, "message": "broken", "exc_info": "Traceback…"}),
        json.dumps({"level": "INFO", "message": "later"}),
    ]
    path.write_text("\n".join(records) + "\n")

    found = read_last_error_entry(path)

    assert found is not None
    assert json.loads(found)["message"] == "broken"
