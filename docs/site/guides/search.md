# Search

Fastplace ships no third-party search dependency. The default is the relational database itself: PostgreSQL full-text search through `Model.full_text_search()`, behind the `supports_full_text` capability flag. Two surfaces, two jobs — a **service** answers queries, an **engine** keeps an index current.

```python
from fastplace.search import get_search_service

results = await get_search_service().search("rocket", model=Post, limit=10)
```

On SQLite/MySQL the database default raises `SearchNotSupported` — use PostgreSQL or register an engine-backed service.

## Making models searchable

Declare the indexed columns on the model — no base-class change:

```python
class Post(Model):
    __tablename__ = "posts"
    __searchable__ = ["title", "body"]

    id: int = Field(primary_key=True)
    title: str
    body: str
```

Enroll it, and its lifecycle keeps the index current:

```python
from fastplace.search import make_searchable, register_engine

register_engine(MyEngine())  # the process-wide index engine
make_searchable(Post)  # queue=True (production default)
```

- `created` / `updated` / `restored` upsert the record; `deleted` removes it — a soft tombstone and `force_delete` alike (the record is serialized from the in-memory instance before the row goes).
- Enrollment is exact-class: a model hierarchy enrolls each indexed class explicitly.
- `make_searchable(Post, engine=other_engine)` pins one engine for that model.
- `unregister_searchable(Post)` detaches; `search_record(instance)` shows the exact payload — `{"id": <pk>, "title": ..., "body": ...}`, JSON-safe scalars, never the ORM object.

With `queue=True` the index update rides the `search_index_sync` queue job through domain-event dispatch: the enqueue waits for the enclosing transaction to commit, a rollback discards it, and a broker outage after commit logs an error and gives up rather than failing the durable write. Use `make_searchable(Post, queue=False)` in dev/tests to call the engine inline (loud failures, no queue).

**Worker note**: the framework job registers when `fastplace.search` is imported. In a queue worker that imports only `app/jobs`, add one line to `app/jobs/__init__.py`:

```python
import fastplace.search  # noqa: F401  — registers the search_index_sync job
```

## Writing an engine

An engine is any object with four async methods — subclassing is welcome, not required:

```python
from fastplace.search import SearchEngine


class MyEngine(SearchEngine):
    async def update(self, records: list) -> int: ...
    async def delete(self, records: list) -> int: ...
    async def flush(self, model: type) -> None: ...
    async def search(self, query: str, *, model: type | None = None, limit: int = 20) -> list: ...
```

`records` may be model instances or `search_record` dicts (normalize through `fastplace.search.search_record`, which passes dicts through untouched) — one engine serves both the in-process and queue paths. `update`/`delete` return how many records were accepted; `flush` drops everything indexed for one model.

### Example: a Meilisearch-style REST adapter

Reference pseudocode for a hosted search engine that talks HTTP — the client suggested here is `httpx` (async). Adapt the routes/payloads to your engine's API:

```python
import httpx
from fastplace.search import SearchEngine, search_record


class MeiliSearchEngine(SearchEngine):
    """REST adapter sketch — MEILI_URL / MEILI_KEY from config."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}
        )

    def _index(self, model: type) -> str:
        return model.__tablename__  # one index per model

    async def update(self, records: list) -> int:
        by_index: dict[str, list[dict]] = {}
        for r in records:
            rec = search_record(r)  # instances -> dicts, dicts pass through
            by_index.setdefault(rec.pop("_index", None) or self._index(type(r)), []).append(rec)
        for index, docs in by_index.items():
            resp = await self._client.post(f"/indexes/{index}/documents", json=docs)
            resp.raise_for_status()
        return len(records)

    async def delete(self, records: list) -> int:
        by_index: dict[str, list[str]] = {}
        for r in records:
            rec = search_record(r)
            by_index.setdefault(self._index(type(r)), []).append(str(rec["id"]))
        for index, ids in by_index.items():
            resp = await self._client.post(f"/indexes/{index}/documents/delete-batch", json=ids)
            resp.raise_for_status()
        return len(records)

    async def flush(self, model: type) -> None:
        resp = await self._client.delete(f"/indexes/{self._index(model)}")
        resp.raise_for_status()

    async def search(self, query: str, *, model: type | None = None, limit: int = 20) -> list:
        if model is None:
            raise ValueError("model is required — pass the repository model to search")
        resp = await self._client.post(
            f"/indexes/{self._index(model)}/search", json={"q": query, "limit": limit}
        )
        resp.raise_for_status()
        # Rehydrate rows through the model so callers get ORM instances back.
        ids = [hit["id"] for hit in resp.json()["hits"]]
        return await model.where(model.__table__.c.id.in_(ids)).get() if ids else []
```

Register it before the app serves traffic (`register_engine(MeiliSearchEngine(...))` at boot) — a searchable model pointed at the default query-only engine raises `SearchNotSupported` on the first write, which is the framework telling you the index would silently drift.
