"""Shared Rivals API client settings for profiles and scrim helpers."""

from __future__ import annotations

import os


def client_options() -> dict:
    """Read settings when constructing a client, after the bot loads .env."""
    return {
        "timeout": float(os.getenv("RIVALS_API_TIMEOUT", "20")),
        "request_interval": float(os.getenv("RIVALS_API_REQUEST_INTERVAL", "3")),
        "rate_limit_cooldown": float(os.getenv("RIVALS_API_RATE_LIMIT_COOLDOWN", "60")),
        "enrich": os.getenv("RIVALS_API_ENRICH", "true").strip().lower() in {"true", "1", "yes", "on"},
        "use_browser_fallback": os.getenv("RIVALS_API_BROWSER_FALLBACK", "true").strip().lower()
        in {"true", "1", "yes", "on"},
    }
