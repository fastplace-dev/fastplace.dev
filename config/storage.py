"""Storage configuration — named disks over the storage abstraction."""

# The disk storage.disk() returns when no name is given.
STORAGE_DISK = "local"

# Named disks; each entry picks a driver plus its settings. The "local" root
# is process-relative ("storage/app" under the app root) and is served at
# /storage only if the app mounts StaticFiles over it — the disk computes
# URLs, the app decides what it exposes. Cloud adapters (S3) plug in here as
# additional entries once shipped; their temporary_url() presigns.
STORAGE_DISKS = {
    "local": {"driver": "local", "root": "storage/app"},
}
