"""Channels — configure_logging builds the documented handlers or fails loud."""

from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

import pytest

from fastplace.errors import ConfigurationError
from fastplace.logging.channels import (
    DEFAULT_FORMAT,
    DailyRotatingFileHandler,
    configure_logging,
    reset_logging_state,
)

_DATED = re.compile(r"fastplace-\d{4}-\d{2}-\d{2}\.log")


def test_default_channel_is_single_file_under_root(tmp_path: Path):
    configure_logging(root=tmp_path)
    file_handlers = [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)  # type: ignore[attr-defined]
    ]
    assert len(file_handlers) == 1
    handler = file_handlers[0]
    assert handler.baseFilename == str(tmp_path / "storage" / "logs" / "fastplace.log")
    # Documented defaults: 10MB x 5 backups.
    assert handler.maxBytes == 10 * 1024 * 1024
    assert handler.backupCount == 5
    # delay=True: the dir is created eagerly, the file only on first write.
    assert (tmp_path / "storage" / "logs").is_dir()
    assert not (tmp_path / "storage" / "logs" / "fastplace.log").exists()


def test_env_overrides_rotation_params(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOG_MAX_BYTES", "5000")
    monkeypatch.setenv("LOG_BACKUP_COUNT", "2")
    configure_logging(root=tmp_path)
    handler = next(
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)  # type: ignore[attr-defined]
    )
    assert handler.maxBytes == 5000
    assert handler.backupCount == 2


def test_daily_channel_builds_timed_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOG_CHANNEL", "daily")
    monkeypatch.setenv("LOG_DAILY_DAYS", "3")
    configure_logging(root=tmp_path)
    handler = next(
        h for h in logging.getLogger().handlers if isinstance(h, DailyRotatingFileHandler)
    )
    assert handler.baseFilename == str(tmp_path / "storage" / "logs" / "fastplace.log")
    assert handler.when == "MIDNIGHT" or handler.when.upper() == "MIDNIGHT"
    assert handler.backupCount == 3


def test_daily_namer_matches_documented_name(tmp_path: Path):
    handler = DailyRotatingFileHandler(tmp_path / "storage" / "logs" / "fastplace.log")
    rotated = handler.namer(str(tmp_path / "storage" / "logs" / "fastplace.log") + ".2026-09-28")
    assert rotated == str(tmp_path / "storage" / "logs" / "fastplace-2026-09-28.log")


def test_daily_retention_deletes_only_oldest_beyond_budget(tmp_path: Path):
    logs = tmp_path / "storage" / "logs"
    logs.mkdir(parents=True)
    for day in ("2026-09-25", "2026-09-26", "2026-09-27", "2026-09-28"):
        (logs / f"fastplace-{day}.log").write_text("x")
    handler = DailyRotatingFileHandler(logs / "fastplace.log", backup_count=2)
    doomed = handler.getFilesToDelete()
    assert [Path(p).name for p in doomed] == [
        "fastplace-2026-09-25.log",
        "fastplace-2026-09-26.log",
    ]


def test_stderr_and_null_channels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOG_CHANNEL", "stderr")
    configure_logging(root=tmp_path)
    ours = [h for h in logging.getLogger().handlers if getattr(h, "fastplace_logging", False)]
    assert len(ours) == 1
    assert isinstance(ours[0], logging.StreamHandler)
    assert not isinstance(ours[0], logging.FileHandler)

    monkeypatch.setenv("LOG_CHANNEL", "null")
    configure_logging(root=tmp_path, force=True)
    ours = [h for h in logging.getLogger().handlers if getattr(h, "fastplace_logging", False)]
    assert len(ours) == 1
    assert isinstance(ours[0], logging.NullHandler)


def test_stack_channel_composites_log_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOG_CHANNEL", "stack")
    monkeypatch.setenv("LOG_STACK", "single,daily,stderr")
    configure_logging(root=tmp_path)
    ours = [h for h in logging.getLogger().handlers if getattr(h, "fastplace_logging", False)]
    kinds = sorted(type(h).__name__ for h in ours)
    assert kinds == ["DailyRotatingFileHandler", "RotatingFileHandler", "StreamHandler"]


