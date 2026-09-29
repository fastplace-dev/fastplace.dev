"""Storage configuration — named disks over the storage abstraction."""

# Used by the commented s3 entry below once uncommented — per-key config()
# calls are what make S3 credentials individually env-overridable.
from fastplace.config import config  # noqa: F401

# The disk storage.disk() returns when no name is given.
STORAGE_DISK = "local"

# Named disks; each entry picks a driver plus its settings. The "local" root
# is process-relative ("storage/app" under the app root) and is served at
# /storage only if the app mounts StaticFiles over it — the disk computes
# URLs, the app decides what it exposes.
STORAGE_DISKS = {
    "local": {"driver": "local", "root": "storage/app"},
    # S3 driver (pip install 'fastplace[s3]'). Uncomment and fill in:
    # "s3": {
    #     "driver": "s3",
    #     "bucket": config("S3_BUCKET", default=""),
    #     "region": config("S3_REGION", default="us-east-1"),
    #     "endpoint_url": config("S3_ENDPOINT_URL", default=None),
    #     "prefix": config("S3_PREFIX", default=""),
    #     "public_base": config("S3_PUBLIC_BASE", default=None),
    #     "public": config("S3_PUBLIC", default=False),
    # },
}
# Notes: the dict itself is not env-carriable (STORAGE_DISKS cannot ride
# through the environment wholesale) — credentials reach it through the
# per-key config() calls above, each individually overridable via env.
