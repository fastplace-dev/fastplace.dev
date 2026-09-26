"""The user's acceptance criterion: a scaffolded app builds and tests clean.

R2's gate — ``npm install`` then ``vite build``, ``tsc --noEmit``, and
``vitest run`` must all pass inside a freshly scaffolded project. Slow
(network npm install); skipped when npm is unavailable or
FASTPLACE_SKIP_BUILD_GATE=1.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from fastplace.cli import app as cli_app


def _npm_available() -> bool:
    return shutil.which("npm") is not None and not os.environ.get("FASTPLACE_SKIP_BUILD_GATE")


@pytest.mark.slow
@pytest.mark.skipif(not _npm_available(), reason="npm unavailable or gate skipped")
def test_scaffolded_app_builds_and_tests(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli_app, ["new", "blog", "--no-auth"])
    assert result.exit_code == 0, result.output
    project = tmp_path / "blog"

    install = subprocess.run(
        ["npm", "install"], cwd=project, capture_output=True, text=True, timeout=600
    )
    assert install.returncode == 0, install.stderr[-2000:]

    build = subprocess.run(
        ["npm", "run", "build"], cwd=project, capture_output=True, text=True, timeout=600
    )
    assert build.returncode == 0, build.stderr[-2000:]

    types = subprocess.run(
        ["npx", "tsc", "--noEmit"], cwd=project, capture_output=True, text=True, timeout=300
    )
    assert types.returncode == 0, types.stdout[-2000:] + types.stderr[-2000:]

    tests = subprocess.run(
        ["npx", "vitest", "run"], cwd=project, capture_output=True, text=True, timeout=600
    )
    assert tests.returncode == 0, tests.stdout[-2000:]
