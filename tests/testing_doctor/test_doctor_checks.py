"""test:doctor check logic — dev-extras contract and pytest-config discovery.

Lives outside tests/cli/ deliberately: that tree tests the CLI suite itself.
"""

from __future__ import annotations

from fastplace.cli.testing import (
    _DEV_EXTRA_FIX,
    _REQUIRED_DEV_EXTRAS,
    _WARNED_DEV_EXTRAS,
    _pytest_config_check,
)


def test_required_dev_extras_cover_the_scaffolded_test_toolchain():
    """Everything a scaffolded app's tests import must sit in the dev extra."""
    assert set(_REQUIRED_DEV_EXTRAS) >= {"pytest", "pytest_asyncio", "httpx"}


def test_asgi_lifespan_stays_a_warning_not_a_failure():
    """Scaffolded tests drive ASGITransport directly — asgi-lifespan is optional."""
    assert "asgi_lifespan" in _WARNED_DEV_EXTRAS
    assert "asgi_lifespan" not in _REQUIRED_DEV_EXTRAS


def test_dev_extra_fix_advises_the_dev_extra_install():
    assert _DEV_EXTRA_FIX == "pip install -e '.[dev]'"


def test_pytest_config_found_in_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\nasyncio_mode = "auto"\n', encoding="utf-8"
    )
    check = _pytest_config_check(tmp_path)
    assert check.status == "pass"
    assert check.name == "pytest-config"
    assert "pyproject.toml" in check.detail


def test_pytest_config_found_in_dedicated_ini(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    assert _pytest_config_check(tmp_path).status == "pass"


def test_pytest_config_found_in_tox_ini(tmp_path):
    (tmp_path / "tox.ini").write_text("[pytest]\n", encoding="utf-8")
    assert _pytest_config_check(tmp_path).status == "pass"


def test_missing_pytest_config_warns_with_actionable_fix(tmp_path):
    check = _pytest_config_check(tmp_path)
    assert check.status == "warn"
    assert check.fix


def test_empty_pyproject_without_the_section_warns(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    assert _pytest_config_check(tmp_path).status == "warn"
