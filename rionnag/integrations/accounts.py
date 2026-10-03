"""Resolve a game account independently of the membership workflow."""

import asyncio

from rivals_api import RivalsClient

from .rivals import queued_lookup
from .rivals_config import client_options


def _verify(username):
    with RivalsClient(**client_options()) as client:
        player = client.get_player(username)
        return {"uid": str(player.uid), "name": player.name or username}


async def verify_account(game, username):
    if game != "marvel-rivals":
        return {"uid": username.strip().casefold(), "name": username.strip()}
    try:
        return await asyncio.to_thread(queued_lookup, _verify, username)
    except Exception as exc:
        raise ValueError(
            "Could not verify your public Marvel Rivals account. Check the username/UID and try again."
        ) from exc
