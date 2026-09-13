"""Paginator — the object returned by ``.paginate(per_page)``."""

from __future__ import annotations

from typing import Any


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
