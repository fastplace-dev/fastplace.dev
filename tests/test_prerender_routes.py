"""Prerender route resolution — precedence and normalization."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any


class FakeAsgi:
    PRERENDER_ROUTES = ["/docs", "/docs/0.3/routing/"]


def tmp_root(tmp_path: Path) -> Path:
    """A stand-in project root; no routes-file convention ships this wave."""
    return tmp_path


def test_explicit_flags_win_over_module_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PRERENDER_ROUTES", "/from-env")
    from fastplace.prerender.routes import resolve_prerender_routes

    routes = resolve_prerender_routes(FakeAsgi, ["/cli"], root=tmp_root(tmp_path))
    assert routes == ["/cli"]


def test_module_attr_beats_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PRERENDER_ROUTES", "/from-env")
    from fastplace.prerender.routes import resolve_prerender_routes

    routes = resolve_prerender_routes(FakeAsgi, [], root=tmp_root(tmp_path))
    assert routes == ["/docs", "/docs/0.3/routing"]  # trailing slash stripped


def test_env_used_without_module(monkeypatch, tmp_path):
    monkeypatch.setenv("PRERENDER_ROUTES", "/a, /b/,/a")
    from fastplace.prerender.routes import resolve_prerender_routes

    routes = resolve_prerender_routes(None, [], root=tmp_root(tmp_path))
    assert routes == ["/a", "/b"]  # trimmed, normalized, deduped


def test_default_is_root(tmp_path):
    from fastplace.prerender.routes import resolve_prerender_routes

    assert resolve_prerender_routes(None, [], root=tmp_root(tmp_path)) == ["/"]


def test_rejects_relative(tmp_path):
    import pytest

    from fastplace.prerender.routes import resolve_prerender_routes

    with pytest.raises(ValueError, match="absolute"):
        resolve_prerender_routes(None, ["docs/x"], root=tmp_root(tmp_path))


def test_empty_module_attr_means_zero_routes(monkeypatch, tmp_path):
    """A present-but-empty module list is the app disabling prerender.

    It must NOT fall through to env or default: `fastplace prerender` on
    such an app exits with "nothing to prerender" and touches no files.
    """

    class EmptyAsgi(ModuleType):
        PRERENDER_ROUTES: list[str] = []

    monkeypatch.setenv("PRERENDER_ROUTES", "/from-env")
    from fastplace.prerender.routes import resolve_prerender_routes

    routes = resolve_prerender_routes(EmptyAsgi("asgi"), [], root=tmp_root(tmp_path))
    assert routes == []


def test_blank_env_entries_are_dropped(monkeypatch, tmp_path):
    """Comma noise in the env var is skipped, real entries validated."""
    monkeypatch.setenv("PRERENDER_ROUTES", " /a , ,/b,")
    from fastplace.prerender.routes import resolve_prerender_routes

    routes = resolve_prerender_routes(None, [], root=tmp_root(tmp_path))
    assert routes == ["/a", "/b"]


def test_dedup_preserves_first_occurrence_order(tmp_path):
    from fastplace.prerender.routes import resolve_prerender_routes

    module: Any = ModuleType("asgi")
    module.PRERENDER_ROUTES = ["/b/", "/a", "/b", "/a/"]
    routes = resolve_prerender_routes(module, [], root=tmp_root(tmp_path))
    assert routes == ["/b", "/a"]


def test_explicit_flags_validated_too(tmp_path):
    import pytest

    from fastplace.prerender.routes import resolve_prerender_routes

    with pytest.raises(ValueError, match="absolute"):
        resolve_prerender_routes(None, ["not-/absolute"], root=tmp_root(tmp_path))
