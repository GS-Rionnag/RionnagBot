from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
GUILD_ID = 1554260744327929987
OWNER_ROLE_ID = 1555657055287648326
VISITOR_ROLE_ID = 1554272420959879188
APPLICATIONS_CATEGORY_ID = 1555725074693099580
ENTRY_CHANNEL_ID = 1555730197939101737
SCRIM_OPPORTUNITIES_CHANNEL_ID = 1555989494451146802
COLOR = 0x8000FF
DATABASE = ROOT / "data" / "rionnag.sqlite3"
TOKEN = os.getenv("DISCORD_TOKEN", "")


def load_forms(path: Path = ROOT / "config" / "forms.json") -> dict:
    forms = json.loads(path.read_text(encoding="utf-8"))
    if not forms or len(forms) > 20:
        raise ValueError("Configure between 1 and 20 games.")
    for game in forms.values():
        keys = [q["key"] for q in game["questions"]]
        if len(keys) != len(set(keys)) or not keys or game["version"] < 1:
            raise ValueError("Form keys must be unique and versions positive.")
        for q in game["questions"]:
            if len(q["label"]) > 45 or not 1 <= q.get("max_length", 100) <= 1000:
                raise ValueError("Question labels must fit Discord; answers are limited to 1000 characters.")
    return forms
