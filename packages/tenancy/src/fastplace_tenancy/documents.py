"""MongoDB isolation — automatic ``company_id`` filters on Document models.

The document adapter is a separate surface from the relational ORM, so the
package covers it with its own base (blueprint §8: "application-level
tenant filters"). :class:`CompanyDocument` mirrors what
``CompanyScopedModel`` does for tables:

- every query (``where``/``find``/``first``/``count``) intersects the filter
  with ``{"company_id": <bound>}`` — the immutable ``DocumentQuery`` ANDs it
  through ``$and``, so a caller's later ``where()`` narrows, never replaces;
- ``create``/``insert`` stamp the tenant from the bound context;
- ``company_id`` is guarded from mass assignment and from ``update()``
  (a payload cannot move a document into another company).
"""

from __future__ import annotations

from typing import Any, ClassVar

from fastplace.orm.documents import Document, DocumentQuery
from fastplace_tenancy.context import require_company_context


def _contains_company_id(value: Any) -> bool:
    """Recursively scan an update document for tenant-column writes.

    Mongo update operators nest (``$set``, ``$inc``, array filters, plain
    sub-documents) — a ``company_id`` at any depth is a tenant move.
    """
    if isinstance(value, dict):
        return any(key == "company_id" or _contains_company_id(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_company_id(item) for item in value)
    return False


class TenantDocumentQuery(DocumentQuery):
    """``DocumentQuery`` that refuses tenant-column writes.

    ``update()`` bypasses mass assignment entirely — the update document goes
    straight to the wire — so the query type itself must guard it.
    """

    async def update(self, update: dict[str, Any]) -> int:
        if _contains_company_id(update):
            raise ValueError(
                "company_id is guarded — update() cannot move a document between companies"
            )
        return await super().update(update)


class CompanyDocument(Document):
    """Base for company-owned collections — the tenant column is automatic."""

    #: Never mass-assignable, mirroring the relational base.
    __guarded__: ClassVar[tuple[str, ...]] = ("company_id",)
    #: ``where()`` builds through this type, so update() inherits the guard.
    __query_class__: ClassVar[type[DocumentQuery]] = TenantDocumentQuery

    def __init_subclass__(cls, **kwargs: Any) -> None:
        # Same force-merge as the relational base: a subclass's own
        # __guarded__ narrows what IT added, never the tenant protection.
        own = cls.__dict__.get("__guarded__", ())
        cls.__guarded__ = ("company_id", *(g for g in own if g != "company_id"))
        super().__init_subclass__(**kwargs)

    @classmethod
    def _tenant_filter(cls) -> dict[str, Any]:
        return {"company_id": require_company_context()}

    @classmethod
    def where(cls, filter: dict[str, Any] | None = None, /, **equality) -> DocumentQuery:
        # Chained, not dict-merged: the tenant criterion rides the query
        # engine's $and, so a caller's company_id condition (an equality or
        # an operator like $ne) is intersected — never silently replaced by
        # the tenant equality. find()/first()/count() all build through
        # here, so they inherit the intersection for free.
        return super().where(filter, **equality).where(cls._tenant_filter())

    @classmethod
    async def create(cls, **data: Any) -> Document:
        """Stamp the tenant after mass assignment (the context is the only source).

        Passing ``company_id`` explicitly raises :class:`MassAssignmentError`
        through the normal guard path — symmetric with ``CompanyScopedModel``.
        """
        company_id = require_company_context()
        if "company_id" in data:
            return await super().create(**data)  # guard raises with the standard error
        payload = cls._build_payload(cls._mass_assignable(data))
        payload["company_id"] = company_id  # framework stamp, not mass assignment
        result = await cls._mongo_collection().insert_one(payload)
        return cls(**{**payload, "_id": result.inserted_id})

    @classmethod
    async def insert(cls, documents: list[dict[str, Any]]) -> list[Any]:
        """Insert many, each stamped with the bound company."""
        company_id = require_company_context()
        payloads = []
        for doc in documents:
            if "company_id" in doc:
                # Guarded column — force the standard error path.
                return await super().insert(documents)
            payloads.append(cls._build_payload(cls._mass_assignable(doc)))
        for payload in payloads:
            payload["company_id"] = company_id
        result = await cls._mongo_collection().insert_many(payloads)
        return list(result.inserted_ids)
