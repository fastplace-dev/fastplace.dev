"""import_gates — boot-time registration loading (spec §4.15/§4.17)."""

from __future__ import annotations

import sys
from pathlib import Path

from fastplace.authz import gate
from fastplace.authz.loader import import_gates


def _clean_modules():
    # "app" too: a cached REAL app package would shadow tmp_path's namespace
    # package and the generated gates.py would never be found.
    for name in ("app.auth.gates", "app.auth", "app"):
        sys.modules.pop(name, None)


class TestImportGates:
    def test_absent_gates_file_is_not_an_error(self, tmp_path):
        assert import_gates(tmp_path) is False

    def test_present_gates_file_registers_abilities(self, tmp_path):
        gates = tmp_path / "app" / "auth"
        gates.mkdir(parents=True)
        (gates / "__init__.py").write_text("")
        (gates / "gates.py").write_text(
            "from fastplace.authz import gate\n"
            "\n"
            "@gate.define('loader-probe')\n"
            "async def loader_probe(user, *args):\n"
            "    return True\n"
        )
        try:
            assert import_gates(tmp_path) is True
            assert gate._abilities.keys() >= {"loader-probe"}
        finally:
            _clean_modules()

    def test_broken_gates_module_raises_at_boot(self, tmp_path):
        gates = tmp_path / "app" / "auth"
        gates.mkdir(parents=True)
        (gates / "__init__.py").write_text("")
        (gates / "gates.py").write_text("import not_a_real_module_xyz\n")
        try:
            import pytest

            with pytest.raises(ModuleNotFoundError):
                import_gates(tmp_path)
        finally:
            _clean_modules()


class TestKernelWiring:
    def test_create_app_imports_gates(self, monkeypatch):
        # create_app resolves `from fastplace.authz import import_gates` at
        # call time — patching the package attribute intercepts the boot hook.
        import os

        import fastplace.authz as authz_pkg
        from fastplace.http.kernel import create_app

        recorded: list[Path] = []

        def spy(project_root=None):
            recorded.append(Path(project_root))
            return False

        monkeypatch.setattr(authz_pkg, "import_gates", spy)
        root = Path.cwd()
        env_before = set(os.environ)
        try:
            create_app(root)
        finally:
            for key in set(os.environ) - env_before:
                os.environ.pop(key, None)
        assert recorded == [root]