def test_unknown_channel_fails_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOG_CHANNEL", "syslog")
    with pytest.raises(ConfigurationError, match="syslog"):
        configure_logging(root=tmp_path)


@pytest.mark.parametrize(
    ("key", "channel"),
    [
        ("LOG_MAX_BYTES", "single"),
        ("LOG_BACKUP_COUNT", "single"),
        ("LOG_DAILY_DAYS", "daily"),
    ],
)
def test_numeric_settings_reject_junk_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, channel: str
):
    # Settings a channel never reads can't fail its boot — the junk test
    # runs under the channel that consumes the setting.
    monkeypatch.setenv("LOG_CHANNEL", channel)
    monkeypatch.setenv(key, "not-a-number")
    with pytest.raises(ConfigurationError, match=key):
        configure_logging(root=tmp_path)


def test_numeric_settings_reject_negative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOG_MAX_BYTES", "-1")
    with pytest.raises(ConfigurationError, match="LOG_MAX_BYTES"):
        configure_logging(root=tmp_path)


def test_stack_channel_rejects_unknown_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOG_CHANNEL", "stack")
    monkeypatch.setenv("LOG_STACK", "single,nope")
    with pytest.raises(ConfigurationError, match="nope"):
        configure_logging(root=tmp_path)


def test_stack_channel_rejects_duplicate_members(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOG_CHANNEL", "stack")
    monkeypatch.setenv("LOG_STACK", "single,single,stderr")
    with pytest.raises(ConfigurationError, match="duplicate"):
        configure_logging(root=tmp_path)


def test_configure_is_idempotent(tmp_path: Path):
    configure_logging(root=tmp_path)
    before = list(logging.getLogger().handlers)
    configure_logging(root=tmp_path)  # second boot in-process: no-op
    assert logging.getLogger().handlers == before


def test_force_replaces_only_our_handlers(tmp_path: Path):
    foreign = logging.NullHandler()
    logging.getLogger().addHandler(foreign)
    configure_logging(root=tmp_path, force=True)
    ours = [h for h in logging.getLogger().handlers if getattr(h, "fastplace_logging", False)]
    assert len(ours) == 1
    assert foreign in logging.getLogger().handlers  # untouched
    ours[0].close()


def test_level_applies_to_root_and_fastplace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    configure_logging(root=tmp_path)
    assert logging.getLogger().level == logging.WARNING
    assert logging.getLogger("fastplace").level == logging.WARNING


def test_invalid_level_fails_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    with pytest.raises(ConfigurationError, match="LOG_LEVEL"):
        configure_logging(root=tmp_path)


def test_record_lands_in_file_with_correlation_ids(tmp_path: Path):
    from fastplace.logging.context import job_context, request_context

    configure_logging(root=tmp_path)
    with request_context("rid-live-1"), job_context("job-live-1"):
        logging.getLogger("fastplace.test.file").info("hello from the file channel")
    log_file = tmp_path / "storage" / "logs" / "fastplace.log"
    content = log_file.read_text(encoding="utf-8")
    assert "hello from the file channel" in content
    assert "request_id=rid-live-1" in content
    assert "job_id=job-live-1" in content
    assert "fastplace.test.file" in content


def test_formatter_fills_empty_ids_for_foreign_records():
    from fastplace.logging.channels import CorrelationFormatter

    record = logging.LogRecord("third.party", logging.INFO, __file__, 1, "msg", None, None)
    formatted = CorrelationFormatter(DEFAULT_FORMAT).format(record)
    assert "request_id= job_id=" in formatted


def test_reset_removes_our_handlers_and_flag(tmp_path: Path):
    configure_logging(root=tmp_path)
    assert reset_logging_state() is True
    ours = [h for h in logging.getLogger().handlers if getattr(h, "fastplace_logging", False)]
    assert ours == []
    assert reset_logging_state() is False
