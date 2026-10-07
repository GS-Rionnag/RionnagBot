"""Resolve a game account independently of the membership workflow."""

import asyncio
import threading

from rivals_api import RivalsClient

from .rivals_config import client_options

identity_slots = threading.BoundedSemaphore(3)


def queued_identity(function, *args):
    """Keep short identity requests independent of the full stats queue.

    Each SDK client still enforces its configured request pacing and cooldowns.
    """
    with identity_slots:
        return function(*args)


def _verify(username):
    with RivalsClient(**client_options()) as client:
        player = client.get_player(username)
        return {"uid": str(player.uid), "name": player.name or username}


async def verify_account(game, username):
    if game != "marvel-rivals":
        return {"uid": username.strip().casefold(), "name": username.strip()}
    try:
        return await asyncio.to_thread(queued_identity, _verify, username)
    except Exception as exc:
        raise ValueError(
            "Could not verify your public Marvel Rivals account. Check the username/UID and try again."
        ) from exc
