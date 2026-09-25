"""import_agents — project agent-factory discovery (import_tools' mirror)."""

from __future__ import annotations

import sys

import pytest

from fastplace.ai import Agent, import_agents, registered_agent_factories, reset_agent_factories

ASSISTANT = (
    "from fastplace.ai import Agent\n\n"
    "def assistant_agent() -> Agent:\n"
    "    return Agent(model='gpt-4o-mini')\n"
)
STRING_HINT = (
    "from __future__ import annotations\n"
    "from fastplace.ai import Agent\n\n"
    "def helper() -> Agent:\n"
    "    return Agent()\n"
)
NOT_A_FACTORY = "def utility():\n    return 42\n"
REEXPORT = "from app.ai.agents.assistant import assistant_agent\n"


@pytest.fixture(autouse=True)
def _clean_factories():
    reset_agent_factories()
    yield
    reset_agent_factories()


@pytest.fixture(autouse=True)
def _park_repo_app_modules():
    """Save/restore the app.* slice of sys.modules around each test.

    import_agents evicts cached app.* modules bound to another root (the
    repo's own app package) — later suites need it back exactly as found.
    Inline copy of tests/cli/_isolation.py's park_app_modules (tests/ai
    cannot import the CLI test helper).
    """
    saved = {n: m for n, m in sys.modules.items() if n == "app" or n.startswith("app.")}
    yield
    for n in [n for n in list(sys.modules) if n == "app" or n.startswith("app.")]:
        del sys.modules[n]
    sys.modules.update(saved)


def _write_agents(root, files: dict[str, str]) -> None:
    agents = root / "app" / "ai" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    # Regular-package markers: without them the tmp tree is only a PEP 420
    # namespace portion, and the repo's own regular ``app`` package (still on
    # sys.path) wins the import — same trick as tests/queue/test_import_jobs_roots.
    for marker in (
        root / "app" / "__init__.py",
        root / "app" / "ai" / "__init__.py",
        agents / "__init__.py",
    ):
        marker.write_text("")
    for filename, source in files.items():
        (agents / filename).write_text(source)


def test_import_agents_collects_return_annotated_factories(tmp_path):
    _write_agents(tmp_path, {"assistant.py": ASSISTANT, "helpers.py": NOT_A_FACTORY})
    names = import_agents(tmp_path)
    assert names == ["assistant_agent"]
    (factory,) = registered_agent_factories()
    assert factory.name == "assistant_agent"
    assert factory.module == "app.ai.agents.assistant"
    assert isinstance(factory.fn(), Agent)


def test_future_annotations_string_hint_is_resolved(tmp_path):
    _write_agents(tmp_path, {"helper.py": STRING_HINT})
    assert import_agents(tmp_path) == ["helper"]


def test_reexported_functions_are_not_reregistered(tmp_path):
    _write_agents(tmp_path, {"assistant.py": ASSISTANT, "extra.py": REEXPORT})
    names = import_agents(tmp_path)
    # One factory, owned by its defining module — the re-export does not
    # claim ownership (fn.__module__ stays app.ai.agents.assistant).
    assert names == ["assistant_agent"]
    assert registered_agent_factories()[0].module == "app.ai.agents.assistant"


def test_missing_agents_package_returns_empty(tmp_path):
    assert import_agents(tmp_path) == []
    assert registered_agent_factories() == []


def test_stale_factories_from_another_root_are_evicted(tmp_path):
    _write_agents(tmp_path, {"assistant.py": ASSISTANT})
    assert import_agents(tmp_path) == ["assistant_agent"]

    other = tmp_path / "other-project"
    _write_agents(other, {"second.py": STRING_HINT})
    names = import_agents(other)
    assert names == ["helper"]  # assistant_agent died with its evicted module
    modules = [f.module for f in registered_agent_factories()]
    assert modules == ["app.ai.agents.second"]  # module path of second.py


def test_same_name_from_two_modules_in_one_project_collides(tmp_path):
    duplicate = ASSISTANT  # same factory name from a second module → collision
    _write_agents(tmp_path, {"a.py": ASSISTANT, "b.py": duplicate})
    with pytest.raises(ValueError, match="agent factory collision"):
        import_agents(tmp_path)
