import asyncio
import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from rionnag.scrims import scrim_rivals
from rionnag.scrims.scrim_discord import LineupEditor, ScrimController, ScrimPanel
from rionnag.scrims.scrims import Player, ScrimStore, random_team, substitute_one


class QueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / "rionnag.scrims.scrims.sqlite3"

        @contextmanager
        def connection():
            db = sqlite3.connect(path)
            try:
                with db:
                    yield db
            finally:
                db.close()

        self.store = ScrimStore(connection)
        self.store.initialize()
        with connection() as db:
            db.execute("CREATE TABLE account_claims(guild_id,member_id,game,identity)")
        self.store.configure(10, "Marvel Rivals", 100, 101, 102)
        self.lobby = self.store.lobby(10, "Marvel Rivals")
        self.pool = [
            Player(i, f"p{i}", (role,), joined=i)
            for i, role in enumerate(("Tank",) * 3 + ("DPS",) * 3 + ("Support",) * 3, 1)
        ]
        self.waiting = MagicMock(spec=discord.VoiceChannel)
        self.waiting.id, self.waiting.members = 101, []
        self.stage = MagicMock(spec=discord.VoiceChannel)
        self.stage.id, self.stage.members, self.stage.instance = 102, [], None
        self.stage.overwrites = {}
        self.stage.permissions_for.return_value = SimpleNamespace(
            manage_channels=True, manage_roles=True, move_members=True, mute_members=True, connect=True
        )
        self.stage.overwrites_for.side_effect = lambda m: self.stage.overwrites.get(
            m, discord.PermissionOverwrite()
        )

        async def permissions(member, *, overwrite, **kwargs):
            if overwrite is None:
                self.stage.overwrites.pop(member, None)
            else:
                self.stage.overwrites[member] = overwrite

        async def create(**kwargs):
            self.stage.instance = SimpleNamespace(delete=AsyncMock())

        self.stage.set_permissions = AsyncMock(side_effect=permissions)
        self.stage.create_instance = AsyncMock(side_effect=create)
        self.message = SimpleNamespace(id=1234, edit=AsyncMock())
        self.control = MagicMock(spec=discord.TextChannel)
        self.control.send = AsyncMock(return_value=self.message)
        self.control.fetch_message = AsyncMock(return_value=self.message)
        channels = {100: self.control, 101: self.waiting, 102: self.stage}
        self.members, self.events = {}, []
        self.guild = SimpleNamespace(
            id=10,
            owner_id=1,
            me=SimpleNamespace(id=1000),
            get_channel=channels.get,
            get_member=self.members.get,
        )
        for p in self.pool:
            member = MagicMock(spec=discord.Member)
            member.id, member.bot, member.guild = p.member_id, False, self.guild
            member.display_name = p.username
            member.voice = SimpleNamespace(channel=self.waiting, mute=False)

            async def move(channel, *, person=member, **kwargs):
                self.events.append(("move", person.id, channel.id))
                previous = person.voice.channel
                if previous and person in previous.members:
                    previous.members.remove(person)
                channel.members.append(person)
                person.voice.channel = channel

            async def edit(*, person=member, mute, **kwargs):
                self.events.append(("mute", person.id, mute))
                person.voice.mute = mute

            member.move_to, member.edit = AsyncMock(side_effect=move), AsyncMock(side_effect=edit)
            self.members[p.member_id] = member
        self.waiting.members = list(self.members.values())
        self.controller = object.__new__(ScrimController)
        self.controller.store, self.controller.guild = self.store, self.guild
        self.controller.locks, self.controller.queue_tasks = {}, {}
        self.controller.views = {}
        self.controller.color, self.controller.games = 0x9B59B6, ["Marvel Rivals"]
        self.controller.authorize = lambda interaction, _: interaction.user.id in (1, 999)
        profiles = {p.member_id: (p.username, "Eastern", p.roles[0], None) for p in self.pool}
        self.controller.profile = lambda _, mid, __: profiles.get(mid)
        self.controller.bot = SimpleNamespace(add_view=MagicMock(), get_guild=lambda _: self.guild)
        self.controller.monitor = SimpleNamespace(start=MagicMock())
        self.controller.queue(self.guild, self.lobby)
        self.store.save_lobby(self.lobby)

    def tearDown(self):
        for task in self.controller.queue_tasks.values():
            task.cancel()
        self.temp.cleanup()

    def interaction(self, user=999):
        return SimpleNamespace(
            guild=self.guild,
            user=SimpleNamespace(id=user, roles=[]),
            response=SimpleNamespace(defer=AsyncMock(), send_modal=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    async def click(self, action, *, user=999, revision=None):
        lobby = self.store.get_lobby(self.lobby["id"])
        sid = lobby["session_id"] or 0
        data = self.store.get(sid) if sid else None
        rev = data.get("revision", 0) if data else lobby["revision"]
        interaction = self.interaction(user)
        # Exercise the existing voice/lineup cases through automatic transitions.
        # Public button rejection and API evidence are tested separately.
        if action in {"start", "finish"} and data:
            if not self.controller.authorize(interaction, data["game"]):
                return interaction
            if data.get("test_mode") and user != data["test_host"]:
                return interaction
            if revision is not None and revision != rev:
                return interaction
            async with self.controller.lock(self.guild.id):
                if action == "start":
                    await self.controller.game_started(
                        self.guild, data, {"battle_id": None, "payload": {"mode": "custom"}}
                    )
                else:
                    await self.controller.game_ended(self.guild, data)
            return interaction
        await self.controller.action(
            interaction, lobby["id"], sid, rev if revision is None else revision, action
        )
        return interaction

    async def begin(self, *, start=True):
        await self.click("begin")
        if start:
            await self.click("start")
        return self.store.get(self.store.get_lobby(self.lobby["id"])["session_id"])

    async def edit_preview(self, data, *, user=999):
        outgoing = next(iter(data["roster"]))
        incoming = next(
            mid
            for mid, p in data["players"].items()
            if mid not in data["roster"] and data["roster"][outgoing] in p["roles"]
        )
        interaction = self.interaction(user)
        args = (self.lobby["id"], data["id"], data["revision"], "swap", outgoing, incoming)
        await self.controller.preview_lineup_edit(interaction, *args)
        return args, interaction.followup.send.call_args.kwargs["view"]

    async def test_manual_form_preview_apply_and_stale_replay(self):
        data = await self.begin(start=False)
        clicked = await self.click("edit")
        modal = clicked.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal, LineupEditor)
        self.assertEqual(len(modal.to_components()), 3)
        self.assertIsInstance(modal.first, discord.ui.UserSelect)
        self.assertFalse(modal.second.required)
        args, view = await self.edit_preview(data)
        self.assertEqual(self.store.get(data["id"])["roster"], data["roster"])
        outsider = self.interaction(2)
        self.assertFalse(await view.interaction_check(outsider))
        apply = self.interaction()
        await self.controller.apply_lineup_edit(apply, *args, expected=view.roster)
        saved = self.store.get(data["id"])
        self.assertEqual(saved["roster"], view.roster)
        self.assertIn(args[-1], saved["protected_in"])
        self.assertEqual(self.events, [])
        self.assertEqual(sum(p["played"] for p in saved["players"].values()), 0)
        self.assertIn(f"Out: <@{args[-2]}>", apply.edit_original_response.call_args.kwargs["content"])
        await self.controller.apply_lineup_edit(self.interaction(), *args, expected=view.roster)
        self.assertEqual(self.store.get(data["id"])["roster"], view.roster)
        events = self.store.export(data["id"])["events"]
        self.assertEqual(len([e for e in events if e["action"] == "manual_lineup"]), 1)

    async def test_edit_rechecks_permissions_attendance_and_running_game(self):
        data = await self.begin(start=False)
        rejected = await self.click("edit", user=2)
        rejected.response.send_modal.assert_not_awaited()
        args, view = await self.edit_preview(data)
        self.controller.authorize = lambda *_: False
        await self.controller.apply_lineup_edit(self.interaction(), *args, expected=view.roster)
        self.assertEqual(self.store.get(data["id"])["roster"], data["roster"])
        self.controller.authorize = lambda *_: True
        newcomer = self.members[args[-1]]
        self.waiting.members.remove(newcomer)
        newcomer.voice.channel = None
        await self.controller.apply_lineup_edit(self.interaction(), *args, expected=view.roster)
        self.assertEqual(self.store.get(data["id"])["roster"], data["roster"])
        await self.click("start")
        await self.controller.apply_lineup_edit(self.interaction(), *args, expected=view.roster)
        rejected = await self.click("edit")
        rejected.response.send_modal.assert_not_awaited()
        self.assertEqual(
            self.store.export(data["id"])["matches"][0]["roster"],
            self.store.match(self.store.get(data["id"])["match_id"])["roster"],
        )

    async def test_example_form_can_select_virtual_players_and_enforces_host(self):
        with patch.object(scrim_rivals, "fetch_test_pool", return_value=self.example_pool()):
            await self.click("test", user=1)
        data = self.store.active()[0]
        clicked = await self.click("edit", user=1)
        modal = clicked.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal.first, discord.ui.Select)
        self.assertTrue(any(int(option.value) < 0 for option in modal.first.options))
        args, view = await self.edit_preview(data, user=1)
        await self.controller.apply_lineup_edit(self.interaction(1), *args, expected=view.roster)
        self.assertNotIn("<@-", self.store.get(data["id"])["note"])
        rejected = await self.click("edit", user=999)
        rejected.response.send_modal.assert_not_awaited()

    async def test_late_arrivals_stay_waiting_through_refresh_sync_and_restore(self):
        late = self.members[9]
        self.waiting.members.remove(late)
        late.voice.channel = None
        self.controller.queue(self.guild, self.lobby)
        self.store.save_lobby(self.lobby)
        data = await self.begin()
        snapshot = self.store.match(data["match_id"])["roster"]
        late.voice.channel = self.waiting
        self.waiting.members.append(late)
        self.events.clear()
        await self.controller.refresh_queue(self.lobby["id"])
        saved = self.store.get(data["id"])
        self.assertIn(9, saved["players"])
        self.assertNotIn(9, saved["roster"])
        self.assertEqual(late.voice.channel, self.waiting)
        self.assertFalse(late.voice.mute)
        self.assertIn("Waiting for next game", self.message.edit.call_args.kwargs["embeds"][-1].description)
        await self.click("sync")
        await self.controller.restore()
        self.assertEqual(late.voice.channel, self.waiting)
        self.assertFalse(any(event[1] == 9 for event in self.events))
        self.assertEqual(self.store.match(data["match_id"])["roster"], snapshot)
        await self.click("finish")
        self.assertEqual(self.store.get(data["id"])["players"][9]["played"], 0)
        for _ in range(3):
            await self.click("sub")
            if 9 in self.store.get(data["id"])["roster"]:
                break
        self.assertIn(9, self.store.get(data["id"])["roster"])
        await self.click("start")
        self.assertEqual(late.voice.channel, self.stage)
        self.assertFalse(late.voice.mute)

    async def test_selected_starter_can_rejoin_but_newcomer_without_roles_stays_waiting(self):
        data = await self.begin()
        starter = self.members[next(iter(data["roster"]))]
        self.stage.members.remove(starter)
        self.waiting.members.append(starter)
        starter.voice.channel = self.waiting
        unknown = MagicMock(spec=discord.Member)
        unknown.id, unknown.bot, unknown.guild = 50, False, self.guild
        unknown.voice = SimpleNamespace(channel=self.waiting, mute=False)
        unknown.move_to, unknown.edit = AsyncMock(), AsyncMock()
        self.waiting.members.append(unknown)
        await self.controller.refresh_queue(self.lobby["id"])
        self.assertEqual(starter.voice.channel, self.stage)
        self.assertEqual(unknown.voice.channel, self.waiting)
        unknown.move_to.assert_not_awaited()
        unknown.edit.assert_not_awaited()
        self.assertIn("/edit_profile", self.message.edit.call_args.kwargs["embeds"][-1].description)

    async def test_empty_room_has_one_waiting_embed_and_no_enabled_controls(self):
        self.waiting.members = []
        self.controller.queue(self.guild, self.lobby)
        await self.controller.panel(self.lobby, self.guild)
        kwargs = self.control.send.call_args.kwargs
        self.assertEqual(len(kwargs["embeds"]), 1)
        self.assertEqual(kwargs["embeds"][0].title, "Waiting for players")
        self.assertTrue(all(b.disabled for b in kwargs["view"].children))

    async def test_manager_can_start_without_any_ready_vote(self):
        data = await self.begin()
        self.assertEqual(data["status"], "playing")
        self.assertEqual(self.lobby["ready_ids"], [])

    async def test_nonmanager_cannot_start_and_under_six_cannot_start(self):
        await self.click("begin", user=2)
        self.assertEqual(self.store.active(), [])
        self.waiting.members = list(self.members.values())[:5]
        self.controller.queue(self.guild, self.lobby)
        self.store.save_lobby(self.lobby)
        await self.click("begin")
        self.assertEqual(self.store.active(), [])

    async def test_missing_profiles_prevent_start_without_silent_exclusion(self):
        self.controller.profile = lambda *_: None
        await self.click("begin")
        self.assertEqual(self.store.active(), [])

    async def test_waiting_room_counts_update_and_has_only_start_and_test(self):
        for count in (1, 2, 3, 6, 7):
            self.waiting.members = list(self.members.values())[:count]
            self.controller.queue(self.guild, self.lobby)
            embeds = self.controller.embeds(self.lobby, self.guild)
            self.assertIn(f"{count}/6 players", embeds[0].description)
            view = ScrimPanel(self.controller, self.lobby)
            self.assertEqual([b.label for b in view.children], ["Start scrim", "Test"])
            self.assertEqual(view.children[0].disabled, count < 6)
            self.assertFalse(view.children[1].disabled)

    async def test_queue_changes_reject_stale_start(self):
        revision = self.lobby["revision"]
        self.waiting.members.pop()
        await self.click("begin", revision=revision)
        self.assertEqual(self.store.active(), [])

    async def test_simultaneous_scrim_clicks_create_one_preview_without_game(self):
        await asyncio.gather(self.click("begin"), self.click("begin"))
        self.assertEqual(len(self.store.active()), 1)
        self.assertEqual(len(self.store.export(self.store.active()[0]["id"])["matches"]), 0)

    def example_pool(self):
        return [
            {**asdict(p), "member_id": -p.member_id, "username": f"Example {p.member_id}", "simulated": True}
            for p in self.pool
        ]

    async def test_example_includes_three_real_players_before_virtual_fillers_and_after_reroll(self):
        real_ids = {1, 4, 7}
        self.waiting.members = [self.members[mid] for mid in real_ids]
        self.controller.queue(self.guild, self.lobby)
        self.store.save_lobby(self.lobby)
        with patch.object(scrim_rivals, "fetch_test_pool", return_value=self.example_pool()) as fetch:
            await self.click("test", user=1)
        data = self.store.active()[0]
        self.assertEqual({mid for mid, p in data["players"].items() if not p["simulated"]}, real_ids)
        self.assertTrue(real_ids <= set(data["roster"]))
        self.assertEqual(sum(data["players"][mid]["simulated"] for mid in data["roster"]), 3)
        self.assertEqual(set(fetch.call_args.args[0]), {"p1", "p4", "p7"})
        await self.click("reroll", user=1)
        data = self.store.get(data["id"])
        self.assertTrue(real_ids <= set(data["roster"]))
        await self.click("start", user=1)
        self.assertEqual({m.id for m in self.stage.members}, real_ids)
        await self.click("finish", user=1)
        self.assertEqual({m.id for m in self.waiting.members}, real_ids)

    async def test_example_lists_excess_same_role_players_as_real_substitutes(self):
        self.waiting.members = [self.members[mid] for mid in (1, 2, 3)]
        self.controller.queue(self.guild, self.lobby)
        self.store.save_lobby(self.lobby)
        with patch.object(scrim_rivals, "fetch_test_pool", return_value=self.example_pool()):
            await self.click("test", user=1)
        data = self.store.active()[0]
        self.assertTrue({1, 2, 3} <= set(data["players"]))
        self.assertEqual(len({1, 2, 3} & set(data["roster"])), 2)
        bench = ({1, 2, 3} - set(data["roster"])).pop()
        cards = self.controller.embeds(self.store.get_lobby(self.lobby["id"]), self.guild, data)
        self.assertIn(f"<@{bench}>", cards[-1].description)

    async def test_example_includes_missing_profiles_as_test_flex_and_new_arrivals_after_restart(self):
        self.waiting.members = [self.members[mid] for mid in (1, 4)]
        self.controller.profile = lambda _, mid, __: ("p1", "Eastern", "Tank", None) if mid == 1 else None
        self.controller.queue(self.guild, self.lobby)
        self.store.save_lobby(self.lobby)
        with patch.object(scrim_rivals, "fetch_test_pool", return_value=self.example_pool()):
            await self.click("test", user=1)
        data = self.store.active()[0]
        self.assertTrue({1, 4} <= set(data["roster"]))
        self.assertTrue(data["players"][4]["test_roles_inferred"])
        self.assertEqual(tuple(data["players"][4]["roles"]), ("Tank", "DPS", "Support"))
        self.waiting.members.append(self.members[7])
        await self.controller.voice_update(
            self.members[7],
            SimpleNamespace(channel=None, mute=False),
            SimpleNamespace(channel=self.waiting, mute=False),
        )
        await self.controller.queue_tasks[self.lobby["id"]]
        self.assertIn(7, self.store.get(data["id"])["players"])
        self.waiting.members.append(self.members[8])
        await self.controller.restore()
        saved = self.store.get(data["id"])
        self.assertIn(8, saved["players"])
        eligible, excluded = self.controller.eligible(self.guild, saved)
        self.assertTrue({1, 4, 7, 8} <= {p.member_id for p in eligible})
        self.assertEqual(excluded, [])

    async def test_owner_solo_example_uses_virtual_players_without_moving_other_members(self):
        self.waiting.members = [self.members[1]]
        self.controller.queue(self.guild, self.lobby)
        self.store.save_lobby(self.lobby)
        with patch.object(scrim_rivals, "fetch_test_pool", return_value=self.example_pool()) as fetch:
            await self.click("test", user=1)
        fetch.assert_called_once()
        data = self.store.active()[0]
        self.assertTrue(data["test_mode"])
        self.assertIn(1, data["roster"])
        self.assertEqual(data["status"], "prepared")
        self.assertEqual(self.events, [])
        await self.click("start", user=1)
        data = self.store.get(data["id"])
        self.assertEqual(data["status"], "playing")
        self.assertEqual(Counter(data["roster"].values()), {"Tank": 2, "DPS": 2, "Support": 2})
        self.assertTrue(all(event[1] == 1 for event in self.events))
        self.assertNotIn(
            "<@-", str([e.to_dict() for e in self.controller.embeds(self.lobby, self.guild, data)])
        )
        await self.click("finish", user=1)
        await self.click("sub", user=1)
        await self.click("start", user=1)
        self.stage.members = []
        self.members[1].voice.channel = None
        await self.controller.end_empty_sessions()
        self.assertEqual(self.store.active(), [])
        self.assertIsNone(self.members[1].voice.channel)

    async def test_test_button_rejects_nonowner_and_disconnected_owner(self):
        with patch.object(scrim_rivals, "fetch_test_pool") as fetch:
            await self.click("test", user=999)
            self.waiting.members = []
            self.controller.queue(self.guild, self.lobby)
            self.store.save_lobby(self.lobby)
            await self.click("test", user=1)
        fetch.assert_not_called()
        self.assertEqual(self.store.active(), [])

    async def test_test_session_controls_reserved_for_host_even_for_other_manager(self):
        with patch.object(scrim_rivals, "fetch_test_pool", return_value=self.example_pool()):
            await self.click("test", user=1)
        data = self.store.active()[0]
        await self.click("start", user=1)
        await self.click("finish", user=999)
        self.assertEqual(self.store.get(data["id"])["status"], "playing")

    async def test_begin_moves_everyone_and_only_six_speak(self):
        data = await self.begin()
        self.assertEqual(Counter(data["roster"].values()), {"Tank": 2, "DPS": 2, "Support": 2})
        self.assertEqual(data["status"], "playing")
        self.assertEqual(len(self.stage.members), 9)
        self.assertEqual(self.waiting.members, [])
        for mid, member in self.members.items():
            self.assertEqual(member.voice.mute, mid not in data["roster"])
            self.assertEqual(self.stage.overwrites[member].speak, mid in data["roster"])
            member.move_to.assert_awaited_once_with(
                self.stage, reason=member.move_to.call_args.kwargs["reason"]
            )
        cards = self.controller.embeds(self.lobby, self.guild, data)
        self.assertEqual([c.title for c in cards][1:4], ["Tank", "DPS", "Support"])
        self.assertEqual(len(cards), 5)

    async def test_substitute_is_muted_after_arrival_and_unmuted_on_return_to_waiting(self):
        data = await self.begin()
        mid = next(mid for mid in self.members if mid not in data["roster"])
        member = self.members[mid]
        self.assertLess(
            self.events.index(("move", mid, self.stage.id)), self.events.index(("mute", mid, True))
        )
        await member.move_to(self.waiting)
        await self.controller.voice_update(
            member,
            SimpleNamespace(channel=self.stage, mute=True),
            SimpleNamespace(channel=self.waiting, mute=True),
        )
        self.assertFalse(member.voice.mute)

    async def test_finish_counts_once_and_makes_no_history_or_live_requests(self):
        with patch.object(scrim_rivals, "RivalsClient", side_effect=AssertionError("API disabled")):
            data = await self.begin()
            revision = data["revision"]
            await self.click("finish")
            await self.click("finish", revision=revision)
            data = self.store.get(data["id"])
            self.assertEqual(data["number"], 2)
            self.assertEqual(sum(p["played"] for p in data["players"].values()), 6)
            exported = self.store.export(data["id"])
            self.assertEqual(len(exported["matches"]), 1)
            self.assertEqual(exported["matches"][0]["status"], "completed")
            self.assertEqual([s["kind"] for s in exported["snapshots"]], ["live"])
            self.assertEqual(len(exported["matches"][0]["roster"]), 6)
            self.assertIsNotNone(exported["matches"][0]["ended_at"])

    async def test_substitution_updates_waiting_lineup_and_rejects_duplicate_click(self):
        data = await self.begin()
        await self.click("finish")
        before = self.store.get(data["id"])
        revision = before["revision"]
        self.events.clear()
        confirmation = await self.click("sub")
        after = self.store.get(data["id"])
        incoming = (set(after["roster"]) - set(before["roster"])).pop()
        outgoing = (set(before["roster"]) - set(after["roster"])).pop()
        reply = confirmation.followup.send.call_args.args[0]
        self.assertIn(f"Out: <@{outgoing}>", reply)
        self.assertIn(f"In: <@{incoming}> ({after['roster'][incoming]})", reply)
        self.assertEqual(before["roster"][outgoing], after["roster"][incoming])
        self.assertEqual(self.members[outgoing].voice.channel, self.waiting)
        self.assertEqual(self.members[incoming].voice.channel, self.waiting)
        self.assertEqual(self.events, [])
        await self.click("sub", revision=revision)
        self.assertEqual(self.store.get(data["id"])["roster"], after["roster"])
        self.assertIn(incoming, after["protected_in"])
        await self.click("start")
        self.assertEqual(self.members[incoming].voice.channel, self.stage)
        self.assertFalse(self.members[incoming].voice.mute)
        self.assertTrue(self.members[outgoing].voice.mute)
        await self.click("finish")
        self.assertEqual(self.store.get(data["id"])["protected_in"], [])

    async def test_match_end_returns_everyone_and_stays_waiting_until_manager_start(self):
        data = await self.begin()
        await self.click("finish")
        self.assertEqual(len(self.waiting.members), 9)
        self.assertEqual(self.stage.members, [])
        self.assertTrue(all(not member.voice.mute for member in self.members.values()))
        await self.controller.refresh_queue(self.lobby["id"])
        self.assertEqual(len(self.waiting.members), 9)
        await self.click("start")
        self.assertEqual(len(self.stage.members), 9)
        self.assertEqual(self.store.get(data["id"])["status"], "playing")

    async def test_player_connect_permission_remains_denied_after_bot_admission(self):
        await self.begin()
        for member in self.members.values():
            self.assertFalse(self.stage.overwrites[member].connect)

    async def test_preexisting_server_mute_restored_after_each_game(self):
        self.members[1].voice.mute = True
        await self.begin()
        await self.click("finish")
        self.assertTrue(self.members[1].voice.mute)

    async def test_failed_return_keeps_completed_count_and_sync_retries_waiting(self):
        data = await self.begin()
        member = self.members[1]
        original_move = member.move_to.side_effect
        member.move_to.side_effect = discord.HTTPException(
            SimpleNamespace(status=500, reason="Server error"), {"code": 0, "message": "Temporary failure"}
        )
        await self.click("finish")
        saved = self.store.get(data["id"])
        self.assertEqual(saved["status"], "prepared")
        self.assertEqual(sum(p["played"] for p in saved["players"].values()), 6)
        self.assertIn("sync_error", saved)
        member.move_to.side_effect = original_move
        await self.click("sync")
        self.assertEqual(self.stage.members, [])
        self.assertEqual(len(self.waiting.members), 9)
        self.assertEqual(sum(p["played"] for p in self.store.get(data["id"])["players"].values()), 6)

    async def test_direct_idle_arrival_is_returned_to_waiting(self):
        member = self.members[2]
        self.waiting.members.remove(member)
        self.stage.members.append(member)
        member.voice.channel = self.stage
        await self.controller.voice_update(
            member,
            SimpleNamespace(channel=self.waiting, mute=False),
            SimpleNamespace(channel=self.stage, mute=False),
        )
        self.assertEqual(member.voice.channel, self.waiting)

    async def test_empty_rooms_end_session_without_counting_unfinished_game_and_restore_on_rejoin(self):
        data = await self.begin()
        await self.controller.end_empty_sessions()
        self.assertEqual(self.store.get(data["id"])["status"], "playing")
        self.stage.members = []
        for member in self.members.values():
            member.voice.channel = None
        await self.controller.end_empty_sessions()
        self.assertEqual(self.store.active(), [])
        self.assertEqual(len(self.waiting.members), 0)
        lobby = self.store.get_lobby(self.lobby["id"])
        self.assertIsNone(lobby["session_id"])
        self.assertEqual(lobby["ready_ids"], [])
        exported = self.store.export(data["id"])
        self.assertEqual(exported["matches"][0]["status"], "aborted")
        self.assertTrue(all(p["played"] == 0 for p in exported["session"]["players"].values()))
        self.assertEqual(self.stage.overwrites, {})
        returning = self.members[1]
        returning.voice.channel = self.waiting
        self.waiting.members.append(returning)
        await self.controller.voice_update(
            returning,
            SimpleNamespace(channel=None, mute=True),
            SimpleNamespace(channel=self.waiting, mute=True),
        )
        self.assertFalse(returning.voice.mute)
        self.assertEqual(self.store.pending_mute_restores(self.guild.id, returning.id), [])

    async def test_panel_replaces_cards_instead_of_posting_more_messages(self):
        await self.controller.panel(self.lobby, self.guild)
        await self.begin()
        await self.click("finish")
        self.assertEqual(self.control.send.await_count, 1)
        self.assertGreater(self.message.edit.await_count, 1)
        self.assertEqual(len(self.message.edit.call_args.kwargs["embeds"]), 5)

    async def test_failed_stage_sync_saves_actual_game_start_and_can_retry(self):
        self.controller.sync_stage = AsyncMock(side_effect=ValueError("Stage unavailable"))
        data = await self.begin()
        self.assertEqual(data["status"], "playing")
        self.assertEqual(data["sync_error"], "Stage unavailable")
        self.assertEqual(len(self.store.export(data["id"])["matches"]), 1)
        await self.click("start")
        self.assertEqual(self.store.get(data["id"])["status"], "playing")

    async def test_restore_disables_old_solo_mode_without_api_requests(self):
        self.store.arm_test(10, "Marvel Rivals", 1, [])
        with patch.object(scrim_rivals, "RivalsClient", side_effect=AssertionError("API disabled")):
            await self.controller.restore()
        self.assertFalse(self.store.test_settings(10, "Marvel Rivals")["enabled"])
        self.assertEqual(self.store.active(), [])

    async def test_persistent_controls_omit_repeat_ready_and_multiple_sub_choices(self):
        data = await self.begin()
        view = ScrimPanel(self.controller, self.lobby, data)
        self.assertTrue(view.is_persistent())
        self.assertEqual(
            [b.label for b in view.children],
            ["Sub in", "Reroll teams", "Edit lineup"],
        )
        self.assertTrue(view.children[0].disabled)
        self.assertTrue(view.children[1].disabled)
        self.assertTrue(view.children[2].disabled)

    async def test_start_scrim_is_only_a_preview_until_start_game(self):
        data = await self.begin(start=False)
        self.assertEqual(data["status"], "prepared")
        self.assertIsNone(data["match_id"])
        self.assertEqual(self.store.export(data["id"])["matches"], [])
        self.assertEqual(len(self.waiting.members), 9)
        self.assertEqual(self.stage.members, [])
        self.assertEqual(self.events, [])
        self.assertEqual(Counter(data["roster"].values()), {"Tank": 2, "DPS": 2, "Support": 2})
        view = ScrimPanel(self.controller, self.lobby, data)
        self.assertTrue(view.children[0].disabled)
        self.assertFalse(view.children[1].disabled)
        self.assertFalse(view.children[2].disabled)
        self.assertIn("Waiting", self.controller.embeds(self.lobby, self.guild, data)[0].title)
        await self.click("start")
        self.assertEqual(len(self.store.export(data["id"])["matches"]), 1)
        self.assertEqual(len(self.stage.members), 9)

    async def test_full_reroll_includes_bench_ignores_counts_and_rejects_duplicate_click(self):
        data = await self.begin(start=False)
        before = dict(data["roster"])
        bench = set(data["players"]) - set(before)
        for mid in bench:
            data["players"][mid]["played"] = 99
            data["players"][mid]["last_played"] = 99
        data["protected_in"] = list(before)
        self.store.save(data)

        def order(population, count):
            if population and isinstance(population[0], Player):
                return sorted(population, key=lambda p: p.member_id not in bench)
            return list(population)

        with patch("rionnag.scrims.scrims.random.sample", side_effect=order):
            await self.click("reroll")
        updated = self.store.get(data["id"])
        self.assertTrue(bench <= set(updated["roster"]))
        self.assertEqual(Counter(updated["roster"].values()), {"Tank": 2, "DPS": 2, "Support": 2})
        self.assertEqual(updated["protected_in"], [])
        self.assertTrue(all(updated["players"][mid]["played"] == 99 for mid in bench))
        self.assertEqual(self.events, [])
        await self.click("reroll", revision=data["revision"])
        self.assertEqual(self.store.get(data["id"])["roster"], updated["roster"])
        export = self.store.export(data["id"])
        self.assertEqual(len([e for e in export["events"] if e["action"] == "rerolled"]), 1)
        self.assertEqual(export["matches"], [])

    async def test_reroll_cannot_change_running_game_or_bypass_manager_permission(self):
        data = await self.begin(start=False)
        await self.click("reroll", user=2)
        self.assertEqual(self.store.get(data["id"])["roster"], data["roster"])
        await self.click("start")
        snapshot = self.store.export(data["id"])["matches"][0]["roster"]
        await self.click("reroll")
        self.assertEqual(self.store.get(data["id"])["roster"], data["roster"])
        self.assertEqual(self.store.export(data["id"])["matches"][0]["roster"], snapshot)

    async def test_restore_preview_does_not_start_game_or_move_anyone(self):
        data = await self.begin(start=False)
        await self.controller.restore()
        self.assertEqual(self.store.get(data["id"])["status"], "prepared")
        self.assertEqual(self.store.export(data["id"])["matches"], [])
        self.assertEqual(self.events, [])
        self.assertEqual(len(self.waiting.members), 9)

    async def test_ending_preview_creates_no_aborted_game(self):
        data = await self.begin(start=False)
        self.waiting.members = []
        for member in self.members.values():
            member.voice.channel = None
        await self.controller.refresh_queue(self.lobby["id"])
        self.assertEqual(self.store.export(data["id"])["matches"], [])
        self.assertEqual(self.store.get(data["id"])["status"], "ended")

    async def test_last_voice_departure_ends_preview_and_old_end_button_is_rejected(self):
        data = await self.begin(start=False)
        rejected = await self.click("end")
        self.assertIn("automatically", rejected.followup.send.call_args.args[0])
        self.assertEqual(self.store.get(data["id"])["status"], "prepared")
        last = self.members[1]
        self.waiting.members = []
        for member in self.members.values():
            member.voice.channel = None
        await self.controller.voice_update(
            last,
            SimpleNamespace(channel=self.waiting, mute=False),
            SimpleNamespace(channel=None, mute=False),
        )
        await self.controller.queue_tasks[self.lobby["id"]]
        self.assertEqual(self.store.get(data["id"])["status"], "ended")
        self.assertIsNone(self.store.get_lobby(self.lobby["id"])["session_id"])

    async def test_compact_match_card_counts_wins_losses_and_keeps_stats_out(self):
        data = await self.begin()
        for index, win in enumerate((True, False), 1):
            current = self.store.get(data["id"])
            if current["status"] == "prepared":
                await self.click("start")
                current = self.store.get(data["id"])
            match = self.store.match(current["match_id"])
            await self.click("finish")
            payload = {
                "teams": [
                    {
                        "is_win": win,
                        "players": [
                            {"player_uid": str(p["member_id"]), "name": p["username"], "kills": 99}
                            for p in match["roster"]
                        ],
                    }
                ]
            }
            self.store.snapshot(match["id"], "details", payload, f"game-{index}")
        current = self.store.get(data["id"])
        header = self.controller.embeds(self.lobby, self.guild, current)[0]
        self.assertEqual(header.title, "Scrim · Game 3 · 1–1 · Between games")
        self.assertIsNone(header.description)
        self.assertIsNone(header.footer.text)
        self.assertEqual(
            [(f.name, f.value) for f in header.fields],
            [("Game 1", "🟢 Win"), ("Game 2", "🔴 Loss")],
        )
        self.assertEqual(header.color.value, self.controller.color)
        await self.click("start")
        header = self.controller.embeds(self.lobby, self.guild, self.store.get(data["id"]))[0]
        self.assertIn("In game", header.title)
        self.assertEqual(header.fields[-1].value, "In game")

    async def test_match_history_paginates_without_dropping_games(self):
        data = await self.begin(start=False)
        roster = [{**data["players"][mid], "role": role} for mid, role in data["roster"].items()]

        with self.store.connection() as db:
            for number in range(1, 27):
                db.execute(
                    "INSERT INTO scrim_matches(session_id,number,status,started_at,ended_at,roster,data) "
                    "VALUES (?,?,'completed',0,1,?,'{}')",
                    (data["id"], number, json.dumps(roster)),
                )
        data["number"] = 27
        self.store.save(data)
        header = self.controller.embeds(self.lobby, self.guild, data)[0]
        self.assertEqual(len(header.fields), 25)
        self.assertEqual(header.fields[0].name, "Game 2")
        await self.click("older")
        current = self.store.get(data["id"])
        header = self.controller.embeds(self.lobby, self.guild, current)[0]
        self.assertEqual([f.name for f in header.fields], ["Game 1"])
        self.assertIn(
            "Newer games",
            [b.label for b in ScrimPanel(self.controller, self.lobby, current).children],
        )

    def test_random_first_team_and_single_sub_fairness(self):
        teams = {tuple(sorted(random_team(self.pool))) for _ in range(30)}
        self.assertGreater(len(teams), 1)
        team = random_team(self.pool)
        history = {p.member_id: asdict(p) for p in self.pool}
        # After a completion the least-used bench player replaces the same role.
        for p in self.pool:
            if p.member_id in team:
                p.played, p.last_played = 1, 1
                history[p.member_id] = asdict(p)
        protected = []
        for _ in range(6):
            before = dict(team)
            team, incoming, outgoing = substitute_one(self.pool, team, history, protected)
            self.assertEqual(before[outgoing], team[incoming])
            self.assertNotIn(outgoing, protected)
            protected.append(incoming)
        with self.assertRaisesRegex(ValueError, "No eligible role-compatible"):
            substitute_one(self.pool, team, history, protected)

    def test_absent_starter_can_be_replaced_even_with_equal_counts(self):
        team = random_team(self.pool)
        missing = next(iter(team))
        present = [p for p in self.pool if p.member_id != missing]
        new, _, outgoing = substitute_one(present, team, {p.member_id: asdict(p) for p in self.pool})
        self.assertEqual(outgoing, missing)
        self.assertEqual(Counter(new.values()), {"Tank": 2, "DPS": 2, "Support": 2})
