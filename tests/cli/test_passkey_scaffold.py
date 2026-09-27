"""Passkey scaffold assertions (plan Task 5) — the emitted app declares
``fastplace[webauthn]``, carries AUTH_PASSKEYS, ships Security passkey props
and lang keys, and gets a self-contained passkey HTTP suite plus the ES256
simulator it drives.

Every test either scaffolds with ``make:auth`` into ``tmp_path`` and asserts
on the GENERATED content, or asserts on the shipped starter corpus / template
constants directly (the same recipe as tests/cli/test_auth_scaffold_gaps.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastplace.cli import app as cli_app
from fastplace.cli.generators import (
    _CONFIG_AUTH_ORM_TEMPLATE,
    _PYPROJECT_TEMPLATE,
    _SETTINGS_PAGES_CONTROLLER_TEMPLATE,
    _rewrite_fastplace_dep_for_webauthn,
    scaffold_templates_dir,
)

CORPUS = Path(scaffold_templates_dir())


def _invoke(tmp_path: Path, monkeypatch, *args: str):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    return CliRunner().invoke(cli_app, ["make:auth", *args])


@pytest.fixture(autouse=True)
def _gate_isolation():
    from fastplace.authz.gate import Gate

    Gate.reset_shared()
    yield
    Gate.reset_shared()


@pytest.fixture()
def scaffolded(tmp_path, monkeypatch):
    """A scaffolded app plus the base project files `fastplace new` writes."""
    _minimal_project_files(tmp_path)
    result = _invoke(tmp_path, monkeypatch, "--no-migration")
    assert result.exit_code == 0, result.output
    return tmp_path


def _minimal_project_files(tmp_path: Path) -> None:
    """Stand-ins for the non-auth files only `fastplace new` writes."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = [\n    "fastplace",\n]\n'
    )
    (tmp_path / ".env").write_text("APP_KEY=k\n")
    (tmp_path / ".env.example").write_text("APP_KEY=\n")
    (tmp_path / "index.html").write_text(
        "<!doctype html>\n<html>\n<head>\n<title>demo</title>\n</head>\n"
        '<body>\n<div id="fastplace"></div>\n</body>\n</html>\n'
    )
    (tmp_path / "README.md").write_text(
        "# Demo\n\n## Parked form targets\n\nThe starter ships the full "
        "settings UI. These form targets are intentionally\nunrouted until "
        "you wire their backends.\n"
    )


