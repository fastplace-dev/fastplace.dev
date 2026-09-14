"""import_jobs across project roots — stale ``app.jobs.*`` submodule eviction.

A second root's import must not silently run the first root's cached job
modules: the eviction sweeps every ``app.*`` submodule bound to another root,
not just the two package markers.
"""

from __future__ import annotations

from pathlib import Path


def _make_root(base: Path, name: str, job_module: str, handler_body: str) -> Path:
    root = base / name
    (root / "app" / "jobs").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "jobs" / "__init__.py").write_text("")
    (root / "app" / "jobs" / f"{job_module}.py").write_text(handler_body)
    return root


HANDLER_A = "from fastplace.queue import Job\n\n\n@Job()\nasync def ping():\n    return 'A'\n"

HANDLER_B = "from fastplace.queue import Job\n\n\n@Job()\nasync def ping():\n    return 'B'\n"


def test_second_root_runs_its_own_handler(tmp_path):
    from fastplace.queue import import_jobs, jobs, reset_registry

    root_a = _make_root(tmp_path, "proj_a", "handlers", HANDLER_A)
    root_b = _make_root(tmp_path, "proj_b", "handlers", HANDLER_B)

    reset_registry()
    try:
        import_jobs(root_a)
        assert "ping" in import_jobs(root_a)

        import_jobs(root_b)
        entry = jobs()["ping"]
        assert "proj_b" in str(entry.fn.__code__.co_filename), (
            "cached app.jobs.handlers from proj_a won — stale submodule eviction missed it"
        )
    finally:
        reset_registry()
