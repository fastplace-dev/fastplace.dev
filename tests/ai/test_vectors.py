"""Vector store registry — pluggable backends behind one API (T5.5)."""

import sys

import pytest

from fastplace.ai import reset_vector_registry, vector_registry
from fastplace.ai.vectors import (
    PgVectorStore,
    active_vector_store,
    import_vector_stores,
    register_vector_store,
    vector_store,
)


def _write_store_project(root, store_name: str = "faiss") -> None:
    """Materialize a real project with one registered vector store."""
    app = root / "app"
    (app / "ai" / "vectors").mkdir(parents=True, exist_ok=True)
    (app / "__init__.py").write_text("")
    (app / "ai" / "__init__.py").write_text("")
    (app / "ai" / "vectors" / "__init__.py").write_text("")
    (app / "ai" / "vectors" / f"{store_name}.py").write_text(
        "from fastplace.ai.vectors import register_vector_store\n"
        "\n"
        "@register_vector_store('faiss')\n"
        "class FaissStore:\n"
        "    async def search(self, model_cls, embedding, limit=10):\n"
        "        return []\n"
    )


def _tmp_bound_modules(root) -> list[str]:
    """sys.modules entries whose __file__ lives under root."""
    return [
        name
        for name, module in sys.modules.items()
        if (name == "app" or name.startswith("app."))
        and str(root) in str(getattr(module, "__file__", "") or "")
    ]


@pytest.fixture(autouse=True)
def _clean():
    reset_vector_registry()
    yield
    reset_vector_registry()


class _StubModel:
    """Records the ORM-native vector_search delegation."""

    calls: list[tuple[list[float], int]] = []

    @classmethod
    async def vector_search(cls, embedding, limit=10):
        cls.calls.append((embedding, limit))
        return [f"hit:{i}" for i in range(limit)]


def test_pgvector_is_registered_by_default():
    assert isinstance(vector_store("pgvector"), PgVectorStore)


async def test_pgvector_store_delegates_to_the_orm_native_path():
    store = vector_store("pgvector")
    results = await store.search(_StubModel, [0.1, 0.2], limit=3)

    assert results == ["hit:0", "hit:1", "hit:2"]
    assert _StubModel.calls == [([0.1, 0.2], 3)]  # capability gating stays in Model


def test_custom_stores_register_and_resolve():
    @register_vector_store("memory")
    class InMemoryStore:
        async def search(self, model_cls, embedding, limit=10):
            return []

    assert isinstance(vector_store("memory"), InMemoryStore)
    assert "memory" in vector_registry


def test_unknown_store_name_is_a_configuration_error():
    from fastplace.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="ghost"):
        vector_store("ghost")


def test_active_store_follows_config(monkeypatch):
    monkeypatch.setenv("AI_VECTOR_STORE", "pgvector")
    assert isinstance(active_vector_store(), PgVectorStore)


def test_import_vector_stores_registers_app_registrations(tmp_path):
    # a real project's app/ is a regular package chain — the repo's own
    # dogfood `app` would otherwise shadow a bare namespace portion
    _write_store_project(tmp_path)

    names = import_vector_stores(tmp_path)

    assert "faiss" in names
    assert "faiss" in vector_registry
    # the central hygiene guarantee: this project's app* modules must not
    # stay cached and hijack the next `import app` elsewhere
    assert _tmp_bound_modules(tmp_path) == []


def test_import_vector_stores_is_idempotent(tmp_path):
    _write_store_project(tmp_path)

    first = import_vector_stores(tmp_path)
    second = import_vector_stores(tmp_path)  # app-factory reloads call twice

    assert second == first
    assert "faiss" in vector_registry


def test_import_vector_stores_handles_sibling_prefixed_roots(tmp_path):
    api = tmp_path / "api"
    api_v2 = tmp_path / "api-v2"
    _write_store_project(api)
    (api_v2 / "app").mkdir(parents=True)
    (api_v2 / "app" / "__init__.py").write_text("")

    # the sibling's app is cached first, as a long-running CLI process would
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }
    for name in saved:
        sys.modules.pop(name)
    sys.path.insert(0, str(api_v2))
    try:
        import app as sibling_app  # noqa: F401 — cached on purpose
    finally:
        sys.path.remove(str(api_v2))

    try:
        # substring containment ("/…/api" in "/…/api-v2/…") would let the
        # sibling survive the stale sweep and silently shadow this root —
        # real path containment must evict it and load api's own stores
        names = import_vector_stores(api)
        assert "faiss" in names
        assert sys.modules.get("app") is not sibling_app
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name)
        sys.modules.update(saved)


def test_import_vector_stores_without_the_directory_is_a_noop(tmp_path):
    assert import_vector_stores(tmp_path) == []


def test_create_app_boots_with_app_vector_stores_loaded(tmp_path, monkeypatch):
    from fastplace.config import reset_config

    _write_store_project(tmp_path)
    # an empty routes package keeps the repo's own routes/ from leaking in
    # through sys.path — the tmp project must boot standalone
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "__init__.py").write_text("")
    monkeypatch.setenv("AI_VECTOR_STORE", "faiss")

    from fastplace.http.kernel import create_app

    saved_routes = {
        name: module
        for name, module in sys.modules.items()
        if name == "routes" or name.startswith("routes.")
    }
    for name in saved_routes:
        sys.modules.pop(name)
    try:
        create_app(tmp_path)
        store = active_vector_store()
        assert type(store).__name__ == "FaissStore"
    finally:
        reset_config()  # back to the repo's own config
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))
        for name in _tmp_bound_modules(tmp_path):
            sys.modules.pop(name, None)
        for name in [n for n in sys.modules if n == "routes" or n.startswith("routes.")]:
            sys.modules.pop(name)
        sys.modules.update(saved_routes)
