"""@Tool decorator — JSON schema from type hints + docstrings (blueprint §9)."""

import enum
import json
from typing import Literal

import pytest
from pydantic import BaseModel

from fastplace.ai import Tool, registered_tools, reset_tool_registry, tool_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_tool_registry()
    yield
    reset_tool_registry()


def test_tool_builds_schema_from_hints_and_docstring():
    @Tool(description="Search internal documentation")
    async def search_docs(query: str, limit: int = 3) -> list[str]:
        """Search the knowledge base.

        Args:
            query: The search text.
            limit: Maximum hits to return.
        """
        return [f"{query}:{i}" for i in range(limit)]

    spec = tool_registry["search_docs"]
    assert spec.name == "search_docs"
    assert spec.description == "Search internal documentation"
    assert spec.parameters == {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search text."},
            "limit": {"type": "integer", "description": "Maximum hits to return."},
        },
        "required": ["query"],  # `limit` has a default — optional
    }


def test_tool_openai_wire_format():
    @Tool(description="d")
    async def ping(value: str) -> str:
        """Ping.

        Args:
            value: what to echo.
        """
        return value

    spec = tool_registry["ping"]
    assert spec.to_openai() == {
        "type": "function",
        "function": {
            "name": "ping",
            "description": "d",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string", "description": "what to echo."}},
                "required": ["value"],
            },
        },
    }


def test_function_stays_callable_and_carries_its_spec():
    calls = []

    @Tool(description="record")
    def record(note: str) -> str:
        """Record.

        Args:
            note: the note.
        """
        calls.append(note)
        return note.upper()

    assert record("x") == "X"  # sync tools stay sync-callable
    assert record.__fastplace_tool__.name == "record"


def test_description_falls_back_to_docstring_summary():
    @Tool()
    async def summarize(text: str) -> str:
        """Summarize the given text in one sentence."""

        return text[:10]

    assert tool_registry["summarize"].description == "Summarize the given text in one sentence."


def test_name_can_be_pinned_for_stable_dispatch():
    @Tool(name="docs_search", description="d")
    async def search(query: str) -> str:
        """Search.

        Args:
            query: q.
        """
        return query

    assert "docs_search" in registered_tools()


def test_dotted_names_are_rejected_for_the_wire_format():
    with pytest.raises(ValueError, match="docs.search"):

        @Tool(name="docs.search", description="d")
        async def dotted(query: str) -> str:
            """Dotted.

            Args:
                query: q.
            """


def test_overlong_names_are_rejected():
    with pytest.raises(ValueError, match="64"):

        @Tool(name="x" * 65, description="d")
        async def long_name(query: str) -> str:
            """Long.

            Args:
                query: q.
            """


def test_supported_type_mappings():
    @Tool(description="d")
    async def kitchen(
        text: str,
        count: int,
        ratio: float,
        flag: bool,
        tags: list[str],
        meta: dict[str, str],
        flavor: Literal["sweet", "savory"],
        note: str | None = None,
    ) -> str:
        """Kitchen sink.

        Args:
            text: t.
            count: c.
            ratio: r.
            flag: f.
            tags: g.
            meta: m.
            flavor: pick one.
            note: n.
        """
        return text

    props = tool_registry["kitchen"].parameters["properties"]
    assert props["text"] == {"type": "string", "description": "t."}
    assert props["count"]["type"] == "integer"
    assert props["ratio"]["type"] == "number"
    assert props["flag"]["type"] == "boolean"
    assert props["tags"] == {"type": "array", "items": {"type": "string"}, "description": "g."}
    assert props["meta"]["type"] == "object"
    assert props["flavor"]["enum"] == ["sweet", "savory"]
    assert props["note"]["type"] == "string"  # Optional unwraps to the inner type
    required = tool_registry["kitchen"].parameters["required"]
    assert "note" not in required and "flavor" in required


def test_optional_without_default_stays_required():
    @Tool(description="d")
    async def greet(name: str, title: str | None) -> str:
        """Greet.

        Args:
            name: who.
            title: optional honorific, but the caller must still pass it.
        """
        return name

    # Python demands the argument (no default) — the schema must agree,
    # or the model may legally omit it and the dispatch burns a round.
    assert tool_registry["greet"].parameters["required"] == ["name", "title"]


