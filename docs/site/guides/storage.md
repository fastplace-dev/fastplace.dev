# File & Cloud Storage

`fastplace.storage` gives every file operation one async surface. Services read and write through a `Disk` and never care whether the bytes live on the local filesystem or — once an adapter ships — an object store.

```python
from fastplace.storage import disk

disk().put("invoices/2026/first.pdf", pdf_bytes)   # parent dirs auto-created
pdf = await disk().get("invoices/2026/first.pdf")  # bytes; missing -> NotFoundError
await disk().text("notes/readme.txt")              # str (UTF-8)
await disk().exists("invoices/2026/first.pdf")
await disk().copy("invoices/2026/first.pdf", "archive/2026/first.pdf")
await disk().move("inbox/a.pdf", "archive/a.pdf")  # source removed only after a successful copy
await disk().size("invoices/2026/first.pdf")       # bytes
await disk().last_modified("invoices/2026/first.pdf")  # unix seconds | None
await disk().files("invoices", recursive=True)     # sorted disk-relative paths
await disk().delete("invoices/2026/first.pdf")     # deleting absence is a no-op
await disk().url("invoices/2026/first.pdf")        # "/storage/invoices/2026/first.pdf"
```

Every path is disk-relative and `/`-separated, and every operation is containment-checked before any filesystem call: `../` escapes, absolute paths, `C:` prefixes, backslashes, and symlinks pointing outside the root all raise `StoragePathError` — nothing is ever written outside the disk root.

## Configuration

Pick the default disk and declare named disks in `config/storage.py` (environment overrides win as everywhere else):

```python
STORAGE_DISK = "local"

STORAGE_DISKS = {
    "local": {"driver": "local", "root": "storage/app"},
}
```

Resolve a specific disk by name — each is built once and cached:

```python
from fastplace.storage import disk

archive = disk("archive")
```

An unknown disk name or driver raises `ConfigurationError` at resolve time, not deep inside a request.

## Serving files

`url()` is pure path math on the conventional `/storage/<path>` prefix; it never touches the disk and does not require the file to exist. Serving those URLs is your application's choice — mount `StaticFiles` over the same root and the URLs become real:

```python
app.mount("/storage", StaticFiles(directory="storage/app"), name="storage")
```

The disk computes URLs; the app decides what it exposes. A different mount point? Pass `url_base="/media"` when constructing the `LocalDisk`.

## Private files and signed URLs

`temporary_url()` is deliberately refused on local disks (`StorageNotSupported`): presigning requires authority over an object store, and a local disk cannot honestly sign anything. To serve a local private file, build an app-level signed route with the auth signing primitives — sign the path, verify on a dedicated route, stream the file:

```python
from fastplace.auth.signing import sign, verify

digest, expires = sign(path, ttl=300)   # hand /download?path=..&expires=..&signature=.. to the client
# on the download route: verify(path, signature, expires) before streaming
```

A cloud adapter (S3) implements the same `Disk` interface with a presigned `temporary_url` — no application code changes when you move disks.

## Testing code that uses storage

Subclass `Disk` and implement the primitives over a dict — the derived operations (`text`, `missing`, `move`, `temporary_url`) come free from the base class:

```python
from fastplace.storage import Disk

class FakeDisk(Disk):
    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    async def put(self, path, content):
        self._files[path] = content.encode() if isinstance(content, str) else content

    async def get(self, path):
        return self._files[path]

    # ... exists / delete / copy / size / last_modified / files / url
```

Inject it into the service under test, or build a whole registry without touching config: `Storage(disks={"local": {"driver": "local", "root": str(tmp_path)}}, default="local")`. Call `reset_storage()` in fixtures to drop the process singleton.

## Error taxonomy

- `StoragePathError` — the path escaped the disk root or was not a valid disk-relative path (HTTP 400).
- `NotFoundError` — the file does not exist (`get`, `text`, `size`, `copy`/`move` sources; HTTP 404).
- `StorageNotSupported` — the driver cannot do the operation, e.g. presigning on a local disk (HTTP 500).
