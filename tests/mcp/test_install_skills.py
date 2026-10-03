"""Skill sync: SKILL.md files written per agent skills path, idempotently."""

from __future__ import annotations

from pathlib import Path

from fastplace.mcp.install.skills import SKILLS, sync_skill


def test_five_skills_defined():
    assert [s.name for s in SKILLS] == [
        "infer-conventions",
        "fastplace-best-practices",
        "testing-best-practices",
        "ai-development",
        "react-bridge-development",
    ]
    for skill in SKILLS:
        assert skill.description
        assert len(skill.body) > 200


def test_sync_writes_skill_md_with_frontmatter(tmp_path: Path):
    status = sync_skill(SKILLS[0], tmp_path / ".claude/skills")

    skill_md = tmp_path / ".claude/skills/infer-conventions/SKILL.md"
    assert skill_md.exists()
    text = skill_md.read_text()
    assert text.startswith("---\n")
    assert "name: infer-conventions" in text
    assert SKILLS[0].description in text
    assert status == "created"


def test_sync_update_detects_external_changes(tmp_path: Path):
    base = tmp_path / ".claude/skills"
    assert sync_skill(SKILLS[1], base) == "created"

    # identical content re-syncs as unchanged
    assert sync_skill(SKILLS[1], base) == "unchanged"

    # a drifted (hand-edited or stale) file is refreshed
    skill_md = base / "fastplace-best-practices/SKILL.md"
    skill_md.write_text("stale")
    assert sync_skill(SKILLS[1], base) == "updated"
    assert "stale" not in skill_md.read_text()