def test_nested_pydantic_parameters_keep_their_defs():
    class Item(BaseModel):
        sku: str

    class Order(BaseModel):
        items: list[Item]

    @Tool(description="d")
    async def place_order(order: Order) -> str:
        """Place an order.

        Args:
            order: the order to place.
        """
        return "ok"

    prop = tool_registry["place_order"].parameters["properties"]["order"]
    # every $ref must resolve inside the fragment — stripping $defs would
    # leave a dangling pointer providers/validators reject
    assert prop["$defs"]["Item"]["properties"]["sku"]["type"] == "string"
    assert prop["properties"]["items"]["items"]["$ref"] == "#/$defs/Item"


def test_flat_pydantic_parameter_inlines_its_schema():
    class Lookup(BaseModel):
        key: str

    @Tool(description="d")
    async def fetch(item: Lookup) -> str:
        """Fetch.

        Args:
            item: what to fetch.
        """
        return "ok"

    prop = tool_registry["fetch"].parameters["properties"]["item"]
    assert prop["type"] == "object"
    assert prop["properties"]["key"]["type"] == "string"


def test_literal_bools_map_to_boolean():
    @Tool(description="d")
    async def toggle(flag: Literal[True, False]) -> str:
        """Toggle.

        Args:
            flag: on or off.
        """
        return "x"

    prop = tool_registry["toggle"].parameters["properties"]["flag"]
    assert prop["enum"] == [True, False]
    assert prop["type"] == "boolean"


def test_mixed_type_literals_omit_the_type_key():
    @Tool(description="d")
    async def pick(value: Literal["a", 1]) -> str:
        """Pick.

        Args:
            value: either shape.
        """
        return "x"

    prop = tool_registry["pick"].parameters["properties"]["value"]
    assert prop["enum"] == ["a", 1]
    assert "type" not in prop  # no contradictory "type"


def test_enum_literals_ship_their_underlying_values():
    """Literal[Enum] must derive a schema providers accept — Python enum
    members are not JSON-serializable, so the wire enum carries each
    member's underlying value and the type follows those values."""

    class Color(enum.Enum):
        RED = "red"
        BLUE = "blue"

    class Priority(enum.IntEnum):
        HIGH = 1
        LOW = 2

    @Tool(description="d")
    async def schedule(
        color: Literal[Color.RED, Color.BLUE], priority: Literal[Priority.HIGH]
    ) -> str:
        """Schedule.

        Args:
            color: what to paint.
            priority: how urgent.
        """
        return "x"

    props = tool_registry["schedule"].parameters["properties"]
    assert props["color"]["enum"] == ["red", "blue"]
    assert props["color"]["type"] == "string"
    assert props["priority"]["enum"] == [1]
    assert props["priority"]["type"] == "integer"
    # the whole parameters object goes on the wire — it must serialize
    json.dumps(tool_registry["schedule"].parameters)


def test_bare_dict_and_list_annotations_are_accepted():
    @Tool(description="d")
    async def notes(meta: dict, tags: list) -> str:
        """Notes.

        Args:
            meta: free-form metadata.
            tags: loose tags.
        """
        return "x"

    props = tool_registry["notes"].parameters["properties"]
    assert props["meta"]["type"] == "object"
    assert props["tags"]["type"] == "array"


def test_untyped_parameter_is_rejected_at_decoration():
    with pytest.raises(ValueError, match="query"):

        @Tool(description="d")
        async def broken(query) -> str:  # type: ignore[no-untyped-def]
            """Broken.

            Args:
                query: q.
            """


def test_duplicate_names_are_rejected():
    @Tool(description="first")
    async def collide(query: str) -> str:
        """One.

        Args:
            query: q.
        """
        return query

    with pytest.raises(ValueError, match="collide"):

        @Tool(name="collide", description="second")
        async def renamed_elsewhere(query: str) -> str:
            """Second.

            Args:
                query: q.
            """


def test_reset_registry_clears_everything():
    @Tool(description="d")
    async def solo(query: str) -> str:
        """Solo.

        Args:
            query: q.
        """
        return query

    reset_tool_registry()
    assert registered_tools() == []
