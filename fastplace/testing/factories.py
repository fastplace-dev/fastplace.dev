"""Model factories — sensible defaults for ORM entities (sweep-G13).

Subclass, point ``model`` at an entity, and describe a realistic default
shape in ``definition()``; tests override only what they care about.
"""

from __future__ import annotations

from typing import Any, ClassVar


class ModelFactory:
    """Base factory for any :class:`~fastplace.orm.Model` subclass.

    ``make`` builds an unsaved instance, ``create``/``create_many`` persist
    through the model's ordinary ``save()`` path (events and fillable rules
    apply). ``sequence(prefix)`` yields per-class increasing strings, so two
    defaults that must differ (emails, slugs) never collide.
    """

    model: ClassVar[type | None] = None
    _sequence_counter: ClassVar[int] = 0

    def __init__(self) -> None:
        # Not a narrowing guard for this class: a subclass that forgot to
        # set `model` fails here, loudly, instead of calling None.
        if self.model is None:
            raise TypeError(
                f"{type(self).__name__} must declare a `model` attribute "
                "pointing at a fastplace.orm.Model subclass"
            )
        self._model: type = self.model

    def sequence(self, prefix: str = "") -> str:
        """The next per-class sequence value: ``sequence("user")`` -> ``user1``."""
        type(self)._sequence_counter += 1
        return f"{prefix}{type(self)._sequence_counter}"

    async def definition(self) -> dict[str, Any]:
        """The default attribute shape; override in the subclass."""
        return {}

    async def make(self, **overrides: Any) -> Any:
        """Build an unsaved instance: defaults, overridden."""
        values = {**await self.definition(), **overrides}
        return self._model(**values)

    async def create(self, **overrides: Any) -> Any:
        """Build and persist one instance."""
        instance = await self.make(**overrides)
        await instance.save()
        return instance

    async def create_many(self, count: int, **overrides: Any) -> list[Any]:
        """Build and persist ``count`` instances (sequences advance per item)."""
        return [await self.create(**overrides) for _ in range(count)]


__all__ = ["ModelFactory"]
