"""`key:generate` and `log:tail` CLI commands (spec #32, #33)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Rich colorizes when the environment forces color; strip codes before matching.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()

# A real key line: `fastplace new` writes 64 url-safe chars; the spec floor is 20.
KEY_LINE_RE = re.compile(r"^APP_KEY=([A-Za-z0-9_-]{20,})$", re.MULTILINE)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A scaffolded project cwd; process env and config restored after.

    ``new`` ships a generated APP_KEY in .env and an empty ``APP_KEY=``
    placeholder in .env.example — exactly the surface key:generate manages.
    """
    cwd_before = Path.cwd().resolve()
    env_before = dict(os.environ)

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["new", "blog"])
    assert result.exit_code == 0, result.output

    root = tmp_path / "blog"
    monkeypatch.chdir(root)
    try:
        yield root
    finally:
        os.environ.clear()
        os.environ.update(env_before)
        from fastplace.config import reset_config

        reset_config(cwd_before)


@pytest.fixture
def log_project(tmp_path, monkeypatch):
    """A minimal project tree — asgi.py plus storage/logs — no scaffold needed.

    ``log:tail`` never imports project code (it reads files only), so the
    guard's asgi.py marker is all a project needs to look like.
    """
    root = tmp_path / "srv"
    (root / "storage" / "logs").mkdir(parents=True)
    (root / "asgi.py").write_text("app = None\n")
    monkeypatch.chdir(root)
    return root


def _blank_app_key(root: Path) -> None:
    """Empty the scaffolded APP_KEY so key:generate has nothing to refuse."""
    env = root / ".env"
    env.write_text(KEY_LINE_RE.sub("APP_KEY=", env.read_text(), count=1))


def _app_key(root: Path) -> str:
    match = KEY_LINE_RE.search((root / ".env").read_text())
    assert match is not None, "expected a generated APP_KEY line"
    return match.group(1)


def _run(*args):
    result = runner.invoke(cli_app, list(args))
    return result.exit_code, ANSI_RE.sub("", result.output)


# --- key:generate --------------------------------------------------------------


def test_key_generate_writes_app_key_without_printing_it(project):
    _blank_app_key(project)
    code, out = _run("key:generate")
    assert code == 0, out
    key = _app_key(project)
    assert re.fullmatch(r"[A-Za-z0-9_-]{20,}", key)
    assert key not in out  # the secret never reaches the terminal
    assert "APP_KEY" in out


def test_key_generate_show_prints_a_key_and_leaves_env_untouched(project):
    _blank_app_key(project)
    code, out = _run("key:generate", "--show")
    assert code == 0, out
    assert re.fullmatch(r"[A-Za-z0-9_-]{20,}", out.strip())
    # --show is read-only: the blanked APP_KEY stays blank.
    assert KEY_LINE_RE.search((project / ".env").read_text()) is None


def test_key_generate_refuses_to_replace_an_existing_key(project):
    before = _app_key(project)  # the scaffold ships a generated key
    code, out = _run("key:generate")
    assert code == 1
    assert "--force" in out
    assert _app_key(project) == before


def test_key_generate_force_rotates_and_never_leaks(project):
    before = _app_key(project)
    code, out = _run("key:generate", "--force")
    assert code == 0, out
    after = _app_key(project)
    assert after != before
    assert before not in out and after not in out


def test_key_generate_documents_example_without_the_secret(project):
    _blank_app_key(project)
    code, out = _run("key:generate")
    assert code == 0, out
    example = (project / ".env.example").read_text()
    assert _app_key(project) not in example  # never the live secret
    assert re.search(r"^#?\s*APP_KEY\s*=$", example, re.MULTILINE)  # a placeholder line


# --- log:tail ------------------------------------------------------------------


def test_log_tail_filters_by_level_in_single_pass(log_project):
    logs = log_project / "storage" / "logs"
    (logs / "app.log").write_text("ERROR x\nINFO y\n")
    code, out = _run("log:tail", "--level=ERROR", "--lines", "20")
    assert code == 0, out
    assert "ERROR x" in out
    assert "INFO y" not in out


def test_log_tail_defaults_to_the_newest_log_by_mtime(log_project):
    logs = log_project / "storage" / "logs"
    (logs / "old.log").write_text("ERROR old\n")
    (logs / "app.log").write_text("ERROR new\n")
    os.utime(logs / "old.log", (1, 1))  # deterministic ordering
    code, out = _run("log:tail", "--lines", "5")
    assert code == 0, out
    assert "ERROR new" in out
    assert "ERROR old" not in out


def test_log_tail_file_override_targets_the_named_log(log_project):
    logs = log_project / "storage" / "logs"
    (logs / "app.log").write_text("ERROR newest\n")
    (logs / "other.log").write_text("ERROR chosen\n")
    code, out = _run("log:tail", "--file", "storage/logs/other.log", "--lines", "5")
    assert code == 0, out
    assert "ERROR chosen" in out
    assert "ERROR newest" not in out


def test_log_tail_lines_caps_the_backfill(log_project):
    body = "\n".join(f"ERROR e{i}" for i in range(1, 6)) + "\n"  # e1..e5
    (log_project / "storage" / "logs" / "app.log").write_text(body)
    code, out = _run("log:tail", "--lines", "2")
    assert code == 0, out
    assert "ERROR e4" in out and "ERROR e5" in out
    assert "ERROR e1" not in out and "ERROR e3" not in out


def test_log_tail_backfill_never_materializes_the_whole_file(log_project, monkeypatch):
    """The backfill must stream — a tail command that loads the entire log
    cannot keep memory bounded on a growing file, so any whole-file read is
    a failure, not just a style issue."""
    logs = log_project / "storage" / "logs"
    (logs / "app.log").write_text("ERROR one\nINFO noise\nERROR two\nERROR three\n")

    def _forbidden(self, *args, **kwargs):
        raise AssertionError("log:tail must stream the file, not read it whole")

    monkeypatch.setattr(Path, "read_text", _forbidden)
    code, out = _run("log:tail", "--level=ERROR", "--lines", "2")
    assert code == 0, out
    assert "ERROR two" in out and "ERROR three" in out
    assert "ERROR one" not in out and "INFO noise" not in out


def test_log_tail_backfill_on_a_log_far_larger_than_the_window(log_project):
    """Regression coverage: a window (20) far smaller than the file (2000
    matching lines) still prints exactly the last 20. Output semantics only —
    the memory bound is asserted by the streaming test above."""
    body = "\n".join(f"ERROR e{i:04d}" for i in range(2000)) + "\n"
    (log_project / "storage" / "logs" / "app.log").write_text(body)
    code, out = _run("log:tail", "--lines", "20")
    assert code == 0, out
    assert "ERROR e1999" in out and "ERROR e1980" in out
    assert "ERROR e1979" not in out and "ERROR e0000" not in out


def test_log_tail_without_logs_exits_friendly(log_project):
    code, out = _run("log:tail", "--lines", "5")
    assert code == 1
    assert "storage/logs" in out


# --- outside-project guards ----------------------------------------------------


@pytest.mark.parametrize(
    "command", [["key:generate"], ["key:generate", "--show"], ["log:tail", "--lines", "5"]]
)
def test_key_and_log_commands_outside_a_project_fail_friendly(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, command)
    assert result.exit_code == 1
    assert "not inside a Fastplace project" in ANSI_RE.sub("", result.output)
