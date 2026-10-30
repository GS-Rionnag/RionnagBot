"""In-memory uwu transformation and URL-only detection."""

import re
from urllib.parse import urlsplit

import uwuify


def transform(text: str) -> str:
    return uwuify.uwu(text, flags=uwuify.SMILEY | uwuify.YU | uwuify.STUTTER) if text else ""


def only_urls(text: str) -> bool:
    parts = text.split()
    if not parts:
        return False
    for part in parts:
        if part.startswith("<") and part.endswith(">"):
            part = part[1:-1]
        if not re.fullmatch(r"(?:https?://|www\.)[^\s<>]+", part, re.IGNORECASE):
            return False
        try:
            if not urlsplit(part if "://" in part else "https://" + part).hostname:
                return False
        except ValueError:
            return False
    return True
