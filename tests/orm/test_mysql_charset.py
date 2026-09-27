"""MySQL charset/collation pinning (mysql-G3).

Framework tables must not inherit the server's default charset — a
database created with latin1 would silently truncate emoji and Bengali
text. The engine connection charset and the per-table DDL defaults are
pinned to utf8mb4 / InnoDB unless the project overrides them; a model's
own ``__table_args__`` always wins over the framework defaults.
"""

from __future__ import annotations

# Autouse fixtures: per-test db facade + model registry (tests/orm/conftest.py).


def _mysql_url(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql://user:secret@localhost:3306/ffw2_my")
    monkeypatch.setenv("DATABASE_DRIVER", "mysql")


def _captured_engine_kwargs(monkeypatch) -> dict:
    """Spy on create_async_engine so connect_args can be asserted wire-free."""
    import fastplace.orm.manager as manager_module

    original = manager_module.create_async_engine
    captured: dict = {}

    def spy(url, **kwargs):
        captured.update(kwargs)
        return original(url, **kwargs)

    monkeypatch.setattr(manager_module, "create_async_engine", spy)
    return captured


async def test_engine_connection_pins_utf8mb4_charset(monkeypatch):
    _mysql_url(monkeypatch)
    captured = _captured_engine_kwargs(monkeypatch)

    from fastplace.db import db

    db.manager.engine()  # engine creation is lazy — force it

    assert captured["connect_args"]["charset"] == "utf8mb4"


async def test_charset_env_override_reaches_the_connection(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql://user:secret@localhost:3306/ffw2_my")
    monkeypatch.setenv("DATABASE_CHARSET", "utf8mb3")
    captured = _captured_engine_kwargs(monkeypatch)

    from fastplace.db import db

    db.manager.engine()

    assert captured["connect_args"]["charset"] == "utf8mb3"


def test_framework_tables_default_to_innodb_utf8mb4(monkeypatch):
    from fastplace.orm import Field, Model

    _mysql_url(monkeypatch)

    class Doc(Model):
        __tablename__ = "w2_charset_default"

        title: str = Field(default="")

    kwargs = Doc.__table__.dialect_kwargs
    assert kwargs["mysql_engine"] == "InnoDB"
    assert kwargs["mysql_charset"] == "utf8mb4"
    assert kwargs["mysql_collate"] == "utf8mb4_unicode_ci"


def test_user_table_args_win_over_framework_defaults(monkeypatch):
    from fastplace.orm import Field, Model

    _mysql_url(monkeypatch)

    class Doc(Model):
        __tablename__ = "w2_charset_user_wins"
        __table_args__ = {"mysql_engine": "MyISAM", "mysql_collation": "utf8mb4_general_ci"}

        title: str = Field(default="")

    kwargs = Doc.__table__.dialect_kwargs
    assert kwargs["mysql_engine"] == "MyISAM"
    assert kwargs["mysql_collation"] == "utf8mb4_general_ci"
    # The framework must not bolt its own collate beside the user's —
    # the server would receive two collation options.
    assert "mysql_collate" not in kwargs
    # The unset knob still gets its framework default.
    assert kwargs["mysql_charset"] == "utf8mb4"


def test_user_tuple_with_dict_merges_without_losing_args(monkeypatch):
    from sqlalchemy import Index

    from fastplace.orm import Field, Model

    _mysql_url(monkeypatch)

    class Doc(Model):
        __tablename__ = "w2_charset_tuple"
        __table_args__ = (Index("ix_w2_tuple_title", "title"), {"mysql_charset": "latin1"})

        title: str = Field(default="")

    kwargs = Doc.__table__.dialect_kwargs
    assert kwargs["mysql_charset"] == "latin1"  # user value wins
    assert kwargs["mysql_engine"] == "InnoDB"  # default fills the gap
    assert "ix_w2_tuple_title" in {i.name for i in Doc.__table__.indexes}  # args survive


def test_collation_env_override(monkeypatch):
    from fastplace.orm import Field, Model

    _mysql_url(monkeypatch)
    monkeypatch.setenv("DATABASE_COLLATION", "utf8mb4_0900_ai_ci")

    class Doc(Model):
        __tablename__ = "w2_charset_collation"

        title: str = Field(default="")

    assert Doc.__table__.dialect_kwargs["mysql_collate"] == "utf8mb4_0900_ai_ci"


def test_non_mysql_backends_get_no_mysql_kwargs(monkeypatch):
    from fastplace.orm import Field, Model

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost/ffw2_pg")

    class Doc(Model):
        __tablename__ = "w2_charset_pg"

        title: str = Field(default="")

    assert not [key for key in Doc.__table__.dialect_kwargs if key.startswith("mysql_")]


def test_mysql_vector_column_keeps_table_defaults_without_index(monkeypatch):
    """Vector fallback on MySQL: no ANN, no btree — but charset pinning still applies."""
    from fastplace.orm import Model, VectorField

    _mysql_url(monkeypatch)

    class Chunk(Model):
        __tablename__ = "w2_charset_vector"

        embedding: list[float] = VectorField(dimensions=3, index=True)

    assert list(Chunk.__table__.indexes) == []
    assert Chunk.__table__.dialect_kwargs["mysql_charset"] == "utf8mb4"
