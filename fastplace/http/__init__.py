"""Fastplace HTTP layer — the public surface application code touches.

Re-exports routing, request/response, the bridge renderer, middleware, and
lifecycle APIs. Application code imports ``fastplace.http`` and never
``fastapi``/``starlette`` directly (escape hatches excepted — ADR-006).
"""

from fastplace.http import lifecycle  # noqa: F401  (module-level API)
from fastplace.http.flash import flash
from fastplace.http.kernel import AI_PREFIX, API_PREFIX, create_app, get_app
from fastplace.http.middleware import Middleware
from fastplace.http.render import render
from fastplace.http.request import Request
from fastplace.http.response import (
    File,
    Html,
    Json,
    NoContent,
    Redirect,
    Response,
    Stream,
    Text,
    to_response,
)
from fastplace.http.router import Controller, Route, Router
from fastplace.http.urls import build_absolute_url
from fastplace.http.websocket import WebSocket

__all__ = [
    "API_PREFIX",
    "AI_PREFIX",
    "Controller",
    "File",
    "Html",
    "Json",
    "Middleware",
    "NoContent",
    "Redirect",
    "Request",
    "Response",
    "build_absolute_url",
    "Route",
    "Router",
    "Stream",
    "Text",
    "WebSocket",
    "create_app",
    "flash",
    "get_app",
    "lifecycle",
    "render",
    "to_response",
]
