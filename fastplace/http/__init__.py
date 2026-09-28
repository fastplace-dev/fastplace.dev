"""Fastplace HTTP layer — the public surface application code touches.

Re-exports routing, request/response, the bridge renderer, middleware, and
lifecycle APIs. Application code imports ``fastplace.http`` and never
``fastapi``/``starlette`` directly (escape hatches excepted — ADR-006).
"""

from fastplace.http import lifecycle  # noqa: F401  (module-level API)
from fastplace.http.authorize import authorize
from fastplace.http.flash import FLASH_SESSION_KEY, flash
from fastplace.http.kernel import (
    AI_PREFIX,
    API_PREFIX,
    create_app,
    get_app,
    middleware_from_config,
)
from fastplace.http.middleware import Middleware
from fastplace.http.render import render, reset_shared_props, share
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
    "FLASH_SESSION_KEY",
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
    "authorize",
    "create_app",
    "flash",
    "get_app",
    "lifecycle",
    "middleware_from_config",
    "render",
    "reset_shared_props",
    "share",
    "to_response",
]
