"""The document module's import must not require pymongo.

``fastplace.orm.documents`` is imported by packages (fastplace-tenancy) whose
users may not have the mongodb extra installed — the adapter degrades to a
clear error at *use* time instead of an ImportError at import time.
"""

from __future__ import annotations

import importlib
import sys

import pytest


def test_documents_imports_without_pymongo():
    import fastplace.orm.documents as docs

    saved = {
        name: sys.modules[name]
        for name in list(sys.modules)
        if name == "pymongo" or name.startswith("pymongo.")
    }
    try:
        # A None entry makes ``import pymongo`` raise ImportError — the
        # module under test must survive that.
        for name in saved:
            sys.modules[name] = None  # type: ignore[assignment]
        reloaded = importlib.reload(docs)
        assert reloaded.ReturnDocument is None
        assert reloaded.AsyncMongoClient is None
    finally:
        sys.modules.update(saved)
        importlib.reload(docs)


def test_documents_use_without_pymongo_raises_a_helpful_error(monkeypatch):
    """Reaching for a collection without the extra explains the fix."""
    import fastplace.orm.documents as docs

    monkeypatch.setattr(docs, "AsyncMongoClient", None)
    monkeypatch.setattr(docs, "_client", None)
    from fastplace.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="mongodb"):
        docs.get_documents_client()
