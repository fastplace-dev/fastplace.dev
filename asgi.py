"""ASGI entry point — ``uvicorn asgi:app`` (used by run dev / serve)."""

from fastplace.http import create_app

app = create_app()
