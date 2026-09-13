"""Shared Rich console for CLI output."""

from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

_theme = Theme(
    {
        "info": "cyan",
        "success": "bold green",
        "warning": "bold yellow",
        "error": "bold red",
        "fastplace": "bold magenta",
    }
)

console = Console(theme=_theme)
err_console = Console(stderr=True, theme=_theme)
