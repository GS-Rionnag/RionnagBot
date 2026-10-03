"""Strict, shared validation for Marvel Rivals opportunities."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

RANKS = (
    "Bronze",
    "Silver",
    "Gold",
    "Platinum",
    "Diamond",
    "Grandmaster",
    "Celestial",
    "Eternity",
    "One Above All",
)
ALIASES = {rank.lower(): rank for rank in RANKS}
ALIASES.update(
    {
        "gm": "Grandmaster",
        "dia": "Diamond",
        "plat": "Platinum",
        "cel": "Celestial",
        "oa": "One Above All",
        "oaa": "One Above All",
    }
)
RANK_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(ALIASES, key=len, reverse=True)) + r")(?:\s*([123]|III|II|I))?\b",
    re.IGNORECASE,
)


def canonical_rank(value):
    if not isinstance(value, str):
        return None
    match = RANK_PATTERN.fullmatch(value.strip())
    if not match:
        return None
    rank = ALIASES[match[1].lower()]
    division = match[2]
    if division and rank in {"Eternity", "One Above All"}:
        return None
    if division:
        division = {"1": "I", "2": "II", "3": "III"}.get(division, division.upper())
        return f"{rank} {division}"
    return rank


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.utcoffset() is None:
            return None
        return int(date.timestamp())
    except (ValueError, OverflowError, OSError):
        return None


def offer_lifecycle(offer, now=None, day_timezone="America/New_York"):
    now = datetime.now(UTC).timestamp() if now is None else now
    start = timestamp(offer.get("Start_Time_timestamp", offer.get("starts_at")))
    end = timestamp(offer.get("End_Time_timestamp", offer.get("ends_at")))
    if start is None:
        return "unknown", None
    try:
        zone = ZoneInfo(offer.get("timezone") or day_timezone)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo(day_timezone)
    local = datetime.fromtimestamp(end if end is not None else start, zone)
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), zone).timestamp()
    status = "upcoming" if now < start else "started"
    if end is not None and now >= end:
        status = "finished"
    return status, int(midnight)


def eastern_local_times(offer, source):
    """Treat informal EST as Eastern local time; correct only evidenced fixed-offset mistakes."""
    text = source.get("content", "") + "\n" + json.dumps(source.get("embeds", []), ensure_ascii=False)
    if not re.search(r"\bEST\b", text, re.I) or re.search(
        r"<t:|UTC\s*-\s*0?5|GMT\s*-\s*0?5|fixed|standard\s+time",
        text,
        re.I,
    ):
        return offer
    clocks = set()
    for match in re.finditer(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text, re.I):
        hour = int(match[1]) % 12 + (12 if match[3].lower() == "pm" else 0)
        clocks.add((hour, int(match[2] or 0)))
    for match in re.finditer(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*[-–]\s*"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
        text,
        re.I,
    ):
        for hour, minute, period in (
            (match[1], match[2], match[3] or match[6]),
            (match[4], match[5], match[6]),
        ):
            if period:
                clocks.add((int(hour) % 12 + (12 if period.lower() == "pm" else 0), int(minute or 0)))
            elif 1 <= int(hour) <= 12:
                clocks.update(((int(hour) % 12, int(minute or 0)), (int(hour) % 12 + 12, int(minute or 0))))
    result = dict(offer)
    zone = ZoneInfo("America/New_York")
    for key in ("Start_Time_timestamp", "End_Time_timestamp"):
        instant = timestamp(result.get(key))
        if instant is None:
            continue
        actual = datetime.fromtimestamp(instant, zone)
        fixed = datetime.fromtimestamp(instant, timezone(timedelta(hours=-5)))
        if (actual.hour, actual.minute) not in clocks and (fixed.hour, fixed.minute) in clocks:
            corrected = fixed.replace(tzinfo=zone).astimezone(UTC)
            result[key] = corrected.isoformat().replace("+00:00", "Z")
    return result


def validate_offer(offer, source):
    """Hard-check the four model fields against the actual Discord message."""
    offer = eastern_local_times(offer, source)
    evidence = source.get("content", "") + "\n" + json.dumps(source.get("embeds", []), ensure_ascii=False)
    if re.search(r"overwatch\s+ranks?|not\s+(?:set|ranked).*rivals", evidence, re.I):
        return None
    minimum, maximum = (canonical_rank(offer.get(key)) for key in ("rank_minimum", "rank_maximum"))
    ranks_in_quote = [canonical_rank(match[0]) for match in RANK_PATTERN.finditer(evidence)]
    if not minimum or not maximum or minimum not in ranks_in_quote or maximum not in ranks_in_quote:
        return None
    start = timestamp(offer.get("Start_Time_timestamp"))
    if start is None:
        return None
    discord_times = [int(value) for value in re.findall(r"<t:(\d+)(?::[tTdDfFR])?>", evidence)]
    if discord_times:
        if start not in discord_times:
            return None
    elif not re.search(
        r"\b(?:UTC|GMT|EST|EDT|ET|CST|CDT|CT|MST|MDT|MT|PST|PDT|PT|BST|CET|CEST|"
        r"EET|EEST|IST|JST|KST|AEST|AEDT|ACST|NZST|NZDT)\b|"
        r"[+-]\d{2}:\d{2}|\b[A-Za-z_]+/[A-Za-z_]+\b",
        evidence,
        re.I,
    ):
        return None  # A wall-clock time without an advertised zone is not an exact instant.
    end = timestamp(offer.get("End_Time_timestamp")) if offer.get("End_Time_timestamp") else None
    if offer.get("End_Time_timestamp") and (end is None or end <= start):
        return None
    if discord_times and end is not None and end not in discord_times:
        return None

    def rank_order(rank):
        tier = next(i for i, name in enumerate(RANKS) if rank == name or rank.startswith(name + " "))
        division = rank.split()[-1]
        return tier, {"III": 0, "II": 1, "I": 2}.get(division, 1)

    minimum, maximum = sorted((minimum, maximum), key=rank_order)
    return {
        "rank_minimum": minimum,
        "rank_maximum": maximum,
        "Start_Time_timestamp": datetime.fromtimestamp(start, UTC).isoformat().replace("+00:00", "Z"),
        "End_Time_timestamp": datetime.fromtimestamp(end, UTC).isoformat().replace("+00:00", "Z")
        if end is not None
        else None,
    }


def compact_offer(offer, source=None):
    source = source or offer.get("source", {})

    def utc_text(value):
        instant = timestamp(value)
        return (
            datetime.fromtimestamp(instant, UTC).isoformat().replace("+00:00", "Z")
            if instant is not None
            else None
        )

    return {
        "rank_minimum": offer.get("rank_minimum", offer.get("rank_min")),
        "rank_maximum": offer.get("rank_maximum", offer.get("rank_max")),
        "Start_Time_timestamp": utc_text(offer.get("Start_Time_timestamp", offer.get("starts_at"))),
        "End_Time_timestamp": utc_text(offer.get("End_Time_timestamp", offer.get("ends_at"))),
        "authorID": source.get("author_id", offer.get("authorID")),
        "messageURL": source.get("url", offer.get("messageURL")),
        "messageContent": source.get("content", offer.get("messageContent", "")),
    }
