"""Channels — the logging bootstrap behind ``configure_logging()``.

Channels are small, stdlib-only handler recipes selected by ``LOG_CHANNEL``;
handlers are attached to the *root* logger so application and library lines
alike land in the configured destination (uvicorn's own ``uvicorn.*``
loggers keep their private handlers and stay on stderr — they do not
propagate, by uvicorn's design). Everything we install is tagged and
removable: reconfiguration replaces only fastplace-owned handlers, never
ones a host process or test harness attached.

Two deliberate choices worth restating:

* **Idempotent by flag, replaceable by tag.** The kernel boots once per
  process, but tests and embedded hosts boot repeatedly — a plain flag
  makes repeat calls no-ops, and ``force=True`` + the handler tag makes
  reconfiguration surgical instead of ``basicConfig(force=True)``'s
  wipe-the-world.
* **The daily channel owns its retention.** A stock ``TimedRotatingFileHandler``
  renames to ``fastplace.log.2026-09-28`` and scans for that prefix when
  pruning — a custom rename would orphan every rotated file past the
  retention budget. :class:`DailyRotatingFileHandler` restates both the name
  (``fastplace-YYYY-MM-DD.log``, what the docs show) and the pruning scan,
  so the documented ``LOG_DAILY_DAYS`` retention actually deletes.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path

from fastplace.errors import ConfigurationError
from fastplace.logging.context import CorrelationFilter

#: The one production format — correlation ids inline and always present
#: (empty when a record originates outside any request/job), so log lines
#: are grep-able and the layout never varies by origin.
DEFAULT_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s job_id=%(job_id)s] %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_CHANNEL_NAMES = ("single", "daily", "stderr", "null", "stack")

_configured = False


class CorrelationFormatter(logging.Formatter):
    """The default format, tolerant of records that skipped the filter.

    Third-party records can reach a fastplace handler through propagation
    paths we do not configure; a plain Formatter would raise on the missing
    fields and turn a log line into a traceback. Missing ids format as
    empty, same as an out-of-context record.
    """

    def format(self, record: logging.LogRecord) -> str:
        for field in ("request_id", "job_id"):
            if not hasattr(record, field):
                setattr(record, field, "")
        return super().format(record)


class DailyRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """Midnight rotation under the documented ``base-YYYY-MM-DD.log`` name.

    The stock namer produces ``base.log.2026-09-28`` and its retention scan
    matches only that prefix — so the rename has to come with a matching
    scan, or ``backup_count`` silently deletes nothing.
    """

    _DATED = re.compile(r"\d{4}-\d{2}-\d{2}\.log")

    def __init__(
        self,
        filename: str | os.PathLike[str],
        *,
        backup_count: int = 7,
        encoding: str = "utf-8",
    ) -> None:
        super().__init__(
            filename,
            when="midnight",
            backupCount=backup_count,
            encoding=encoding,
            delay=True,
        )
        self.suffix = "%Y-%m-%d"
        # Instance attribute (the base initializes this slot to None).
        self.namer = self._rotated_name

    def _rotated_name(self, default_name: str) -> str:
        """``fastplace.log.2026-09-28`` → ``fastplace-2026-09-28.log``."""
        stem, _, date = default_name.rpartition(".log.")
        return f"{stem}-{date}.log" if date else default_name

    def getFilesToDelete(self) -> list[str]:  # noqa: N802 — stdlib contract
        """Prune by the renamed pattern; oldest dated file deletes first."""
        dir_name, base_name = os.path.split(self.baseFilename)
        stem = base_name.removesuffix(".log")
        prefix = f"{stem}-"
        dated = sorted(
            name
            for name in os.listdir(dir_name)
            if name.startswith(prefix) and self._DATED.fullmatch(name.removeprefix(prefix))
        )
        overflow = len(dated) - self.backupCount
        if overflow <= 0:
            return []
        return [os.path.join(dir_name, name) for name in dated[:overflow]]


def _int_setting(key: str, default: int) -> int:
    """Coerce a numeric ``LOG_*`` setting, mapping junk to a config error.

    A raw ``ValueError`` from ``int()`` would surface as an unrelated
    traceback at boot; the config contract here is the same loud-but-named
    failure the channel names get.
    """
    from fastplace.config import config

    raw = config(key, default=default)
    try:
        value = int(raw)  # env arrives as a string; defaults are already ints
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{key}={raw!r} must be an integer") from exc
    if value < 0:
        raise ConfigurationError(f"{key}={value} must be >= 0")
    return value


def _level_from_config() -> int:
    from fastplace.config import config

    raw = str(config("LOG_LEVEL", default="INFO")).upper()
    level = logging.getLevelName(raw)
    if not isinstance(level, int):
        raise ConfigurationError(
            f"LOG_LEVEL={raw!r} is not a known level — use DEBUG, INFO, WARNING, ERROR or CRITICAL"
        )
    return level


def _build_handler(channel: str, log_dir: Path) -> logging.Handler:
    if channel == "single":
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            log_dir / "fastplace.log",
            maxBytes=_int_setting("LOG_MAX_BYTES", 10 * 1024 * 1024),
            backupCount=_int_setting("LOG_BACKUP_COUNT", 5),
            encoding="utf-8",
            delay=True,
        )
    elif channel == "daily":
        handler = DailyRotatingFileHandler(
            log_dir / "fastplace.log",
            backup_count=_int_setting("LOG_DAILY_DAYS", 7),
        )
    elif channel == "stderr":
        handler = logging.StreamHandler(sys.stderr)
    elif channel == "null":
        handler = logging.NullHandler()
    else:  # "stack" is resolved by the caller; anything else is config error
        raise ConfigurationError(
            f"LOG_CHANNEL={channel!r} is not a channel — "
            f"expected one of: {', '.join(_CHANNEL_NAMES)}"
        )
    return handler


def _resolve_channels() -> list[str]:
    from fastplace.config import config

    channel = str(config("LOG_CHANNEL", default="single"))
    if channel == "stack":
        members = [
            name.strip()
            for name in str(config("LOG_STACK", default="single,daily,stderr")).split(",")
        ]
        stack = [name for name in members if name]
        if not stack:
            raise ConfigurationError("LOG_STACK is empty — the stack channel needs members")
        for name in stack:
            if name not in ("single", "daily", "stderr", "null"):
                raise ConfigurationError(
                    f"LOG_STACK member {name!r} is not a channel — "
                    "expected single, daily, stderr or null"
                )
        if len(set(stack)) != len(stack):
            raise ConfigurationError(
                f"LOG_STACK has duplicate members: {', '.join(stack)} — "
                "a repeated channel writes every line twice"
            )
        return stack
    if channel not in _CHANNEL_NAMES:
        raise ConfigurationError(
            f"LOG_CHANNEL={channel!r} is not a channel — "
            f"expected one of: {', '.join(_CHANNEL_NAMES)}"
        )
    return [channel]


def configure_logging(*, root: str | Path | None = None, force: bool = False) -> None:
    """Install the configured channel(s) on the root logger — idempotent.

    The kernel calls this once at boot with the project root; a process that
    already configured logging (a second ``create_app``, an embedded host)
    is a no-op. ``force=True`` re-reads config and swaps the fastplace-owned
    handlers — the escape hatch tests and runtime log-level switches need.

    ``root`` is the project directory holding ``storage/logs/``; it defaults
    to the working directory. Handler params come from ``LOG_*`` config
    (env vars always win, per the standard config precedence).
    """
    global _configured
    if _configured and not force:
        return

    log_dir = (Path(root) if root else Path.cwd()) / "storage" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigurationError(f"cannot create log directory {log_dir}: {exc}") from exc

    level = _level_from_config()
    channels = _resolve_channels()

    formatter = CorrelationFormatter(DEFAULT_FORMAT, datefmt=_DATE_FORMAT)
    handlers = [_build_handler(name, log_dir) for name in channels]
    for handler in handlers:
        handler.setLevel(level)
        handler.setFormatter(formatter)
        handler.addFilter(CorrelationFilter())
        # Removal tag: reconfiguration deletes only fastplace-owned handlers.
        handler.fastplace_logging = True  # type: ignore[attr-defined]

    root_logger = logging.getLogger()
    root_logger.handlers[:] = [
        h for h in root_logger.handlers if not getattr(h, "fastplace_logging", False)
    ]
    for handler in handlers:
        root_logger.addHandler(handler)
    root_logger.setLevel(level)
    logging.getLogger("fastplace").setLevel(level)
    _configured = True


def reset_logging_state() -> bool:
    """Detach fastplace-owned handlers and clear the configured flag.

    Returns whether a configuration was live. Test isolation and embedded
    hosts use this — production code never does.
    """
    global _configured
    root_logger = logging.getLogger()
    ours = [h for h in root_logger.handlers if getattr(h, "fastplace_logging", False)]
    for handler in ours:
        root_logger.removeHandler(handler)
        handler.close()
    was = _configured
    _configured = False
    return was
