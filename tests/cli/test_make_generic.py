"""``make:*`` generic scaffolders — class, enum, exception, interface (spec #19–#22)."""

from __future__ import annotations

import pytest

from fastplace.cli import app as cli_app

# (argv, expected project-relative path, must-appear markers)
_CASES = [
    pytest.param(
        ["make:class", "Money"],
        "app/support/money.py",
        ["class Money:"],
        id="class",
    ),
    pytest.param(
        ["make:enum", "InvoiceStatus"],
        "app/support/invoice_status.py",
        ["class InvoiceStatus(str, Enum):"],
        id="enum",
    ),
    pytest.param(
        ["make:exception", "InvoiceNotFound"],
        "app/support/invoice_not_found.py",
        ["class InvoiceNotFound(Exception):"],
        id="exception",
    ),
    pytest.param(
        ["make:interface", "Notifiable"],
        "app/support/notifiable.py",
        ["class Notifiable(Protocol):"],
        id="interface",
    ),
]


def _invoke(monkeypatch, tmp_path, *args: str):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    return CliRunner().invoke(cli_app, list(args))


class TestGenericScaffolds:
    @pytest.mark.parametrize(("argv", "rel", "markers"), _CASES)
    def test_creates_stub_with_class_markers(self, tmp_path, monkeypatch, argv, rel, markers):
        result = _invoke(monkeypatch, tmp_path, *argv)
        assert result.exit_code == 0, result.output

        path = tmp_path / rel
        assert path.is_file(), f"missing {rel}"
        source = path.read_text()
        for marker in markers:
            assert marker in source, f"{rel} lacks {marker!r}"
        # The stub must be valid Python as written, and the command reports
        # the created path.
        compile(source, str(path), "exec")
        assert rel in result.output

    @pytest.mark.parametrize(("argv", "rel", "markers"), _CASES)
    def test_refuses_to_overwrite_without_force(self, tmp_path, monkeypatch, argv, rel, markers):
        result = _invoke(monkeypatch, tmp_path, *argv)
        assert result.exit_code == 0, result.output

        path = tmp_path / rel
        path.write_text("# SENTINEL — hand edits must survive\n")
        result = _invoke(monkeypatch, tmp_path, *argv)
        assert result.exit_code == 0, result.output
        assert "exists" in result.output
        assert "SENTINEL" in path.read_text()

    @pytest.mark.parametrize(("argv", "rel", "markers"), _CASES)
    def test_force_overwrites_the_stub(self, tmp_path, monkeypatch, argv, rel, markers):
        path = tmp_path / rel
        path.parent.mkdir(parents=True)
        path.write_text("# SENTINEL — replaced under --force\n")

        result = _invoke(monkeypatch, tmp_path, *argv, "--force")
        assert result.exit_code == 0, result.output
        source = path.read_text()
        assert "SENTINEL" not in source
        for marker in markers:
            assert marker in source, f"{rel} lacks {marker!r}"

    def test_creates_app_support_package_on_demand(self, tmp_path, monkeypatch):
        result = _invoke(monkeypatch, tmp_path, "make:class", "Money")
        assert result.exit_code == 0, result.output
        assert (tmp_path / "app" / "support" / "__init__.py").is_file()

    def test_enum_stub_ships_a_member_placeholder_not_fake_values(self, tmp_path, monkeypatch):
        result = _invoke(monkeypatch, tmp_path, "make:enum", "InvoiceStatus")
        assert result.exit_code == 0, result.output
        source = (tmp_path / "app" / "support" / "invoice_status.py").read_text()
        # One example-member comment guides the developer; no value ships.
        assert "TODO" in source
        # No member assignment: the only "=" would come from a fake value.
        lines = [
            line
            for line in source.splitlines()
            if line.strip() and not line.strip().startswith("#") and "=" in line
        ]
        assert lines == []

    def test_rejects_path_shaped_names(self, tmp_path, monkeypatch):
        result = _invoke(monkeypatch, tmp_path, "make:class", "../evil")
        assert result.exit_code == 1
        assert not (tmp_path / "app").exists()
