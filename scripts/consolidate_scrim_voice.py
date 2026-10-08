"""Owner-authorized, one-time consolidation; run with the shared service stopped."""

import argparse
import asyncio
import json
import sqlite3
from contextlib import closing

import discord

from rionnag import config
from rionnag.instance import SingleInstance


async def consolidate(client, remove_id):
    guild = client.get_guild(config.GUILD_ID)
    if guild is None:
        raise RuntimeError("Configured guild is unavailable")
    with closing(sqlite3.connect(config.DATABASE)) as db:
        row = db.execute(
            "SELECT control_id,waiting_id,stage_id FROM scrim_config WHERE guild_id=? AND game=?",
            (guild.id, "Marvel Rivals"),
        ).fetchone()
        if not row:
            raise RuntimeError("No saved Marvel Rivals scrim configuration")
        _, keep_id, old_id = row
        if keep_id == old_id:
            if keep_id == remove_id:
                raise RuntimeError("Cannot remove the retained voice room")
            retired = guild.get_channel(remove_id)
            retained = guild.get_channel(keep_id)
            if isinstance(retired, discord.VoiceChannel) and isinstance(retained, discord.VoiceChannel):
                for member in list(retired.members):
                    await member.move_to(retained, reason="Finish one-time scrim room consolidation")
                await retired.delete(reason="Finish owner-authorized scrim voice consolidation")
            print("Scrim voice is already consolidated")
            return
        if old_id != remove_id or keep_id == remove_id:
            raise RuntimeError("Removal confirmation does not match the saved extra voice room")
        keep, old = guild.get_channel(keep_id), guild.get_channel(old_id)
        if not isinstance(keep, discord.VoiceChannel):
            raise RuntimeError("Retained voice room is unavailable")
        if old is not None and not isinstance(old, discord.VoiceChannel):
            raise RuntimeError("Removal target is not a voice room")
        # Preserve private runtime state before changing channel mappings.
        backup = config.ROOT / "data" / "before_single_scrim_voice.sqlite3"
        if not backup.exists():
            with closing(sqlite3.connect(backup)) as destination:
                db.backup(destination)
        saved = [json.loads(raw) for (raw,) in db.execute(
            "SELECT data FROM scrim_sessions WHERE guild_id=? ORDER BY id", (guild.id,)
        )]
        original_mutes = {}
        for session in saved:
            original_mutes.update(session.get("voice_mutes_before", {}))
        for member_id, muted in original_mutes.items():
            member = guild.get_member(int(member_id))
            if member and member.voice and member.voice.channel and member.voice.mute != muted:
                await member.edit(mute=muted, reason="Restore voice state after removing scrim voice control")
        role_ids = {config.VISITOR_ROLE_ID}
        for form in config.load_forms().values():
            if form["name"] == "Marvel Rivals":
                role_ids.update(form[key] for key in ("team_role", "tryout_role", "manager_role"))
        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role:
                overwrite = keep.overwrites_for(role)
                overwrite.view_channel = overwrite.connect = overwrite.speak = True
                await keep.set_permissions(role, overwrite=overwrite, reason="One shared scrim voice room")
        if old:
            await keep.edit(name=old.name, reason="Keep the waiting room as the only scrim voice room")
            for member in list(old.members):
                await member.move_to(keep, reason="One-time consolidation into the retained scrim room")
        # Write all mappings together before deleting the retired channel.
        db.execute(
            "UPDATE scrim_config SET stage_id=waiting_id WHERE guild_id=? AND game=?",
            (guild.id, "Marvel Rivals"),
        )
        for table in ("scrim_lobbies", "scrim_sessions"):
            for record_id, raw in db.execute(
                f"SELECT id,data FROM {table} WHERE guild_id=? AND game=?",
                (guild.id, "Marvel Rivals"),
            ).fetchall():
                data = json.loads(raw)
                if table == "scrim_sessions" and data["status"] == "ended":
                    continue
                data.update(waiting_id=keep_id, stage_id=keep_id)
                for key in ("voice_mutes_before", "overwrites_before", "admission_ids", "sync_error"):
                    data.pop(key, None)
                db.execute(f"UPDATE {table} SET data=? WHERE id=?", (json.dumps(data), record_id))
        db.commit()
        if old:
            await old.delete(reason="Owner requested removal of the extra scrim voice room")
        print("Retained scrim room renamed; extra room removed; tracking data preserved")


async def main(remove_id):
    intents = discord.Intents(guilds=True, members=True, voice_states=True)
    client = discord.Client(intents=intents)
    completed = asyncio.get_running_loop().create_future()

    @client.event
    async def on_ready():
        if completed.done():
            return
        try:
            await consolidate(client, remove_id)
        except Exception as exc:
            completed.set_exception(exc)
        else:
            completed.set_result(None)
        finally:
            await client.close()

    async with client:
        await client.start(config.TOKEN)
    await completed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-remove", required=True, type=int)
    args = parser.parse_args()
    with SingleInstance(config.ROOT / "data" / "bot.lock"):
        asyncio.run(main(args.confirm_remove))
