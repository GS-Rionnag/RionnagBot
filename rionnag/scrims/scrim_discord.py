"""Persistent scrim controls with automatic Rivals Data game detection."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import sqlite3
from dataclasses import asdict

import discord
from discord import app_commands

from rionnag.scrims import scrim_rivals
from rionnag.scrims.scrim_monitor import ScrimMonitor
from rionnag.scrims.scrim_results import match_outcome
from rionnag.scrims.scrims import (
    ROLES,
    Player,
    ScrimStore,
    manual_lineup,
    normalized_roles,
    random_team,
    reconcile_lobby,
    reroll_team,
    substitute_one,
)

logger = logging.getLogger(__name__)


class ScrimPanel(discord.ui.View):
    def __init__(self, controller, lobby, session=None):
        super().__init__(timeout=None)
        revision = session.get("revision", 0) if session else lobby["revision"]
        sid = session["id"] if session else 0
        if session:
            playing = session["status"] == "playing"
            controls = [
                ("Sub in", "sub", discord.ButtonStyle.primary, playing or session["number"] == 1),
                ("Reroll teams", "reroll", discord.ButtonStyle.secondary, playing),
                ("Edit lineup", "edit", discord.ButtonStyle.secondary, playing),
            ]
            if session.get("sync_error"):
                controls.append(("Sync voice", "sync", discord.ButtonStyle.secondary, False))
            pages = max(1, (len(controller.store.session_matches(session["id"])) + 24) // 25)
            page = min(session.get("history_page", 0), pages - 1)
            if page < pages - 1:
                controls.append(("Older games", "older", discord.ButtonStyle.secondary, False))
            if page:
                controls.append(("Newer games", "newer", discord.ButtonStyle.secondary, False))
        else:
            controls = [
                ("Start scrim", "begin", discord.ButtonStyle.primary, len(lobby["queue_ids"]) < 6),
                (
                    "Test",
                    "test",
                    discord.ButtonStyle.secondary,
                    lobby.get("test_owner_id") not in lobby["queue_ids"],
                ),
            ]
        for label, action, style, disabled in controls:
            button = discord.ui.Button(
                label=label,
                style=style,
                disabled=disabled,
                custom_id=f"scrimq:{lobby['id']}:{sid}:{revision}:{action}",
            )

            async def callback(interaction, selected=action):
                await controller.action(interaction, lobby["id"], sid, revision, selected)

            button.callback = callback
            self.add_item(button)


class LineupEditor(discord.ui.Modal, title="Edit scrim lineup"):
    def __init__(self, controller, lobby, data):
        super().__init__(timeout=300)
        self.controller, self.lid, self.sid = controller, lobby["id"], data["id"]
        self.revision = data.get("revision", 0)
        self.operation = discord.ui.Select(
            options=[
                discord.SelectOption(
                    label="Force in", value="in", description="Bring a substitute into the starting six"
                ),
                discord.SelectOption(
                    label="Force out", value="out", description="Bench a starter and fill their role"
                ),
                discord.SelectOption(
                    label="Swap two players",
                    value="swap",
                    description="Swap starter/substitute or compatible starter roles",
                ),
            ]
        )
        self.add_item(discord.ui.Label(text="Change", component=self.operation))
        self.first = self.selector(data, False)
        self.second = self.selector(data, True)
        self.add_item(discord.ui.Label(text="Player to bring in, take out, or swap", component=self.first))
        self.add_item(
            discord.ui.Label(
                text="Other player (optional for force in/out)",
                description="Choose both for a swap. Leave blank for an automatic main-role replacement.",
                component=self.second,
            )
        )

    @staticmethod
    def selector(data, optional):
        kwargs = dict(
            placeholder="Choose a player",
            min_values=0 if optional else 1,
            max_values=1,
            required=not optional,
        )
        if not data.get("test_mode"):
            return discord.ui.UserSelect(**kwargs)
        options = [
            discord.SelectOption(
                label=item["username"][:100],
                value=str(mid),
                description=(
                    f"Starter · {data['roster'][mid]}"
                    if mid in data["roster"]
                    else "Substitute · " + " / ".join(item["roles"])
                )[:100],
            )
            for mid, item in data["players"].items()
        ]
        return discord.ui.Select(options=options, **kwargs)

    @staticmethod
    def chosen(select):
        value = select.values[0] if select.values else None
        return None if value is None else int(value.id if hasattr(value, "id") else value)

    async def on_submit(self, interaction):
        await self.controller.preview_lineup_edit(
            interaction,
            self.lid,
            self.sid,
            self.revision,
            self.operation.values[0],
            self.chosen(self.first),
            self.chosen(self.second),
        )


class LineupConfirmation(discord.ui.View):
    def __init__(self, controller, actor, args, roster):
        super().__init__(timeout=180)
        self.controller, self.actor, self.args, self.roster = controller, actor, args, roster

    async def interaction_check(self, interaction):
        if interaction.user.id != self.actor:
            await interaction.response.send_message(
                "This preview belongs to another manager.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Apply change", style=discord.ButtonStyle.success)
    async def apply(self, interaction, button):
        await self.controller.apply_lineup_edit(interaction, *self.args, expected=self.roster)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Lineup change cancelled.", embed=None, view=None)
        self.stop()


class ScrimController:
    def __init__(self, bot, connection, profile, authorize, games, color):
        self.bot, self.profile, self.authorize = bot, profile, authorize
        self.games, self.color = games, color
        self.store = ScrimStore(connection)
        self.locks, self.queue_tasks, self.views = {}, {}, {}
        self.monitor = ScrimMonitor(self)
        self.register()

    def start_monitor(self):
        self.monitor.start()

    async def game_started(self, guild, data, evidence):
        """Called under the guild lock only after a starter reports a custom game."""
        if data["status"] != "prepared":
            return
        data["revision"] = data.get("revision", 0) + 1
        data["detection"] = {
            **data.get("detection", {}),
            "battle_id": evidence.get("battle_id"),
            "absent": {},
            "test_mode": bool(data.get("test_mode")),
        }
        data["note"] = "Game Started · Custom game detected automatically by Rivals Data."
        data["admission_ids"] = [
            m.id
            for cid in (data["waiting_id"], data["stage_id"])
            for m in getattr(guild.get_channel(cid), "members", [])
            if m.id in data["players"] and not m.bot
        ]
        # A Discord failure must not erase the actual game start or change its recorded roster.
        self.store.start(data, None, started_at=evidence.get("started_at"))
        self.store.snapshot(data["match_id"], "live", evidence["payload"])
        await self.try_sync(guild, data)
        lobby = self.store.lobby(guild.id, data["game"])
        await self.panel(lobby, guild, data, mention_ids=data["roster"])

    async def game_ended(self, guild, data):
        """Record completion once, then recover voice independently of result indexing."""
        if data["status"] != "playing":
            return
        battle_id = data.get("detection", {}).get("battle_id")
        if battle_id:
            data.setdefault("finished_live_ids", []).append(battle_id)
        data["revision"] = data.get("revision", 0) + 1
        data["detection"] = {}
        data.pop("admission_ids", None)
        data["protected_in"] = []
        data["note"] = "Game Ended · Recorded locally; verifying the latest custom match for statistics."
        self.store.finish(data, None)
        try:
            await self.return_waiting(guild, data)
        except (ValueError, discord.HTTPException) as exc:
            data["sync_error"] = str(exc)
            self.store.save(data)
        lobby = self.store.lobby(guild.id, data["game"])
        self.queue(guild, lobby)
        await self.panel(lobby, guild, data)

    def lock(self, guild_id):
        return self.locks.setdefault(guild_id, asyncio.Lock())

    async def end_if_empty(self, guild, lobby, data):
        """Called under the guild lock after voice changes settle."""
        if data["status"] == "ended":
            return False
        waiting, play = self.channels(guild, data)
        if any(not member.bot for member in waiting.members + play.members):
            return False
        await self.cleanup_stage(guild, data)
        data["revision"] = data.get("revision", 0) + 1
        if data["status"] == "playing":
            self.store.finish(data, None, aborted=True)
        self.store.end(data, None)
        self.store.event(data, None, "empty_voice_rooms", {})
        lobby.update(session_id=None, ready_ids=[], revision=lobby["revision"] + 1)
        self.queue(guild, lobby)
        self.store.save_lobby(lobby)
        await self.panel(lobby, guild)
        return True

    async def end_empty_sessions(self):
        for active in self.store.active():
            guild = self.bot.get_guild(active["guild_id"])
            if guild is None:
                continue
            async with self.lock(guild.id):
                data = self.store.get(active["id"])
                lobby = self.store.lobby(guild.id, data["game"])
                if lobby["session_id"] == data["id"]:
                    try:
                        await self.end_if_empty(guild, lobby, data)
                    except (ValueError, discord.HTTPException):
                        logger.exception("Could not end empty scrim %s; cleanup will retry", data["id"])

    def edit_context(self, interaction, lid, sid, revision):
        lobby = self.store.get_lobby(lid)
        if not interaction.guild or interaction.guild.id != lobby["guild_id"] or lobby["session_id"] != sid:
            raise ValueError("This scrim session has changed. Use the current panel.")
        data = self.store.get(sid)
        self.permission(interaction, data["game"])
        if data.get("test_mode") and interaction.user.id != data["test_host"]:
            raise ValueError("This example scrim is reserved for its tester.")
        if data["status"] != "prepared" or data.get("revision", 0) != revision:
            raise ValueError(
                "The lineup changed or a game started. Reopen Edit lineup from the current panel."
            )
        return lobby, data

    async def open_lineup_editor(self, interaction, lid, sid, revision):
        try:
            async with self.lock(interaction.guild.id if interaction.guild else 0):
                lobby, data = self.edit_context(interaction, lid, sid, revision)
                self.eligible(interaction.guild, data)
                await interaction.response.send_modal(LineupEditor(self, lobby, data))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)

    def lineup_change_text(self, data, roster):
        before = data["roster"]
        lines = []
        for mid in sorted(set(before) - set(roster)):
            lines.append(f"Out: {self.player_label(data, mid)} ({before[mid]})")
        for mid in sorted(set(roster) - set(before)):
            lines.append(f"In: {self.player_label(data, mid)} ({roster[mid]})")
        for mid in sorted(set(roster) & set(before)):
            if roster[mid] != before[mid]:
                lines.append(f"{self.player_label(data, mid)}: {before[mid]} → {roster[mid]}")
        return "\n".join(lines)

    async def preview_lineup_edit(self, interaction, lid, sid, revision, operation, first, second):
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.lock(interaction.guild.id if interaction.guild else 0):
                _, data = self.edit_context(interaction, lid, sid, revision)
                players, _ = self.eligible(interaction.guild, data)
                roster = manual_lineup(players, data["roster"], data["players"], operation, first, second)
                description = (
                    self.lineup_change_text(data, roster)
                    + "\n\nEveryone stays in waiting until a custom game is detected."
                )
                embed = discord.Embed(title="Review lineup change", description=description, color=self.color)
                args = (lid, sid, revision, operation, first, second)
                view = LineupConfirmation(self, interaction.user.id, args, roster)
                await interaction.followup.send(
                    embed=embed, view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
                )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    async def apply_lineup_edit(self, interaction, lid, sid, revision, operation, first, second, *, expected):
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.lock(interaction.guild.id if interaction.guild else 0):
                lobby, data = self.edit_context(interaction, lid, sid, revision)
                players, _ = self.eligible(interaction.guild, data)
                roster = manual_lineup(players, data["roster"], data["players"], operation, first, second)
                if roster != expected:
                    raise ValueError(
                        "Available players or roles changed. Reopen Edit lineup for a fresh preview."
                    )
                summary = self.lineup_change_text(data, roster)
                incoming = set(roster) - set(data["roster"])
                outgoing = set(data["roster"]) - set(roster)
                data["protected_in"] = sorted((set(data.get("protected_in", [])) & set(roster)) | incoming)
                for mid in outgoing:
                    data["rotation_tick"] = data.get("rotation_tick", 0) + 1
                    data["players"][mid]["benched_at"] = data["rotation_tick"]
                data["revision"] = revision + 1
                data["note"] = summary
                self.store.lineup(data, roster, interaction.user.id, action="manual_lineup")
                await self.panel(lobby, interaction.guild, data, mention_ids=incoming)
                await interaction.edit_original_response(
                    content=summary + "\nLineup updated.",
                    embed=None,
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except (ValueError, discord.HTTPException) as exc:
            await interaction.edit_original_response(content=str(exc)[:1800], embed=None, view=None)

    def permission(self, interaction, game):
        extra = any(r.name == f"{game} Scrim Manager" for r in getattr(interaction.user, "roles", ()))
        if not interaction.guild or not (self.authorize(interaction, game) or extra):
            raise ValueError("Only this game's managers and server admins can manage scrims.")
        if game not in self.games:
            raise ValueError("Choose a configured game.")

    def channels(self, guild, data):
        waiting = guild.get_channel(data["waiting_id"])
        play = guild.get_channel(data["stage_id"])
        if not isinstance(waiting, discord.VoiceChannel) or not isinstance(play, discord.VoiceChannel):
            raise ValueError("The waiting room or scrim VC is missing. Run /scrim setup to repair it.")
        return waiting, play

    def queue(self, guild, lobby):
        waiting = guild.get_channel(lobby["waiting_id"])
        if not isinstance(waiting, discord.VoiceChannel):
            raise ValueError("The waiting room is missing. Run /scrim setup to repair it.")
        members = [m for m in waiting.members if not m.bot]
        reconcile_lobby(lobby, [m.id for m in members])
        lobby["test_owner_id"] = guild.owner_id
        return members

    def eligible(self, guild, data, *, include_stage=True):
        waiting, stage = self.channels(guild, data)
        people = {m.id: m for m in waiting.members if not m.bot}
        if include_stage:
            people.update({m.id: m for m in stage.members if m.id in data.get("players", {}) and not m.bot})
        result, excluded = [], []
        if data.get("test_mode"):
            result = [
                Player(**{key: item[key] for key in Player.__dataclass_fields__ if key in item})
                for item in data["players"].values()
                if item.get("simulated")
            ]
        for mid in people:
            saved = self.profile(guild.id, mid, data["game"])
            roles = normalized_roles(saved[2:]) if saved else ()
            if not roles and not data.get("test_mode"):
                excluded.append(mid)
                continue
            with self.store.connection() as db:
                # Retain verified account identities without making any API request.
                rows = db.execute(
                    "SELECT substr(identity,5) FROM account_claims WHERE guild_id=? "
                    "AND member_id=? AND game=? AND identity LIKE 'uid:%'",
                    (guild.id, mid, data["game"].casefold()),
                ).fetchall()
            old = data.get("players", {}).get(mid, {})
            username = saved[0] if saved else people[mid].display_name
            player = Player(
                mid,
                username,
                roles or ROLES,
                rows[0][0]
                if len(rows) == 1
                else (old.get("uid") if old.get("username") == username else None),
                old.get("played", 0),
                old.get("last_played", 0),
                old.get("joined", len(data.get("players", {})) + 1),
                benched_at=old.get("benched_at", 0),
            )
            data.setdefault("players", {})[mid] = asdict(player)
            if data.get("test_mode"):
                data["players"][mid]["test_roles_inferred"] = not roles
            result.append(player)
        return result, excluded

    @staticmethod
    def lines(rows, limit=3500):
        text = ""
        for i, row in enumerate(rows):
            if len(text) + len(row) + 1 > limit - 70:
                return text + f"\n…and {len(rows) - i} more."
            text += ("\n" if text else "") + row
        return text

    @staticmethod
    def player_label(data, mid):
        item = data["players"][mid]
        return (
            discord.utils.escape_markdown(item["username"]) + " (example)"
            if item.get("simulated")
            else f"<@{mid}>"
        )

    def embeds(self, lobby, guild, data=None):
        if not data:
            count = len(lobby["queue_ids"])
            description = f"Join <#{lobby['waiting_id']}> to play.\n2 Tank · 2 DPS · 2 Support"
            if count < 6:
                title = "Waiting for players"
                description += f"\n\n**{count}/6 players** · {6 - count} more needed."
            else:
                title = "Ready for a manager"
                description += f"\n\n**{count}/6 players** in the waiting room."
                description += "\nA manager can press **Start scrim**."
            embed = discord.Embed(title=title, description=description, color=self.color)
            if lobby.get("test_owner_id") in lobby["queue_ids"]:
                embed.add_field(
                    name="Solo test available",
                    value="The server owner can press **Test** to try an example scrim.",
                    inline=False,
                )
            if lobby.get("notice"):
                embed.add_field(name="Notice", value=lobby["notice"][:1000], inline=False)
            cards = [embed]
            if count:
                rows = []
                for mid in lobby["queue_ids"]:
                    profile = self.profile(guild.id, mid, lobby["game"])
                    roles = normalized_roles(profile[2:]) if profile else ()
                    rows.append(f"<@{mid}> · {' / '.join(roles) or 'Save roles with /edit_profile'}")
                cards.append(
                    discord.Embed(
                        title=f"Waiting room · {count} players", description=self.lines(rows), color=0x5865F2
                    )
                )
            return cards
        playing = data["status"] == "playing"
        phase = "In game" if playing else ("Waiting" if data["number"] == 1 else "Between games")
        matches = self.store.session_matches(data["id"])
        outcomes = {match["id"]: match_outcome(match)[1] for match in matches}
        wins = sum(match["status"] == "completed" and outcomes[match["id"]] is True for match in matches)
        losses = sum(match["status"] == "completed" and outcomes[match["id"]] is False for match in matches)
        title = f"Scrim · Game {data['number']} · {wins}–{losses} · {phase}"
        header = discord.Embed(title=title, color=self.color)
        pages = max(1, (len(matches) + 24) // 25)
        page = min(data.get("history_page", 0), pages - 1)
        end = len(matches) - page * 25
        for match in matches[max(0, end - 25) : end]:
            if match["status"] == "playing":
                result = "In game"
            elif match["status"] == "aborted":
                result = "Unfinished"
            else:
                outcome = outcomes[match["id"]]
                result = "🟢 Win" if outcome is True else "🔴 Loss" if outcome is False else "Result pending"
            header.add_field(name=f"Game {match['number']}", value=result, inline=True)
        cards = [header]
        waiting, stage = self.channels(guild, data)
        present = {m.id for m in waiting.members + stage.members}
        if data.get("test_mode"):
            present.update(mid for mid, item in data["players"].items() if item.get("simulated"))
        for role, color in zip(ROLES, (0x3498DB, 0xED4245, 0x2ECC71), strict=True):
            rows = [
                f"{self.player_label(data, mid)} · {data['players'][mid]['played']} completed"
                + (
                    " · Test flex roles"
                    if data["players"][mid].get("test_roles_inferred")
                    else (" · Main role" if role == data["players"][mid]["roles"][0] else " · Secondary role")
                )
                + (" · **not in voice**" if mid not in present else "")
                for mid, assigned in data["roster"].items()
                if assigned == role
            ]
            cards.append(discord.Embed(title=role, description="\n".join(rows), color=color))
        bench = sorted(
            (p for mid, p in data["players"].items() if mid in present and mid not in data["roster"]),
            key=lambda p: (p["played"], p["last_played"], p.get("benched_at", 0), p["joined"]),
        )
        rows = [
            f"{self.player_label(data, p['member_id'])} · {' / '.join(p['roles'])} · {p['played']} completed"
            + (" · Test flex roles" if p.get("test_roles_inferred") else "")
            + (" · Waiting for next game" if playing and p["member_id"] in lobby["queue_ids"] else "")
            for p in bench
        ]
        cards.append(
            discord.Embed(
                title=f"Substitutes · {len(bench)}",
                color=0x95A5A6,
                description=self.lines(rows, 2400) or "No substitutes currently in voice.",
            )
        )
        if data.get("sync_error"):
            cards[-1].description += "\n\nVoice needs attention. Use **Sync voice**."
        if lobby.get("notice"):
            cards[-1].description += "\n\n" + lobby["notice"][:700]
        return cards

    async def panel(self, lobby, guild, data=None, *, mention_ids=()):
        channel = guild.get_channel(lobby["control_id"])
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("The scrim control channel is missing.")
        view = ScrimPanel(self, lobby, data)
        mention_ids = [
            mid for mid in mention_ids if mid > 0 and (not data or not data["players"][mid].get("simulated"))
        ]
        kwargs = dict(
            embeds=self.embeds(lobby, guild, data),
            view=view,
            content=("Playing this round: " + " ".join(f"<@{mid}>" for mid in mention_ids))
            if mention_ids
            else None,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=[discord.Object(id=mid) for mid in mention_ids]
            ),
        )
        message = None
        if lobby["message_id"]:
            try:
                message = await channel.fetch_message(lobby["message_id"])
                await message.edit(**kwargs)
            except discord.NotFound:
                message = None
        if message is None:
            message = await channel.send(**kwargs)
            lobby["message_id"] = message.id
        self.store.save_lobby(lobby)
        previous = self.views.get(lobby["id"])
        if previous:
            previous.stop()
        self.bot.add_view(view, message_id=message.id)
        self.views[lobby["id"]] = view

    async def set_speaker_permission(self, play, member, selected, data):
        before = data.setdefault("overwrites_before", {})
        key = str(member.id)
        overwrite = play.overwrites_for(member)
        if key not in before:
            before[key] = [p.value for p in overwrite.pair()] if member in play.overwrites else None
            self.store.save(data)
        overwrite.view_channel = True
        overwrite.connect = False  # Bot admission never unlocks direct joining.
        overwrite.speak = selected
        await play.set_permissions(member, overwrite=overwrite, reason="Scrim lineup access")

    async def set_voice_mute(self, member, muted, data):
        before = data.setdefault("voice_mutes_before", {})
        key = str(member.id)
        if key not in before:
            before[key] = bool(member.voice and member.voice.mute)
            self.store.save(data)
        if member.voice and member.voice.mute != muted:
            await member.edit(mute=muted, reason="Scrim active lineup and substitutes")

    async def move_to_stage(self, member, play):
        if not member.voice or not member.voice.channel:
            raise ValueError(f"<@{member.id}> disconnected. Bring them back before starting.")
        if member.voice.channel.id == play.id:
            return
        await member.move_to(play, reason="Bot admission to locked scrim VC")
        try:
            async with asyncio.timeout(8):
                while not member.voice or not member.voice.channel or member.voice.channel.id != play.id:
                    await asyncio.sleep(0.05)
        except TimeoutError as exc:
            raise ValueError(f"Waiting for <@{member.id}> to arrive in scrim VC. Try Sync voice.") from exc

    async def sync_stage(self, guild, data):
        waiting, play = self.channels(guild, data)
        access = play.permissions_for(guild.me)
        if not all(
            getattr(access, flag)
            for flag in ("manage_channels", "manage_roles", "move_members", "mute_members", "connect")
        ):
            raise ValueError(
                "The bot needs Connect, Manage Channels, Manage Roles, "
                "Move Members and Mute Members on the scrim VC."
            )
        players, _ = self.eligible(guild, data)
        present = {p.member_id for p in players}
        missing = set(data["roster"]) - present
        if missing:
            raise ValueError(
                "Selected players left voice: "
                + ", ".join(f"<@{mid}>" for mid in missing)
                + ". Bring them back or use Sub in between games."
            )
        self.store.save(data)
        real_ids = {mid for mid in present if not data["players"][mid].get("simulated")}
        if data["status"] == "playing":
            # Reconnecting starters and admitted spectators can be repaired.
            # Late substitutes stay in waiting until the next detected game.
            admitted = {int(mid) for mid in data.get("voice_mutes_before", {})}
            admitted.update(data.get("admission_ids", []))
            admitted.update(m.id for m in play.members)
            real_ids &= admitted | set(data["roster"])
        # Mute outgoing players before enabling the new lineup.
        for mid in real_ids - set(data["roster"]):
            member = guild.get_member(mid)
            await self.set_speaker_permission(play, member, False, data)
            await self.move_to_stage(member, play)
            await self.set_voice_mute(member, True, data)
        for mid in set(data["roster"]) & real_ids:
            member = guild.get_member(mid)
            await self.set_speaker_permission(play, member, True, data)
            await self.move_to_stage(member, play)
            await self.set_voice_mute(member, False, data)
        data.pop("sync_error", None)
        self.store.save(data)

    async def return_waiting(self, guild, data):
        waiting = guild.get_channel(data["waiting_id"])
        play = guild.get_channel(data["stage_id"])
        if not isinstance(waiting, discord.VoiceChannel):
            raise ValueError("Restore the waiting VC before returning participants.")
        if play:
            for member in list(play.members):
                if member.id in data["players"] and not member.bot:
                    await member.move_to(waiting, reason="Game ended; wait for next custom game")
        for mid, muted in list(data.get("voice_mutes_before", {}).items()):
            member = guild.get_member(int(mid))
            if member and member.voice and member.voice.channel:
                await member.edit(mute=muted, reason="Restore pre-game voice state")
                del data["voice_mutes_before"][mid]
                self.store.save(data)
        data.pop("sync_error", None)
        self.store.save(data)

    async def cleanup_stage(self, guild, data):
        await self.return_waiting(guild, data)
        play = guild.get_channel(data["stage_id"])
        if play is None:
            return
        for mid, values in data.get("overwrites_before", {}).items():
            target = guild.get_member(int(mid))
            if target is None:
                try:
                    target = await guild.fetch_member(int(mid))
                except discord.NotFound:
                    continue
            overwrite = (
                None
                if values is None
                else discord.PermissionOverwrite.from_pair(
                    discord.Permissions(values[0]), discord.Permissions(values[1])
                )
            )
            if overwrite is not None:
                overwrite.connect = False
            await play.set_permissions(
                target, overwrite=overwrite, reason="Restore pre-scrim speaker permissions"
            )

    async def try_sync(self, guild, data):
        try:
            await self.sync_stage(guild, data)
        except (ValueError, discord.HTTPException) as exc:
            data["sync_error"] = str(exc)
            self.store.save(data)
            return False
        return True

    async def action(self, interaction, lid, sid, revision, action):
        if action == "edit":
            await self.open_lineup_editor(interaction, lid, sid, revision)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if action in {"start", "finish"}:
                raise ValueError("Game starts and ends are detected automatically. Use the current panel.")
            if action == "end":
                raise ValueError("Sessions end automatically when everyone leaves the scrim voice rooms.")
            lobby = self.store.get_lobby(lid)
            if not interaction.guild or interaction.guild.id != lobby["guild_id"]:
                raise ValueError("These controls belong to another server.")
            self.permission(interaction, lobby["game"])
            if action == "test" and interaction.user.id != interaction.guild.owner_id:
                raise ValueError("The Test button is reserved for the server owner.")
            async with self.lock(lobby["guild_id"]):
                lobby = self.store.get_lobby(lid)
                if sid != (lobby["session_id"] or 0):
                    raise ValueError("These controls have changed. Use the current panel.")
                if not sid:
                    members = self.queue(interaction.guild, lobby)
                    self.store.save_lobby(lobby)
                    if revision != lobby["revision"]:
                        await self.panel(lobby, interaction.guild)
                        raise ValueError("The waiting room changed. Use the updated buttons.")
                    if action == "test":
                        await self.begin_test(interaction, lobby)
                        reply = "Test lineup ready. Real starters' custom games are detected automatically."
                    elif action == "begin":
                        if lobby["game"].casefold() != "marvel rivals":
                            raise ValueError("Automatic scrim detection currently supports Marvel Rivals.")
                        if len(members) < 6:
                            raise ValueError(
                                "At least six players must join before a manager starts the scrim."
                            )
                        temporary = {**lobby, "players": {}}
                        players, excluded = self.eligible(interaction.guild, temporary, include_stage=False)
                        if excluded:
                            raise ValueError(
                                "All queued players need saved roles. Use /edit_profile before starting."
                            )
                        config = self.store.config(lobby["guild_id"], lobby["game"])
                        data = self.store.create(
                            lobby["guild_id"],
                            lobby["game"],
                            config,
                            players,
                            random_team(players),
                            interaction.user.id,
                        )
                        data.update(revision=0, protected_in=[])
                        self.store.save(data)
                        lobby["session_id"] = data["id"]
                        lobby["ready_ids"] = []
                        self.store.save_lobby(lobby)
                        await self.panel(lobby, interaction.guild, data, mention_ids=data["roster"])
                        self.start_monitor()
                        reply = (
                            "Lineup created. Review it, then start a custom game; "
                            "Rivals Data detects it automatically."
                        )
                    else:
                        raise ValueError("Use the current waiting-room controls.")
                else:
                    data = self.store.get(sid)
                    if data.get("test_mode") and interaction.user.id != data["test_host"]:
                        raise ValueError("This example scrim is reserved for its tester.")
                    if revision != data.get("revision", 0) or data["status"] == "ended":
                        raise ValueError("That action was already handled. Use the updated controls.")
                    # Persist the button revision alongside the roster/count mutation,
                    # before making slower Discord calls. Retries cannot repeat it.
                    data["revision"] = data.get("revision", 0) + 1
                    mention = ()
                    if action == "sub":
                        if data["status"] != "prepared" or data["number"] == 1:
                            raise ValueError("End the current game before making substitutions.")
                        players, _ = self.eligible(interaction.guild, data)
                        roster, incoming, outgoing = substitute_one(
                            players, data["roster"], data["players"], data.get("protected_in", [])
                        )
                        data.setdefault("protected_in", []).append(incoming)
                        data["rotation_tick"] = data.get("rotation_tick", 0) + 1
                        data["players"][outgoing]["benched_at"] = data["rotation_tick"]
                        self.store.lineup(data, roster, interaction.user.id)
                        data["note"] = (
                            f"{self.player_label(data, incoming)} in for "
                            f"{self.player_label(data, outgoing)} · {roster[incoming]}."
                        )
                        mention = (incoming,)
                        reply = (
                            f"Out: {self.player_label(data, outgoing)} · "
                            f"In: {self.player_label(data, incoming)} ({roster[incoming]})."
                        )
                    elif action == "reroll":
                        if data["status"] != "prepared":
                            raise ValueError("End the current game before rerolling teams.")
                        players, _ = self.eligible(interaction.guild, data)
                        roster = reroll_team(players, prefer_real=bool(data.get("test_mode")))
                        data["protected_in"] = []
                        data["note"] = (
                            "Full lineup rerolled from all available players, including substitutes."
                        )
                        self.store.lineup(data, roster, interaction.user.id, action="rerolled")
                        mention = tuple(roster)
                        reply = "Teams rerolled. Everyone stays in waiting until a custom game is detected."
                    elif action == "sync":
                        if data["status"] == "playing":
                            await self.try_sync(interaction.guild, data)
                        else:
                            await self.return_waiting(interaction.guild, data)
                        reply = (
                            "Voice synchronized."
                            if not data.get("sync_error")
                            else "Voice still needs attention; see the panel."
                        )
                    elif action in {"older", "newer"}:
                        pages = max(1, (len(self.store.session_matches(data["id"])) + 24) // 25)
                        data["history_page"] = max(
                            0,
                            min(
                                pages - 1,
                                data.get("history_page", 0) + (1 if action == "older" else -1),
                            ),
                        )
                        reply = "Match list updated."
                    else:
                        raise ValueError("Unknown scrim control.")
                    self.store.save(data)
                    await self.panel(lobby, interaction.guild, data, mention_ids=mention)
            await interaction.followup.send(reply, ephemeral=True)
        except (ValueError, sqlite3.IntegrityError, discord.HTTPException) as exc:
            await interaction.followup.send(str(exc)[:1800], ephemeral=True)
        except Exception:
            logger.exception("Scrim control failed")
            await interaction.followup.send(
                "The control could not finish. Your saved session is retained; try /scrim status.",
                ephemeral=True,
            )

    async def begin_test(self, interaction, lobby):
        host_id = interaction.user.id
        if host_id not in lobby["queue_ids"]:
            raise ValueError("Join the Scrim Waiting Room before pressing Test.")
        if lobby["game"].casefold() != "marvel rivals":
            raise ValueError("The leaderboard example currently supports Marvel Rivals.")
        temporary = {**lobby, "players": {}, "test_mode": True}
        real_players, _ = self.eligible(interaction.guild, temporary, include_stage=False)
        try:
            pool = await asyncio.to_thread(
                scrim_rivals.fetch_test_pool, tuple(p.username for p in real_players)
            )
        except Exception as exc:
            raise ValueError("The leaderboard is unavailable. Try Test again later.") from exc
        # Recheck attendance after the leaderboard request; no automatic start on joining.
        self.queue(interaction.guild, lobby)
        self.store.save_lobby(lobby)
        if host_id not in lobby["queue_ids"]:
            raise ValueError("You left the waiting room. Rejoin before testing.")
        real_players, _ = self.eligible(interaction.guild, temporary, include_stage=False)
        real_names = {p.username.casefold() for p in real_players}
        pool = [item for item in pool if item["username"].casefold() not in real_names]
        examples = [Player(**{k: item[k] for k in Player.__dataclass_fields__ if k in item}) for item in pool]
        players = [*real_players, *examples]
        config = self.store.config(lobby["guild_id"], lobby["game"])
        data = self.store.create(
            lobby["guild_id"],
            lobby["game"],
            config,
            players,
            random_team(players, required_ids={host_id}, prefer_real=True),
            host_id,
            test_mode=True,
        )
        data.update(revision=0, protected_in=[], manual_test=True)
        data["players"].update({p.member_id: temporary["players"][p.member_id] for p in real_players})
        data["players"].update({item["member_id"]: item for item in pool})
        self.store.save(data)
        lobby.update(session_id=data["id"], ready_ids=[])
        self.store.save_lobby(lobby)
        await self.panel(lobby, interaction.guild, data, mention_ids=data["roster"])
        self.start_monitor()

    async def restore(self):
        self.store.initialize()
        for guild_id, game in self.store.configured():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            async with self.lock(guild_id):
                self.store.disable_test(guild_id, game)
                lobby = self.store.lobby(guild_id, game)
                try:
                    active = next(
                        (s for s in self.store.active() if s["guild_id"] == guild_id and s["game"] == game),
                        None,
                    )
                    if active and active.get("test_mode") and not active.get("manual_test"):
                        if active["status"] == "playing":
                            self.store.finish(active, None, aborted=True)
                        self.store.end(active, None)
                        active = None
                    lobby["session_id"] = active["id"] if active else None
                    self.queue(guild, lobby)
                    if active and await self.end_if_empty(guild, lobby, active):
                        continue
                    if active:
                        if active.get("test_mode"):
                            self.eligible(guild, active)
                            self.store.save(active)
                        if active["status"] == "playing":
                            await self.try_sync(guild, active)
                        else:
                            try:
                                await self.return_waiting(guild, active)
                            except (ValueError, discord.HTTPException) as exc:
                                active["sync_error"] = str(exc)
                                self.store.save(active)
                    else:
                        # Reconnect cannot prove continuous voice presence: re-check in.
                        lobby["ready_ids"] = []
                        lobby["revision"] += 1
                        self.queue(guild, lobby)
                    await self.panel(lobby, guild, active)
                except (ValueError, discord.HTTPException):
                    logger.exception("Could not restore %s scrim controls in %s", game, guild_id)
        self.start_monitor()

    async def voice_update(self, member, before, after):
        if member.bot:
            return
        if after.channel and before.channel != after.channel:
            async with self.lock(member.guild.id):
                pending = self.store.pending_mute_restores(member.guild.id, member.id)
                if pending:
                    original = pending[0]["voice_mutes_before"][str(member.id)]
                    await member.edit(mute=original, reason="Restore voice state after leaving the scrim")
                    for ended in pending:
                        ended["voice_mutes_before"].pop(str(member.id), None)
                        self.store.save(ended)
        for guild_id, game in self.store.configured():
            if guild_id != member.guild.id:
                continue
            lobby = self.store.lobby(guild_id, game)
            ids = {c.id for c in (before.channel, after.channel) if c}
            if not ids & {lobby["waiting_id"], lobby["stage_id"]}:
                continue
            async with self.lock(guild_id):
                lobby = self.store.get_lobby(lobby["id"])
                if lobby["session_id"]:
                    data = self.store.get(lobby["session_id"])
                    if after.channel and after.channel.id == lobby["waiting_id"]:
                        original = data.get("voice_mutes_before", {}).get(str(member.id))
                        if original is not None and after.mute != original:
                            await member.edit(
                                mute=original, reason="Restore voice state in scrim waiting room"
                            )
                    if after.channel and after.channel.id == lobby["stage_id"]:
                        if data["status"] != "playing" or member.id not in data["players"]:
                            waiting = member.guild.get_channel(lobby["waiting_id"])
                            await member.move_to(waiting, reason="Wait for manager-controlled game admission")
                        elif after.mute != (member.id not in data["roster"]):
                            await self.set_voice_mute(member, member.id not in data["roster"], data)
                elif before.channel != after.channel:
                    if after.channel and after.channel.id == lobby["stage_id"]:
                        waiting = member.guild.get_channel(lobby["waiting_id"])
                        await member.move_to(waiting, reason="Scrim VC admission is managed by the bot")
                    if before.channel and before.channel.id == lobby["waiting_id"]:
                        lobby["revision"] += 1
                    self.queue(member.guild, lobby)
                    self.store.save_lobby(lobby)
            if before.channel == after.channel:
                continue
            task = self.queue_tasks.get(lobby["id"])
            if task is None or task.done():
                self.queue_tasks[lobby["id"]] = asyncio.create_task(self.refresh_queue(lobby["id"]))

    async def refresh_queue(self, lid):
        await asyncio.sleep(1)
        lobby = self.store.get_lobby(lid)
        guild = self.bot.get_guild(lobby["guild_id"])
        if not guild:
            return
        async with self.lock(guild.id):
            lobby = self.store.get_lobby(lid)
            try:
                data = self.store.get(lobby["session_id"]) if lobby["session_id"] else None
                if data and await self.end_if_empty(guild, lobby, data):
                    return
                if data:
                    self.queue(guild, lobby)
                    previous = set(data["players"])
                    eligible, excluded = self.eligible(guild, data)
                    self.store.save(data)
                    if set(data["players"]) != previous:
                        self.store.event(data, None, "queue_joined", sorted(set(data["players"]) - previous))
                    if data["status"] == "playing":
                        waiting, play = self.channels(guild, data)
                        for p in eligible:
                            if p.simulated:
                                continue
                            member = guild.get_member(p.member_id)
                            if (
                                p.member_id in data["roster"]
                                and member.voice
                                and member.voice.channel == waiting
                            ):
                                selected = p.member_id in data["roster"]
                                await self.set_speaker_permission(play, member, selected, data)
                                await self.move_to_stage(member, play)
                                await self.set_voice_mute(member, not selected, data)
                    lobby["notice"] = (
                        "Save preferred roles with /edit_profile to join the substitute queue: "
                        + ", ".join(f"<@{mid}>" for mid in excluded)
                        if excluded
                        else ""
                    )
                else:
                    self.queue(guild, lobby)
                await self.panel(lobby, guild, data)
            except (ValueError, discord.HTTPException):
                logger.exception("Could not update scrim waiting room")

    async def setup_channels(self, guild, game, control=None, waiting=None, stage=None):
        if any(s["guild_id"] == guild.id and s["game"] == game for s in self.store.active()):
            raise ValueError("End the active session before changing scrim channels.")
        channels = await guild.fetch_channels()
        category = discord.utils.get(channels, name=game)
        if not isinstance(category, discord.CategoryChannel):
            category = await guild.create_category(game)

        def existing(name, kind):
            candidates = [c for c in channels if isinstance(c, kind) and c.name == name]
            scoped = next((c for c in candidates if c.category_id == category.id), None)
            # Adopt the original uncategorized channels only for the first game.
            return scoped or (
                next((c for c in candidates if c.category_id is None), None)
                if game == self.games[0]
                else None
            )

        control = (
            control
            or existing("scrim-control", discord.TextChannel)
            or await guild.create_text_channel("scrim-control", category=category)
        )
        waiting = (
            waiting
            or existing("Scrim Waiting Room", discord.VoiceChannel)
            or await guild.create_voice_channel("Scrim Waiting Room", category=category)
        )
        stage = (
            stage
            or existing("Scrim", discord.VoiceChannel)
            or await guild.create_voice_channel("Scrim", category=category)
        )
        if not isinstance(stage, discord.VoiceChannel):
            raise ValueError("Select a regular voice channel for scrims.")
        me = guild.me or await guild.fetch_member(self.bot.user.id)
        for channel in (control, waiting, stage):
            if channel.category_id != category.id:
                await channel.edit(category=category, sync_permissions=False)
            overwrite = channel.overwrites_for(guild.default_role)
            overwrite.view_channel = False
            overwrite.send_messages = False
            overwrite.connect = overwrite.speak = False
            if overwrite != channel.overwrites_for(guild.default_role):
                await channel.set_permissions(guild.default_role, overwrite=overwrite)
            for role in guild.roles:
                visitor = role.name == os.getenv("MEMBER_ROLE_NAME", "Member")
                game_role = role.name in (game, f"{game} Tryout", f"{game} Manager")
                if not visitor and not game_role:
                    continue
                access = channel.overwrites_for(role)
                access.view_channel = access.read_message_history = True
                access.send_messages = False
                access.connect = access.speak = game_role and channel == waiting
                if access != channel.overwrites_for(role):
                    await channel.set_permissions(
                        role, overwrite=access, reason="Visitor and game-specific scrim access"
                    )
            if channel == stage:
                for target, original in list(channel.overwrites.items()):
                    if target == me or target == guild.default_role:
                        continue
                    original.connect = False
                    await channel.set_permissions(
                        target, overwrite=original, reason="Lock direct scrim VC entry"
                    )
            if me.guild_permissions.administrator:
                continue
            bot_access = channel.overwrites_for(me)
            for flag in (
                "view_channel",
                "send_messages",
                "embed_links",
                "read_message_history",
                "connect",
                "speak",
                "manage_channels",
                "manage_roles",
                "mute_members",
                "move_members",
            ):
                setattr(bot_access, flag, True)
            await channel.set_permissions(me, overwrite=bot_access)
        self.store.configure(guild.id, game, control.id, waiting.id, stage.id)
        lobby = self.store.lobby(guild.id, game)
        if lobby["control_id"] != control.id:
            old_channel = guild.get_channel(lobby["control_id"])
            if old_channel and lobby["message_id"]:
                try:
                    await old_channel.get_partial_message(lobby["message_id"]).delete()
                except discord.NotFound:
                    pass
            lobby["message_id"] = None
        lobby.update(control_id=control.id, waiting_id=waiting.id, stage_id=stage.id, ready_ids=[])
        lobby["revision"] += 1
        self.store.save_lobby(lobby)
        return control, waiting, stage

    def register(self):
        group = app_commands.Group(
            name="scrim", description="Waiting room, fair substitutions and match controls", guild_only=True
        )

        @group.command(
            name="setup", description="Managers: configure the scrim channels under the game category"
        )
        async def setup(
            interaction: discord.Interaction,
            control: discord.TextChannel | None = None,
            waiting: discord.VoiceChannel | None = None,
            stage: discord.VoiceChannel | None = None,
            game: str | None = None,
        ):
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                game = game or self.games[0]
                self.permission(interaction, game)
                async with self.lock(interaction.guild.id):
                    await self.setup_channels(interaction.guild, game, control, waiting, stage)
                    lobby = self.store.lobby(interaction.guild.id, game)
                    self.queue(interaction.guild, lobby)
                    await self.panel(lobby, interaction.guild)
                await interaction.followup.send(
                    f"Controls ready in <#{lobby['control_id']}>.", ephemeral=True
                )
            except (ValueError, discord.HTTPException) as exc:
                await interaction.followup.send(str(exc)[:1800], ephemeral=True)

        @group.command(name="status", description="Show or restore the current scrim dashboard")
        async def status(interaction: discord.Interaction, game: str | None = None):
            await interaction.response.defer(ephemeral=True)
            try:
                async with self.lock(interaction.guild.id):
                    lobby = self.store.lobby(interaction.guild.id, game or self.games[0])
                    data = self.store.get(lobby["session_id"]) if lobby["session_id"] else None
                    if data and data["status"] == "ended":
                        lobby.update(session_id=None, ready_ids=[])
                        lobby["revision"] += 1
                        data = None
                    if not data:
                        self.queue(interaction.guild, lobby)
                    await self.panel(lobby, interaction.guild, data)
                await interaction.followup.send(f"Controls: <#{lobby['control_id']}>.", ephemeral=True)
            except (ValueError, discord.HTTPException) as exc:
                await interaction.followup.send(str(exc)[:1800], ephemeral=True)

        @group.command(name="create", description="Managers: start a scrim with six or more queued players")
        async def create(interaction: discord.Interaction, game: str | None = None):
            try:
                lobby = self.store.lobby(interaction.guild.id, game or self.games[0])
                await self.action(interaction, lobby["id"], 0, lobby["revision"], "begin")
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)

        @group.command(
            name="log", description="Managers: export local rosters, games and substitution history"
        )
        async def log(interaction: discord.Interaction, session_id: int):
            await interaction.response.defer(ephemeral=True)
            try:
                data = self.store.get(session_id)
                self.permission(interaction, data["game"])
                if data["guild_id"] != interaction.guild.id:
                    raise ValueError("Session belongs to another server.")
                payload = json.dumps(self.store.export(session_id), ensure_ascii=False, indent=2).encode()
                if len(payload) > interaction.guild.filesize_limit:
                    raise ValueError(
                        "This export exceeds Discord’s upload limit. Export the SQLite data locally."
                    )
                await interaction.followup.send(
                    file=discord.File(io.BytesIO(payload), filename=f"scrim-{session_id}.json"),
                    ephemeral=True,
                )
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)

        self.bot.tree.add_command(group)
        self.bot.add_listener(self.voice_update, "on_voice_state_update")
