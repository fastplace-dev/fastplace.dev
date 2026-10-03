"""Guideline composition + the <fastplace-guidelines> tag-replace writer."""

from __future__ import annotations

from pathlib import Path

from fastplace.mcp.install.guidelines import (
    GUIDELINES_TAG_END,
    GUIDELINES_TAG_START,
    compose_guidelines,
    write_guidelines,
)


def test_compose_wraps_tag_with_live_values():
    markdown = compose_guidelines(root=Path("/proj"), fastplace_version="0.4.1")

    assert markdown.startswith(GUIDELINES_TAG_START)
    assert markdown.rstrip().endswith(GUIDELINES_TAG_END)
    assert "0.4.1" in markdown
    # the section suite ships with every install
    for heading in (
        "Foundation",
        "HTTP Layer",
        "Conventions",
        "Testing",
        "Database",
        "React Bridge",
        "AI",
        "CLI",
        "MCP",
    ):
        assert heading in markdown


def test_write_creates_new_agents_md(tmp_path: Path):
    path = write_guidelines(tmp_path / "AGENTS.md", "CONTENT")

    text = path.read_text()
    assert GUIDELINES_TAG_START in text
    assert "CONTENT" in text
    assert text.endswith("\n")


def test_write_replaces_existing_block_in_place(tmp_path: Path):
    path = tmp_path / "AGENTS.md"
    write_guidelines(path, "OLD")
    path.write_text("user head\n\n" + path.read_text() + "\nuser tail\n")
    before = path.read_text()

    write_guidelines(path, "NEW")

    text = path.read_text()
    assert "user head" in text and "user tail" in text
    assert text.index("user head") < text.index(GUIDELINES_TAG_START)
    assert "OLD" not in text and "NEW" in text
    # position preserved: block still after the head, before the tail
    assert before.index("user tail") > before.index(GUIDELINES_TAG_START)


def test_write_is_idempotent(tmp_path: Path):
    path = tmp_path / "AGENTS.md"
    write_guidelines(path, "CONTENT")
    first = path.read_text()
    write_guidelines(path, "CONTENT")

    assert path.read_text() == first
    assert path.read_text().count(GUIDELINES_TAG_START) == 1


def test_write_appends_to_existing_content_without_tags(tmp_path: Path):
    path = tmp_path / "AGENTS.md"
    path.write_text("existing user rules\n")

    write_guidelines(path, "CONTENT")

    text = path.read_text()
    assert text.startswith("existing user rules")
    assert GUIDELINES_TAG_START in text


def test_write_accepts_full_tagged_block_without_double_wrap(tmp_path: Path):
    path = tmp_path / "AGENTS.md"
    tagged = GUIDELINES_TAG_START + "\nBODY\n\n" + GUIDELINES_TAG_END

    write_guidelines(path, tagged)

    text = path.read_text()
    assert text.count(GUIDELINES_TAG_START) == 1
    assert "BODY" in text


def test_write_heals_double_wrapped_prior_install(tmp_path: Path):
    path = tmp_path / "AGENTS.md"
    path.write_text(
        GUIDELINES_TAG_START * 2
        + "\nold body\n\n"
        + GUIDELINES_TAG_END
        + "\n\n"
        + GUIDELINES_TAG_START
        + "\nold body\n\n"
        + GUIDELINES_TAG_END
        + "\n"
    )

    write_guidelines(path, "NEW BODY")

    text = path.read_text()
    assert text.count(GUIDELINES_TAG_START) == 1
    assert "NEW BODY" in text
    assert "old body" not in text
