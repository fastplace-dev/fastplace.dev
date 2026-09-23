"""personal_access_tokens — spec §5 schema on the ORM surface."""

from __future__ import annotations


class TestPersonalAccessTokenModel:
    def test_model_declares_the_schema_columns(self):
        from app.modules.accounts.models.personal_access_token import PersonalAccessToken

        columns = PersonalAccessToken.__table__.columns
        expected = {
            "id",
            "user_id",
            "name",
            "token_hash",
            "abilities",
            "last_used_at",
            "expires_at",
            "created_at",
            "updated_at",
            "deleted_at",
        }
        assert expected <= set(columns.keys())
        assert columns["token_hash"].unique
        assert columns["user_id"].index
        assert all(columns[name].nullable for name in ("abilities", "last_used_at", "expires_at"))

    def test_token_hash_stays_hidden_from_serialization(self):
        from app.modules.accounts.models.personal_access_token import PersonalAccessToken

        token = PersonalAccessToken(user_id=7, name="ci", token_hash="a" * 64, abilities=["orders"])
        dumped = token.to_dict()
        assert "token_hash" not in dumped
        assert dumped.get("name") == "ci"

    def test_fillable_covers_only_the_safe_columns(self):
        from app.modules.accounts.models.personal_access_token import PersonalAccessToken

        assert PersonalAccessToken.__fillable__ == {"user_id", "name", "abilities", "expires_at"}


class TestMigrationChain:
    def test_single_head_chains_onto_the_two_factor_revision(self):
        import re
        from pathlib import Path

        versions = Path(__file__).resolve().parents[2] / "database" / "migrations" / "versions"
        revisions: dict[str, str | None] = {}
        for file in versions.glob("*.py"):
            source = file.read_text()
            # Quote-agnostic on purpose: the two Alembic-generated legacy
            # revisions (00d1a37b303e, 6d873dd65fb2) declare single-quoted
            # ids while the hand-written auth revisions use double quotes.
            rev = re.search(r'^revision: str = ["\']([0-9a-f]+)["\']', source, re.M)
            down = re.search(
                r'^down_revision[^=]*= (?:"([0-9a-f]+)"|\'([0-9a-f]+)\'|None)', source, re.M
            )
            assert rev, f"{file.name} declares no revision id"
            revisions[rev.group(1)] = down.group(1) or down.group(2) if down else None
        heads = [r for r in revisions if r not in set(revisions.values())]
        assert heads == ["b9d5c2e8f7a3"]
        assert revisions["b9d5c2e8f7a3"] == "a7c3e9f1b2d4"