class TestWebauthnDependency:
    def test_make_auth_rewrites_the_fastplace_dep(self, scaffolded):
        pyproject = (scaffolded / "pyproject.toml").read_text()
        assert '"fastplace[webauthn]"' in pyproject
        assert '\n    "fastplace",\n' not in pyproject

    def test_rewrite_handles_a_versioned_dep(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = [\n    "fastplace>=0.1.0",\n]\n'
        )
        assert _rewrite_fastplace_dep_for_webauthn(tmp_path) is True
        content = (tmp_path / "pyproject.toml").read_text()
        assert '"fastplace[webauthn]>=0.1.0",' in content

    def test_rewrite_is_idempotent_on_an_already_rewritten_dep(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = [\n    "fastplace[webauthn]>=0.1.0",\n]\n'
        )
        before = (tmp_path / "pyproject.toml").read_text()
        assert _rewrite_fastplace_dep_for_webauthn(tmp_path) is False
        assert (tmp_path / "pyproject.toml").read_text() == before

    def test_rewrite_without_a_fastplace_dep_is_a_noop(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
        assert _rewrite_fastplace_dep_for_webauthn(tmp_path) is False
        assert "webauthn" not in (tmp_path / "pyproject.toml").read_text()

    def test_rewrite_leaves_other_packages_alone(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = [\n    "fastplace-extra",\n    "fastplace",\n]\n'
        )
        assert _rewrite_fastplace_dep_for_webauthn(tmp_path) is True
        content = (tmp_path / "pyproject.toml").read_text()
        assert '"fastplace-extra",' in content
        assert content.count("fastplace[webauthn]") == 1

    def test_new_auth_pyproject_template_rewrites_the_same_way(self, tmp_path):
        """The `new --auth` path writes the plain template, then the same
        helper rewrites it — both paths share one rewrite."""
        (tmp_path / "pyproject.toml").write_text(
            _PYPROJECT_TEMPLATE.format(slug="demo", fastplace_dep="fastplace>=0.1.0")
        )
        assert _rewrite_fastplace_dep_for_webauthn(tmp_path) is True
        content = (tmp_path / "pyproject.toml").read_text()
        assert '"fastplace[webauthn]>=0.1.0",' in content


class TestEmittedConfigAndProps:
    def test_auth_config_template_declares_auth_passkeys(self):
        assert "AUTH_PASSKEYS" in _CONFIG_AUTH_ORM_TEMPLATE
        assert '"enabled": True' in _CONFIG_AUTH_ORM_TEMPLATE
        assert "APP_PASSKEYS_ENABLED" in _CONFIG_AUTH_ORM_TEMPLATE
        assert "login_max_attempts" in _CONFIG_AUTH_ORM_TEMPLATE

    def test_settings_controller_template_carries_passkey_props(self):
        assert "canManagePasskeys" in _SETTINGS_PAGES_CONTROLLER_TEMPLATE
        assert '"passkeys"' in _SETTINGS_PAGES_CONTROLLER_TEMPLATE
        assert "passkey_guard" in _SETTINGS_PAGES_CONTROLLER_TEMPLATE

    def test_readme_unparks_passkeys(self, scaffolded):
        readme = (scaffolded / "README.md").read_text()
        assert "Still parked: passkeys" not in readme
        assert "Passkeys" in readme
        assert "fastplace[webauthn]" in readme

    def test_lang_keys_ship_passkey_lines(self):
        lang = (CORPUS / "lang/en/messages.py").read_text()
        assert '"auth.passkey.verify_failed": "Unable to verify this passkey."' in lang
        assert '"auth.passkey.added"' in lang
        assert '"auth.passkey.removed"' in lang
        assert '"auth.passkey.confirmed"' in lang
        assert '"auth.passkey.already_registered"' in lang


class TestEmittedPasskeySuite:
    def test_corpus_walk_skips_compiler_artifacts(self, tmp_path, monkeypatch):
        """A stray __pycache__/ .pyc in the corpus (an import during local
        development creates one) must not crash `fastplace new` — the walk
        treats bytecode as non-corpus and copies nothing from it."""
        import fastplace.cli.generators as generators

        fake_corpus = tmp_path / "corpus"
        (fake_corpus / "tests/support").mkdir(parents=True)
        (fake_corpus / "README.md").write_text("# demo\n")
        (fake_corpus / "tests/support/webauthn_sim.py").write_text("VALUE = 1\n")
        pycache = fake_corpus / "tests/support/__pycache__"
        pycache.mkdir()
        (pycache / "webauthn_sim.cpython-313.pyc").write_bytes(b"\xf3\r\nrubbish")

        target = tmp_path / "project"
        target.mkdir()
        monkeypatch.setattr(generators, "scaffold_templates_dir", lambda: str(fake_corpus))
        monkeypatch.setattr(generators, "_project_root", lambda: target)
        generators._write_scaffold_templates(target)

        assert (target / "README.md").is_file()
        assert (target / "tests/support/webauthn_sim.py").is_file()
        assert not (target / "tests/support/__pycache__").exists()

    def test_simulator_ships_self_contained_in_the_corpus(self):
        sim = CORPUS / "tests/support/webauthn_sim.py"
        assert sim.is_file()
        text = sim.read_text()
        assert "class SimulatedAuthenticator" in text
        assert "registration_response" in text
        assert "assertion_response" in text
        # Self-contained: emitted apps must not need a third-party CBOR lib.
        assert "cbor2" not in text
        assert "import webauthn" not in text

    def test_passkey_suite_ships_two_http_round_trips(self):
        suite = CORPUS / "tests/test_passkeys.py"
        assert suite.is_file()
        text = suite.read_text()
        assert "def test_register_and_delete_passkey_over_http" in text
        assert "def test_passkey_login_roundtrip" in text
        assert "webauthn_sim" in text
        # Degrades cleanly without the extra or without mounted routes.
        assert "importorskip" in text

    def test_emitted_conftest_mounts_the_passkey_routes(self):
        """The emitted app fixture passes the auth router through
        mount_passkey_routes — exactly what create_app does."""
        from fastplace.cli.auth_scaffold import _TESTS_CONFTEST_TEMPLATE

        assert "mount_passkey_routes" in _TESTS_CONFTEST_TEMPLATE
