"""Humanized per-field 422 messages from request.validate (no raw pydantic strings)."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, Field, field_validator

from fastplace.errors import ValidationError
from fastplace.http.request import Request


class _JsonBody:
    """Minimal stand-in for the Starlette request underneath Request."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.headers = {"content-type": "application/json"}

    def header(self, name: str, default: str | None = None) -> str | None:
        if name.lower() == "content-type":
            return "application/json"
        return default

    async def form(self):  # pragma: no cover - never taken for JSON bodies
        raise AssertionError("form() must not run for JSON bodies")

    async def body(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class Signup(BaseModel):
    name: str = Field(min_length=3, max_length=50)
    email: str

    @field_validator("email")
    @classmethod
    def _email_shape(cls, value: str) -> str:
        if "@" not in value:
            raise ValueError("value is not a valid email address: missing @-sign")
        return value


class Age(BaseModel):
    age: int


async def validate(payload: dict, schema: type[BaseModel]) -> ValidationError:
    with pytest.raises(ValidationError) as excinfo:
        await Request(_JsonBody(payload)).validate(schema)
    return excinfo.value


async def test_missing_field_is_required():
    exc = await validate({"name": "Firoz"}, Signup)
    assert exc.errors["email"] == ["The email field is required."]
    assert exc.message == "The given data was invalid."


async def test_too_short_maps_to_minimum_characters():
    exc = await validate({"name": "ab", "email": "a@b.test"}, Signup)
    assert exc.errors["name"] == ["The name must be at least 3 characters."]


async def test_too_long_maps_to_maximum_characters():
    exc = await validate({"name": "x" * 51, "email": "a@b.test"}, Signup)
    assert exc.errors["name"] == ["The name may not be greater than 50 characters."]


async def test_email_error_maps_to_valid_email_sentence():
    exc = await validate({"name": "Firoz", "email": "notanemail"}, Signup)
    assert exc.errors["email"] == ["The email field must be a valid email address."]


async def test_unmapped_types_fall_back_to_a_cleaned_pydantic_string():
    exc = await validate({"age": "soon"}, Age)
    assert exc.errors["age"] == [
        "Input should be a valid integer, unable to parse string as an integer."
    ]


async def test_service_validator_messages_survive_the_value_error_prefix():
    class Pair(BaseModel):
        password: str
        confirm: str

        @field_validator("confirm")
        @classmethod
        def _match(cls, value: str) -> str:
            raise ValueError("The password confirmation does not match.")

    exc = await validate({"password": "s3cret", "confirm": "other"}, Pair)
    assert exc.errors["confirm"] == ["The password confirmation does not match."]


async def test_envelope_still_keys_errors_by_field_path():
    exc = await validate({}, Signup)
    assert set(exc.errors) == {"name", "email"}
