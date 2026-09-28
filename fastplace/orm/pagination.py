"""Paginators — ``.paginate()`` page numbers and ``.cursor_paginate()`` keysets."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from fastplace.errors import ConfigurationError


class Paginator:
    """Fluent paginator with JSON-safe serialization."""

    def __init__(
        self,
        items: list[Any],
        *,
        total: int,
        per_page: int,
        current_page: int,
    ) -> None:
        self.items = items
        self.total = total
        self.per_page = per_page
        self.current_page = current_page
        self.last_page = max(1, -(-total // per_page)) if per_page else 1

    @property
    def has_pages(self) -> bool:
        return self.last_page > 1

    @property
    def has_more(self) -> bool:
        return self.current_page < self.last_page

    @property
    def on_first_page(self) -> bool:
        return self.current_page <= 1

    @property
    def on_last_page(self) -> bool:
        return self.current_page >= self.last_page

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index):
        return self.items[index]

    def to_dict(self) -> dict:
        items = [item.to_dict() if hasattr(item, "to_dict") else item for item in self.items]
        return {
            "items": items,
            "total": self.total,
            "per_page": self.per_page,
            "current_page": self.current_page,
            "last_page": self.last_page,
            "has_more": self.has_more,
            "has_pages": self.has_pages,
        }


class CursorPaginator:
    """One keyset page — an opaque cursor instead of page numbers.

    No COUNT query runs; ``next_cursor`` is ``None`` on the last page.
    """

    def __init__(self, items: list[Any], *, next_cursor: str | None, has_more: bool) -> None:
        self.items = items
        self.next_cursor = next_cursor
        self.has_more = has_more

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index):
        return self.items[index]

    def to_dict(self) -> dict:
        items = [item.to_dict() if hasattr(item, "to_dict") else item for item in self.items]
        return {"items": items, "next_cursor": self.next_cursor, "has_more": self.has_more}


def encode_cursor(values: list[Any], fingerprint: str | None = None) -> str:
    """Pack keyset values into an opaque, URL-safe token.

    ``fingerprint`` (when given) binds the token to the sort that minted it
    — a cursor replayed under a changed ``order_by`` is rejected on decode
    instead of silently duplicating or truncating the walk.
    """
    raw = json.dumps({"f": fingerprint, "v": values}, separators=(",", ":"), default=str)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str, fingerprint: str | None = None) -> list[Any]:
    """Unpack an opaque token; anything unreadable is a configuration error,
    never a silent first-page restart (that would loop clients forever).

    A ``fingerprint`` mismatch (cursor minted under a different sort) is the
    same loud error, not a best-effort page.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw)
    except (binascii.Error, UnicodeEncodeError, ValueError, json.JSONDecodeError):
        raise ConfigurationError("cursor_paginate received a malformed cursor") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("v"), list):
        raise ConfigurationError("cursor_paginate received a malformed cursor")
    if fingerprint is not None and payload.get("f") != fingerprint:
        raise ConfigurationError("cursor does not match the sort order")
    return list(payload["v"])
