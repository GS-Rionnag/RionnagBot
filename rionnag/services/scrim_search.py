"""Saved opponent-rank searches, shared by the automatic log and preview."""

from rionnag.scrims.scrim_offer_rules import RANKS, canonical_rank


def rank_bounds(value):
    rank = canonical_rank(value)
    if rank is None:
        raise ValueError("Choose a valid Marvel Rivals rank, for example Diamond or GM.")
    tier = next(i for i, name in enumerate(RANKS) if rank == name or rank.startswith(name + " "))
    division = rank.split()[-1]
    if division in {"III", "II", "I"}:
        position = tier * 3 + {"III": 0, "II": 1, "I": 2}[division]
        return position, position
    return tier * 3, tier * 3 + 2


def normalize_filter(min_rank, max_rank=None):
    if min_rank.strip().casefold() in {"any", "all"}:
        if max_rank:
            raise ValueError("Leave max_rank empty when searching Any rank.")
        return None
    minimum, maximum = canonical_rank(min_rank), canonical_rank(max_rank or min_rank)
    low, high = rank_bounds(min_rank)[0], rank_bounds(max_rank or min_rank)[1]
    if low > high:
        raise ValueError("min_rank must be below or equal to max_rank.")
    return minimum, maximum


def rank_matches(offer, ranks):
    if ranks is None:
        return True
    try:
        low = rank_bounds(offer.get("rank_minimum"))[0]
        high = rank_bounds(offer.get("rank_maximum"))[1]
        wanted_low, wanted_high = rank_bounds(ranks[0])[0], rank_bounds(ranks[1])[1]
    except ValueError:
        return False
    return wanted_low <= low <= high <= wanted_high


def rank_suggestions(current):
    query = current.strip().casefold()
    canonical = canonical_rank(current)
    values = ["Any", *RANKS]
    for name in RANKS[:-2]:
        values.extend(f"{name} {division}" for division in ("III", "II", "I"))
    matches = [v for v in values if query in v.casefold() or (canonical and v.startswith(canonical))]
    return matches[:25]
