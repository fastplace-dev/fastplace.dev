"""Task 2 — options: --mcp, FASTPLACE_VERSION pin, re-run, existing uv."""

from __future__ import annotations

from tests.installer.conftest import run_installer


def _uv_args(home) -> list[str]:
    return (home / "uv.log").read_text().strip().split()


def test_mcp_flag_installs_extra(sandbox):
    sandbox.write_stub(
        "uv",
        (sandbox.stub_bin / "uv-staged").read_text(),
    )
    r = run_installer(sandbox.home, args=("--mcp",))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _uv_args(sandbox.home)[5] == "fastplace[mcp]"


def test_env_version_pins_spec(sandbox):
    sandbox.write_stub(
        "uv",
        (sandbox.stub_bin / "uv-staged").read_text(),
    )
    r = run_installer(sandbox.home, env={"FASTPLACE_VERSION": "0.4.3"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert _uv_args(sandbox.home)[5] == "fastplace==0.4.3"


def test_invalid_version_rejected_before_install(sandbox):
    sandbox.write_stub(
        "uv",
        (sandbox.stub_bin / "uv-staged").read_text(),
    )
    for bad in ("abc", "==1", "1..2"):
        r = run_installer(sandbox.home, env={"FASTPLACE_VERSION": bad})
        assert r.returncode == 2, f"FASTPLACE_VERSION={bad!r} accepted"
        assert "FASTPLACE_VERSION" in r.stdout + r.stderr
    assert not (sandbox.home / "uv.log").exists()


def test_existing_uv_skips_bootstrap(sandbox):
    sandbox.write_stub(
        "uv",
        (sandbox.stub_bin / "uv-staged").read_text(),
    )
    r = run_installer(sandbox.home)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (sandbox.home / "uv-install.log").exists()


def test_rerun_upgrades(sandbox):
    sandbox.write_stub(
        "uv",
        (sandbox.stub_bin / "uv-staged").read_text(),
    )
    first = run_installer(sandbox.home)
    second = run_installer(sandbox.home)
    assert first.returncode == 0 and second.returncode == 0
    lines = (sandbox.home / "uv.log").read_text().strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        assert "--upgrade" in line
