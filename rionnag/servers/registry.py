"""Explicit server registry: adding a server never enables another server's features."""

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

from rionnag import config
from rionnag.servers.third_eye.config import GUILD_ID as THIRD_EYE_GUILD_ID


@dataclass(frozen=True)
class ServerProfile:
    key: str
    guild_id: int
    module: str
    message_content: bool = False

    @property
    def data_dir(self) -> Path:
        return config.ROOT / "data" / "servers" / self.key


PROFILES = (
    ServerProfile("rionnag", config.GUILD_ID, "rionnag.servers.rionnag"),
    ServerProfile("third_eye", THIRD_EYE_GUILD_ID, "rionnag.servers.third_eye", message_content=True),
)


async def load_servers(bot, profiles=PROFILES):
    if len({p.guild_id for p in profiles}) != len(profiles):
        raise ValueError("Each server must have exactly one profile.")
    if len({p.key for p in profiles}) != len(profiles):
        raise ValueError("Server profile keys must be unique.")
    for profile in profiles:
        await import_module(profile.module).setup(bot, profile)
    if bot.tree.get_commands():
        raise RuntimeError("Server modules must register guild commands, never global commands.")
