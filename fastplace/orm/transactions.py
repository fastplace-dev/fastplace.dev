"""``async with db.transaction():`` — first-class transaction scopes (blueprint §8).

Services open the boundary; repositories execute work inside it. Nested scopes
on the same connection become savepoints automatically. A nested scope on a
*different* named connection is an independent transaction — two physical
databases cannot share one atomic unit, so it commits or rolls back on its own.
"""

from __future__ import annotations

from contextlib import asynccontextmanager


@asynccontextmanager
async def transaction(name: str = "default"):
    """Open a transaction scope; nested same-connection scopes use savepoints."""
    from fastplace.orm.session import ambient, mark_owns_commit, session_scope

    state = ambient()
    if state is not None and state.name == name:
        # Savepoint inside the enclosing scope; the outer scope owns commit.
        # Per-write commits are suspended so a savepoint rollback still works
        # even when the outer scope is a non-owning db.connection().
        async with state.session.begin_nested():
            with mark_owns_commit():
                yield state.session
        return

    async with session_scope(name) as session:
        async with session.begin():
            with mark_owns_commit():
                yield session
