"""Shared Rivals API client settings for profiles and scrim helpers."""

from __future__ import annotations

import os


def client_options() -> dict:
    """Read settings when constructing a client, after the bot loads .env."""
    return {
        "timeout": float(os.getenv("RIVALS_API_TIMEOUT", "20")),
        "enrich": os.getenv("RIVALS_API_ENRICH", "true").strip().lower() in {"true", "1", "yes", "on"},
        "use_browser_fallback": os.getenv("RIVALS_API_BROWSER_FALLBACK", "false").strip().lower()
        in {"true", "1", "yes", "on"},
    }
