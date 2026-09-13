"""Request/task-scoped async session management via context variables.

The ambient session is tracked together with its connection name, the task
that opened it, and whether an enclosing ``db.transaction()`` owns the commit.
Writes in scopes that do NOT own the commit (``db.connection()``,
``session_scope()``) commit per operation — nothing is silently rolled back.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class _ScopeState:
    """One ambient session binding: which connection, who owns the commit."""

    session: AsyncSession
    name: str = "default"
    owns_commit: bool = False
    owner: asyncio.Task[Any] | None = None


_current_session: ContextVar[_ScopeState | None] = ContextVar(
    "fastplace_session", default=None
)


def ambient() -> _ScopeState | None:
    """The ambient scope state, refusing cross-task session reuse.

    asyncio child tasks inherit context variables; sharing one AsyncSession
    across tasks corrupts transactions ("Session is already flushing"). Child
    tasks must open their own scope instead.
    """
    state = _current_session.get()
    if state is None:
        return None
    task = asyncio.current_task()
    if state.owner is not None and task is not state.owner:
        raise RuntimeError(
            "The ambient database session was opened in another asyncio task. "
            "Child tasks inherit the context but must not share one session — "
            "open an explicit scope in the child: `async with db.transaction():` "
            "or `async with db.connection():`."
        )
    return state


def current_session() -> AsyncSession | None:
    """The ambient session (raw accessor — no cross-task guard)."""
    state = _current_session.get()
    return state.session if state is not None else None


@contextmanager
def bind_session(
    session: AsyncSession, *, name: str = "default", owns_commit: bool = False
):
    """Bind a session as ambient for the current task."""
    state = _ScopeState(session=session, name=name, owns_commit=owns_commit)
    state.owner = asyncio.current_task()
    tok: Token = _current_session.set(state)
    try:
        yield session
    finally:
        _current_session.reset(tok)


@contextmanager
def mark_owns_commit():
    """Declare (inner) transaction semantics: the scope owns the commit.

    Used around ``begin_nested()`` blocks so per-operation commits do not
    defeat a savepoint's rollback while a non-owning scope (``db.connection()``)
    is ambient outside.
    """
    state = _current_session.get()
    if state is None:  # pragma: no cover — defensive
        yield
        return
    previous = state.owns_commit
    state.owns_commit = True
    try:
        yield
    finally:
        state.owns_commit = previous


@asynccontextmanager
async def session_scope(name: str = "default"):
    """Open a short-lived session; repositories join it implicitly.

    Unit-of-work semantics: a clean exit commits pending changes, an exception
    rolls them back. Per-operation writes inside the scope also commit —
    ``run_write`` commits whenever the ambient scope does not own the commit.
    """
    from fastplace.orm.manager import get_manager

    session = get_manager().session(name)
    with bind_session(session, name=name, owns_commit=False):
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
        else:
            if session.in_transaction():
                await session.commit()
        finally:
            await session.close()


async def run_read(statement: Any, name: str = "default") -> Any:
    """Execute a SELECT in the ambient session, or a short-lived one.

    An open scope (``db.transaction()`` / ``db.connection()``) is authoritative:
    ORM reads always join it, whatever connection it was opened on. ``name``
    only selects the connection when no ambient scope exists.
    """
    state = ambient()
    if state is not None:
        return await state.session.execute(statement)
    async with session_scope(name) as session:
        return await session.execute(statement)


async def run_write(action: Any, *args: Any, name: str = "default", **kwargs: Any) -> Any:
    """Run a write action; a commit-owning scope flushes, others commit now.

    Joins the ambient scope when one is open (see ``run_read``); otherwise
    opens a short-lived committing session on ``name``.
    """
    state = ambient()
    if state is not None:
        result = await action(state.session, *args, **kwargs)
        if not state.owns_commit:
            # db.connection()/session_scope(): unit-of-work — commit per write.
            await state.session.commit()
        return result

    async with session_scope(name) as session:
        result = await action(session, *args, **kwargs)
        await session.commit()
        return result
