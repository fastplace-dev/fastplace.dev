"""Args-section parsing — a colon line is a parameter only when declared.

Prose like ``Note: must not be empty.`` on its own continuation line used
to be mistaken for a new parameter, stealing the rest of the description.
The parser must anchor parameter lines to the function's actual signature.
"""

import pytest

from fastplace.ai import Tool, reset_tool_registry, tool_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_tool_registry()
    yield
    reset_tool_registry()


def test_colon_sentence_on_its_own_line_stays_in_the_param_description():
    @Tool(description="d")
    def create_issue(title: str) -> str:
        """Create an issue.

        Args:
            title: The issue title.
                Note: must not be empty.
        """
        return title

    description = tool_registry["create_issue"].parameters["properties"]["title"]["description"]
    assert description == "The issue title. Note: must not be empty."


def test_genuine_second_param_still_parses_after_a_colon_sentence():
    @Tool(description="d")
    def edit_issue(title: str, body: str) -> str:
        """Edit an issue.

        Args:
            title: The issue title.
                Note: must not be empty.
            body: The issue body.
        """
        return f"{title}:{body}"

    properties = tool_registry["edit_issue"].parameters["properties"]
    assert properties["title"]["description"] == "The issue title. Note: must not be empty."
    assert properties["body"]["description"] == "The issue body."


def test_unknown_colon_word_before_any_param_is_ignored():
    @Tool(description="d")
    def ping(host: str) -> str:
        """Ping a host.

        Args:
            Warning: echoes only the first reply.
            host: The host to ping.
        """
        return host

    properties = tool_registry["ping"].parameters["properties"]
    # "Warning" is not a declared parameter — the line is dropped, not turned
    # into a phantom parameter, and host still parses.
    assert set(properties) == {"host"}
    assert properties["host"]["description"] == "The host to ping."


def test_typed_param_form_still_recognized():
    @Tool(description="d")
    def scale(value: int) -> int:
        """Scale a value.

        Args:
            value (int): The value to scale.
                Units: none.
        """
        return value * 2

    description = tool_registry["scale"].parameters["properties"]["value"]["description"]
    assert description == "The value to scale. Units: none."
