"""App-plane mail CLI — mail:outbox, mail:preview, mail:clear, mail:resend."""

from __future__ import annotations

import json
import os
import re

import pytest
from _isolation import isolate_project_state  # noqa: F401  (autouse: db + app.* isolation)
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
runner = CliRunner()


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    mail:outbox bootstraps config via load_env(), and python-dotenv writes the
    cwd .env's keys straight into the REAL os.environ — a mutation no
    monkeypatch sees or undoes. Snapshot before, restore after: identical
    pattern to tests/cli/test_cache_cmds.py.
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture()
def outbox_log(tmp_path, monkeypatch):
    """A cwd with storage/logs/; returns the mail.log path (not yet created)."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    log = tmp_path / "storage" / "logs" / "mail.log"
    log.parent.mkdir(parents=True)
    return log


def _line(subject: str, to: str = "user@example.com", **extra) -> str:
    payload = {
        "subject": subject,
        "text": "body",
        "to": to,
        "html": None,
        "from_address": "fastplace@localhost",
        "from_name": None,
        **extra,
    }
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# mail:outbox
# ---------------------------------------------------------------------------


def test_outbox_lists_messages_with_true_line_numbers(outbox_log):
    outbox_log.write_text(_line("first") + "\n" + _line("second") + "\n" + _line("third") + "\n")

    result = runner.invoke(cli_app, ["mail:outbox"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "Mail outbox" in plain
    for word in ("first", "second", "third"):
        assert word in plain
    assert "2" in plain  # the middle row's file line number is shown


def test_outbox_filters_on_to_and_subject_substrings(outbox_log):
    outbox_log.write_text(
        _line("welcome", to="ada@example.com")
        + "\n"
        + _line("receipt", to="grace@example.com")
        + "\n"
    )

    result = runner.invoke(cli_app, ["mail:outbox", "--to", "ada", "--subject", "welc"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "welcome" in plain
    assert "receipt" not in plain


def test_outbox_lines_bounds_window_to_last_n_matches(outbox_log):
    outbox_log.write_text("\n".join(_line(f"msg-{i}") for i in range(1, 6)) + "\n")

    result = runner.invoke(cli_app, ["mail:outbox", "--lines", "2"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "msg-5" in plain and "msg-4" in plain  # the LAST two matches
    assert "msg-1" not in plain and "msg-2" not in plain and "msg-3" not in plain


def test_outbox_lines_zero_empties_window(outbox_log):
    """--lines 0 must print nothing — an empty window, never the whole log."""
    outbox_log.write_text(_line("one") + "\n" + _line("two") + "\n")

    result = runner.invoke(cli_app, ["mail:outbox", "--lines", "0"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "one" not in plain and "two" not in plain
    assert "no matching messages" in plain


def test_outbox_json_prints_parsed_array_with_line_numbers(outbox_log):
    outbox_log.write_text(_line("first") + "\n" + _line("second") + "\n")

    result = runner.invoke(cli_app, ["mail:outbox", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(ANSI_RE.sub("", result.output))
    assert [entry["line"] for entry in payload] == [1, 2]
    assert payload[0]["subject"] == "first"


def test_outbox_skips_malformed_lines_with_a_count(outbox_log):
    outbox_log.write_text(_line("good") + "\n" + "{not json}\n" + _line("also-good") + "\n")

    result = runner.invoke(cli_app, ["mail:outbox"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "good" in plain
    assert "skipped 1 malformed" in plain


def test_outbox_missing_log_is_dim_exit_zero(outbox_log):
    result = runner.invoke(cli_app, ["mail:outbox"])

    assert result.exit_code == 0, result.output
    assert "no mail log" in ANSI_RE.sub("", result.output)


# ---------------------------------------------------------------------------
# mail:preview
# ---------------------------------------------------------------------------


def test_preview_applies_default_from_address(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["mail:preview"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "fastplace@localhost" in plain  # MAIL_FROM_ADDRESS default
    for field in ("to", "subject", "from"):
        assert field in plain


def test_preview_honors_configured_from_name_and_address(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAIL_FROM_ADDRESS", "noreply@corp.test")
    monkeypatch.setenv("MAIL_FROM_NAME", "Corp Notifier")

    result = runner.invoke(cli_app, ["mail:preview"])

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "Corp Notifier <noreply@corp.test>" in plain


def test_preview_renders_html_body_when_given(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        cli_app, ["mail:preview", "--html", "<h1>Big Hello</h1>", "--text", "plain hello"]
    )

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "Big Hello" in plain
    assert "plain hello" not in plain  # html wins when given


def test_preview_flags_flow_into_the_panel(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        cli_app,
        ["mail:preview", "--to", "ada@example.com", "--subject", "Welcome", "--text", "hi ada"],
    )

    assert result.exit_code == 0, result.output
    plain = ANSI_RE.sub("", result.output)
    assert "ada@example.com" in plain and "Welcome" in plain and "hi ada" in plain


# ---------------------------------------------------------------------------
# mail:clear — destructive guard + truncate
# ---------------------------------------------------------------------------


def test_clear_guard_blocks_in_production(outbox_log, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    outbox_log.write_text(_line("one") + "\n" + _line("two") + "\n")

    result = runner.invoke(cli_app, ["mail:clear"], input="n\n")

    assert result.exit_code == 1
    assert "aborted" in ANSI_RE.sub("", result.output)
    assert len(outbox_log.read_text().splitlines()) == 2  # untouched


def test_clear_force_empties_and_reports_count(outbox_log, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    outbox_log.write_text(_line("one") + "\n" + _line("two") + "\n" + _line("three") + "\n")

    result = runner.invoke(cli_app, ["mail:clear", "--force"])

    assert result.exit_code == 0, result.output
    assert outbox_log.read_text() == ""
    assert "dropped 3 logged message(s)" in ANSI_RE.sub("", result.output)


def test_clear_outside_production_runs_without_prompt(outbox_log):
    outbox_log.write_text(_line("one") + "\n")

    result = runner.invoke(cli_app, ["mail:clear"])

    assert result.exit_code == 0, result.output
    assert outbox_log.read_text() == ""


def test_clear_missing_log_is_dim_exit_zero(outbox_log):
    result = runner.invoke(cli_app, ["mail:clear"])

    assert result.exit_code == 0, result.output
    assert "no mail log" in ANSI_RE.sub("", result.output)
