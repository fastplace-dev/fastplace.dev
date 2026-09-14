"""``async with db.transaction():`` — first-class transaction scopes (blueprint §8).

Services open the boundary; repositories execute work inside it. Nested scopes
on the same connection become savepoints automatically. A nested scope on a
*different* named connection is an independent transaction — two physical
databases cannot share one atomic unit, so it commits or rolls back on its own.

Domain events dispatched inside the scope (``__dispatches__`` → queue) are
buffered on the scope and only enqueued after the commit; a rollback discards
them, so a job can never reference a row that never existed.
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
        # even when the outer scope is a non-owning db.connection(). Buffered
        # domain events stay on the outer scope — they flush (or vanish) with
        # the outer commit, not with this savepoint.
        async with state.session.begin_nested():
            with mark_owns_commit():
                yield state.session
        return

    async with session_scope(name) as session:
        scope = ambient()
        try:
            async with session.begin():
                with mark_owns_commit():
                    yield session
        except BaseException:
            from fastplace.events import discard_deferred_domain_events

            if scope is not None:
                discard_deferred_domain_events(scope)
            raise
        else:
            from fastplace.events import flush_deferred_domain_events

            if scope is not None:
                await flush_deferred_domain_events(scope)
