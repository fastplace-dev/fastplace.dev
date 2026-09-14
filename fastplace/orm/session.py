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
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class _ScopeState:
    """One ambient session binding: which connection, who owns the commit."""

    session: AsyncSession
    name: str = "default"
    owns_commit: bool = False
    owner: asyncio.Task[Any] | None = None
    # Domain events buffered while the scope owns the commit — flushed only
    # after the commit lands, discarded on rollback (fastplace.events).
    deferred_domain_events: list[tuple[str, dict[str, Any], bool | None]] = field(
        default_factory=list
    )


_current_session: ContextVar[_ScopeState | None] = ContextVar("fastplace_session", default=None)

#: Read-after-write consistency (blueprint §8): a standalone write pins the
#: current context to the primary so later reads in the same request see it.
#: Explicit scopes (``db.transaction()``/``db.connection()``) pick their own
#: connection and are never re-routed.
_read_pinned: ContextVar[bool] = ContextVar("fastplace_read_pinned", default=False)


def reads_pinned_to_primary() -> bool:
    """Whether this context has written and must keep reading the primary."""
    return _read_pinned.get()


def reset_primary_pin() -> None:
    """Clear the pin (test isolation; fresh contexts start unpinned)."""
    _read_pinned.set(False)


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
def bind_session(session: AsyncSession, *, name: str = "default", owns_commit: bool = False):
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
async def session_scope(name: str = "default", *, read: bool = False):
    """Open a short-lived session; repositories join it implicitly.

    Unit-of-work semantics: a clean exit commits pending changes, an exception
    rolls them back. Per-operation writes inside the scope also commit —
    ``run_write`` commits whenever the ambient scope does not own the commit.

    ``read=True`` opens the session on a round-robin read replica when the
    connection declares any (and this context has not written — pinned
    contexts stay on the primary for read-after-write consistency).
    """
    from fastplace.orm.manager import get_manager

    manager = get_manager()
    if read and not _read_pinned.get():
        session = manager.read_session(name)
    else:
        session = manager.session(name)
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
    async with session_scope(name, read=True) as session:
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

    # Read-after-write: everything after this write in the same request
    # context reads the primary, never a possibly-lagging replica.
    _read_pinned.set(True)
    async with session_scope(name) as session:
        result = await action(session, *args, **kwargs)
        await session.commit()
        return result
