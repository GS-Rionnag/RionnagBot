import json
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from rionnag import config
from rionnag.services.scrim_hosting import HostingService, HostSlot, generate_slots, role_team
from rionnag.storage import Store
from rionnag.ui.scrim_hosting import DayPicker, HostingBoard, SlotPicker, day_groups
from scrim_collector.hosting import invalid_request, process_job


def player(mid, best, second):
    return {
        "member_id": mid,
        "answers": {
            "preferred_role_1": best,
            "preferred_role_2": second,
            "time_zone": "Eastern Time (ET)",
            "availability_days": {
                day: {"start": 16, "end": 24}
                for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
            },
        },
    }


def roster():
    return [
        player(i + 1, role, "DPS" if role != "DPS" else "Tank")
        for i, role in enumerate(("Tank", "Tank", "DPS", "DPS", "Support", "Support"))
    ]


class HostingRulesTests(unittest.TestCase):
    def test_attendance_ranks_before_role_fit(self):
        six = HostSlot(1, 2, set(range(6)), set(), {}, 0, False)
        nine = HostSlot(3, 4, set(range(9)), set(), {}, 1, False)
        confirmed = HostSlot(5, 6, set(range(6)), {1}, {}, 2, False)
        self.assertEqual(sorted([six, nine, confirmed], key=lambda s: s.order), [confirmed, nine, six])

    def test_days_rank_confirmed_then_distinct_available_without_double_counting(self):
        day1 = int(datetime(2026, 10, 8, 20, tzinfo=UTC).timestamp())
        day2 = day1 + 86400
        day3 = day2 + 86400
        slots = [
            HostSlot(day1, day1 + 7200, set(range(9)), set(), {}, 0, False),
            HostSlot(day1 + 1800, day1 + 9000, set(range(9)), set(), {}, 0, False),
            HostSlot(day2, day2 + 7200, set(range(6)), {1}, {}, 1, False),
            HostSlot(day3, day3 + 7200, set(range(6)), set(), {}, 0, False),
        ]
        groups = day_groups(slots, ZoneInfo("America/New_York"))
        self.assertEqual(list(groups), ["2026-10-09", "2026-10-08", "2026-10-10"])
        self.assertEqual(DayPicker.counts(groups["2026-10-08"]), (0, 9))

    def test_never_assign_worst_role_and_reject_impossible_six(self):
        players = [player(i, "Tank", "DPS") for i in range(8)]
        self.assertEqual(role_team(players)[0], {})
        self.assertEqual(generate_slots(players, {}, time.time()), [])

    def test_solver_reassigns_flexible_players_and_prefers_primaries(self):
        players = roster() + [player(9, "DPS", "Support")]
        team, secondary = role_team(players)
        self.assertEqual(len(team), 6)
        self.assertEqual(secondary, 0)
        players[5]["answers"]["preferred_role_1"] = "DPS"
        players[5]["answers"]["preferred_role_2"] = "Tank"
        team, secondary = role_team(players)
        self.assertEqual(team[9], "Support")
        self.assertEqual(secondary, 1)

    def test_full_duration_unique_players_and_confirmed_composition(self):
        now = datetime(2026, 10, 8, 20, tzinfo=UTC).timestamp()
        players = roster()
        slots = generate_slots(players, {}, now, days=1)
        self.assertTrue(slots)
        self.assertFalse(any(s.ready for s in slots))
        start = slots[0].start
        slots = generate_slots(players, {start: set(range(1, 7))}, now, days=1)
        self.assertTrue(slots[0].ready)
        self.assertEqual(slots[0].start, start)
        self.assertEqual(len(slots[0].lineup), 6)
        self.assertEqual(generate_slots(players[:5] + [players[0]], {}, now, days=1), [])
        for p in players:
            p["answers"]["availability_days"] = {"Thursday": {"start": 16, "end": 17}}
        self.assertEqual(generate_slots(players, {}, now, days=1), [])

    def test_six_votes_without_role_balance_never_ready_and_no_vote_cap(self):
        players = roster() + [player(i, "DPS", "Tank") for i in range(7, 13)]
        now = datetime(2026, 10, 8, 20, tzinfo=UTC).timestamp()
        start = generate_slots(players, {}, now, days=1)[0].start
        slot = next(
            s for s in generate_slots(players, {start: set(range(7, 13))}, now, days=1) if s.start == start
        )
        self.assertFalse(slot.ready)
        slot = generate_slots(players, {start: set(range(1, 13))}, now, days=1)[0]
        self.assertTrue(slot.ready)
        self.assertEqual(len(slot.confirmed), 12)


class HostingPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name) / "test.sqlite3")
        self.start = int(time.time()) + 86400
        self.settings = dict(duration=7200, destination=42, min_rank="Diamond", max_rank="Grandmaster")
        self.store.configure_host(config.SCRIM_HOST_CHANNEL_ID, **self.settings)
        self.team = {1: "Tank", 2: "Tank", 3: "DPS", 4: "DPS", 5: "Support", 6: "Support"}

    def queue(self):
        return self.store.queue_host_advert(
            config.SCRIM_HOST_CHANNEL_ID, self.start, self.settings, self.team, 99
        )

    async def test_idempotent_persisted_queue_and_departure_cleanup(self):
        self.assertTrue(self.queue())
        self.assertFalse(self.queue())
        job = Store(self.store.path).host_adverts(config.SCRIM_HOST_CHANNEL_ID)[self.start]
        self.assertEqual(job["content"], f"LFS at <t:{self.start}:F>")
        self.store.set_host_vote(config.SCRIM_HOST_CHANNEL_ID, self.start, 1, True)
        self.store.delete_member(config.GUILD_ID, 1)
        self.assertEqual(self.store.host_votes(config.SCRIM_HOST_CHANNEL_ID), {})
        self.assertTrue(self.store.claim_host_advert(config.SCRIM_HOST_CHANNEL_ID, self.start))
        self.assertFalse(self.store.claim_host_advert(config.SCRIM_HOST_CHANNEL_ID, self.start))

    async def test_prune_invalid_votes_without_capping(self):
        for mid in range(12):
            self.store.set_host_vote(1, self.start, mid, True)
        self.store.prune_host_votes(1, {self.start: set(range(10))})
        self.assertEqual(len(self.store.host_votes(1)[self.start]), 10)

    async def test_publish_rechecks_authorization_and_preview(self):
        service = HostingService(SimpleNamespace(), SimpleNamespace(store=self.store), None)
        slot = SimpleNamespace(start=self.start, ready=True, lineup=self.team)
        service.snapshot = lambda: [slot]
        service.sync = AsyncMock()
        interaction = SimpleNamespace(user=SimpleNamespace(id=99))
        service.manager = lambda i: False
        with self.assertRaisesRegex(ValueError, "Only"):
            await service.publish(interaction, self.start, ())
        service.manager = lambda i: True
        with self.assertRaisesRegex(ValueError, "changed"):
            await service.publish(interaction, self.start, ())
        expected = service.preview_token(slot, self.settings)
        await service.publish(interaction, self.start, expected)
        with self.assertRaisesRegex(ValueError, "already"):
            await service.publish(interaction, self.start, expected)

    async def test_uncertain_delivery_recovers_without_resending(self):
        self.queue()
        self.store.claim_host_advert(config.SCRIM_HOST_CHANNEL_ID, self.start)
        job = self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID)[self.start]
        message = SimpleNamespace(id=77, author=SimpleNamespace(id=99), content=job["content"])

        async def history(**kwargs):
            yield message

        channel = SimpleNamespace(history=history, send=AsyncMock())
        client = SimpleNamespace(get_channel=lambda cid: channel, user=SimpleNamespace(id=99))
        await process_job(client, self.store, job, {42})
        self.assertEqual(self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID)[self.start]["message_id"], 77)
        channel.send.assert_not_awaited()

    async def test_invalid_destination_never_sends(self):
        self.queue()
        job = self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID)[self.start]
        await process_job(SimpleNamespace(), self.store, job, set())
        self.assertEqual(
            self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID)[self.start]["status"], "failed"
        )

    async def test_successful_delivery_validates_and_records_once(self):
        self.queue()
        job = self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID)[self.start]
        role = config.load_forms()["marvel-rivals"]["team_role"]
        members = {
            mid: SimpleNamespace(id=mid, bot=False, roles=[SimpleNamespace(id=role)])
            for mid in (*self.team, 99)
        }
        guild = SimpleNamespace(owner_id=99, fetch_member=AsyncMock(side_effect=lambda mid: members[mid]))
        channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=123)))
        client = SimpleNamespace(
            get_channel=lambda cid: channel, get_guild=lambda gid: guild, user=SimpleNamespace(id=99)
        )
        for mid in self.team:
            self.store.set_host_vote(config.SCRIM_HOST_CHANNEL_ID, self.start, mid, True)
        with patch.object(self.store, "scrim_candidates", return_value=roster()):
            with patch("scrim_collector.hosting.covers_interval", return_value=True):
                await process_job(client, self.store, job, {42})
                await process_job(client, self.store, job, {42})
        channel.send.assert_awaited_once()
        sent = self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID)[self.start]
        self.assertEqual((sent["status"], sent["message_id"]), ("sent", 123))

    async def test_board_recovers_existing_message_without_duplicates(self):
        message = SimpleNamespace(
            id=123,
            author=SimpleNamespace(id=44),
            embeds=[SimpleNamespace(footer=SimpleNamespace(text="Rionnag scrim hosting board"))],
            edit=AsyncMock(),
        )

        async def history(**kwargs):
            yield message

        channel = SimpleNamespace(
            history=history, send=AsyncMock(), fetch_message=AsyncMock(return_value=message)
        )
        guild = SimpleNamespace(get_channel=lambda cid: channel)
        bot = SimpleNamespace(get_guild=lambda gid: guild, user=SimpleNamespace(id=44))
        service = HostingService(bot, SimpleNamespace(store=self.store, forms=config.load_forms()), None)
        service.players = lambda: roster()
        service.snapshot = lambda: []
        await service.sync()
        await service.sync()
        channel.send.assert_not_awaited()
        message.edit.assert_awaited_once()
        self.assertEqual(self.store.host_settings(config.SCRIM_HOST_CHANNEL_ID)["message_id"], 123)

    async def test_outbound_revalidates_withdrawn_vote(self):
        self.queue()
        job = self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID)[self.start]
        with patch("scrim_collector.hosting.time.time", return_value=self.start - 1):
            for mid in self.team:
                self.store.set_host_vote(config.SCRIM_HOST_CHANNEL_ID, self.start, mid, True)
            players = {p["member_id"]: p for p in roster()}
            role = config.load_forms()["marvel-rivals"]["team_role"]
            members = {
                mid: SimpleNamespace(id=mid, bot=False, roles=[SimpleNamespace(id=role)])
                for mid in (*self.team, 99)
            }
            with patch("scrim_collector.hosting.covers_interval", return_value=True):
                self.assertIsNone(invalid_request(self.store, job, players, members, 99, 88))
                self.store.set_host_vote(config.SCRIM_HOST_CHANNEL_ID, self.start, 1, False)
                self.assertIn("confirmed", invalid_request(self.store, job, players, members, 99, 88))

    async def test_persistent_controls_and_paginated_dropdown(self):
        service = SimpleNamespace(store=self.store, channel_id=config.SCRIM_HOST_CHANNEL_ID)
        board = HostingBoard(service)
        self.assertTrue(board.is_persistent())
        now = datetime(2026, 10, 8, 20, tzinfo=UTC).timestamp()
        slots = generate_slots(roster(), {}, now)
        view = SlotPicker(service, 1, slots, "add")
        self.assertEqual(len(view.children[0].options), 10)
        self.assertLessEqual(len(json.dumps(view.embed().to_dict())), 6000)
        board.stop()
        view.stop()

    async def test_day_first_ui_has_no_time_choices_until_day_selected(self):
        now = datetime(2026, 10, 8, 20, tzinfo=UTC).timestamp()
        slots = generate_slots(roster(), {}, now)
        service = SimpleNamespace(
            store=self.store, channel_id=config.SCRIM_HOST_CHANNEL_ID, players=roster, snapshot=lambda: slots
        )
        view = DayPicker(service, 1, slots, "add")
        self.assertEqual(len(view.children), 1)
        self.assertNotIn(":F>", json.dumps(view.embed().to_dict()))
        self.assertIn("available", view.children[0].options[0].description)
        day = view.children[0].options[0].value
        interaction = SimpleNamespace(
            data={"values": [day]}, response=SimpleNamespace(edit_message=AsyncMock())
        )
        await view.selected(interaction)
        picker = interaction.response.edit_message.call_args.kwargs["view"]
        self.assertIsInstance(picker, SlotPicker)
        self.assertEqual({picker.date_key(s) for s in picker.slots}, {day})
        self.assertIn("available", picker.children[0].options[0].description)
        picker.stop()
        view.stop()

    async def test_board_empty_until_first_confirmation_then_mentions_best_role(self):
        service = HostingService(SimpleNamespace(), SimpleNamespace(store=self.store), None)
        service.players = roster
        now = datetime(2026, 10, 8, 20, tzinfo=UTC).timestamp()
        slots = generate_slots(roster(), {}, now)
        embed = service.board_embed(slots)
        self.assertEqual(len(embed.fields), 0)
        self.assertNotIn("<t:", embed.description)
        slots[0].confirmed.add(1)
        embed = service.board_embed(slots)
        self.assertEqual(len(embed.fields), 1)
        self.assertIn("1 confirmed", embed.fields[0].value)
        self.assertIn("Tank: <@1>", embed.fields[0].value)
        self.assertIn("**Available players**\n<@1> <@2> <@3> <@4> <@5> <@6>", embed.fields[0].value)
        self.assertNotIn("6 available", embed.fields[0].value)

    async def test_confirmed_players_group_by_best_role_in_tank_dps_support_order(self):
        service = HostingService(SimpleNamespace(), SimpleNamespace(store=self.store), None)
        service.players = roster
        slot = HostSlot(self.start, self.start + 7200, set(range(1, 7)), {1, 2, 3, 4, 5, 6}, {}, 0, True)
        value = service.board_embed([slot]).fields[0].value
        self.assertIn("Tank: <@1>, <@2>\nDPS: <@3>, <@4>\nSupport: <@5>, <@6>", value)
        self.assertNotIn("Best role:", value)

    async def test_public_board_orders_confirmed_count_then_soonest_start(self):
        service = HostingService(SimpleNamespace(), SimpleNamespace(store=self.store), None)
        service.players = roster
        soon = HostSlot(self.start, self.start + 7200, set(range(1, 7)), {1}, {}, 2, False)
        later = HostSlot(self.start + 86400, self.start + 93600, set(range(1, 10)), {2}, {}, 0, False)
        most = HostSlot(self.start + 172800, self.start + 180000, set(range(1, 7)), {1, 2}, {}, 2, False)
        embed = service.board_embed([later, most, soon])
        self.assertEqual(len(embed.fields), 3)
        self.assertIn(f"<t:{most.start}:F>", embed.fields[0].value)
        self.assertIn(f"<t:{soon.start}:F>", embed.fields[1].value)
        self.assertIn(f"<t:{later.start}:F>", embed.fields[2].value)
        cards = service.board_embeds([later, most, soon])
        self.assertEqual(
            [card.title for card in cards],
            ["HOST SCRIMS", f"<t:{most.start}:F>", f"<t:{soon.start}:F>", f"<t:{later.start}:F>"],
        )
        self.assertTrue(cards[1].footer.text.startswith("First place"))
        self.assertTrue(cards[2].footer.text.startswith("Second place"))
        self.assertTrue(cards[3].footer.text.startswith("Third place"))
        self.assertEqual(len(cards[0].fields), 0)
        self.assertIn(f"<t:{most.start}:F>", cards[1].description)

    async def test_board_shows_top_three_sessions_even_without_attendance_ties(self):
        service = HostingService(SimpleNamespace(), SimpleNamespace(store=self.store), None)
        service.players = roster
        tied = [
            HostSlot(
                self.start + i * 86400,
                self.start + i * 86400 + 7200,
                set(range(1, 10)),
                {1, 2},
                {},
                i % 3,
                False,
            )
            for i in range(5)
        ]
        less_available = HostSlot(
            self.start - 3600, self.start + 3600, set(range(1, 7)), {1, 2}, {}, 0, False
        )
        less_confirmed = HostSlot(self.start - 7200, self.start, set(range(1, 20)), {1}, {}, 0, False)
        embed = service.board_embed([less_available, less_confirmed, *reversed(tied)])
        self.assertEqual(len(embed.fields), 3)
        self.assertIn(f"<t:{less_available.start}:F>", embed.fields[0].value)
        self.assertIn(f"<t:{tied[0].start}:F>", embed.fields[1].value)
        self.assertIn(f"<t:{tied[1].start}:F>", embed.fields[2].value)
        embed = service.board_embed(tied[:2])
        self.assertEqual(len(embed.fields), 2)
        self.assertEqual(len(service.board_embeds([])), 1)
        self.assertEqual(len(service.board_embeds(tied[:2])), 3)

    async def test_time_selection_confirms_only_the_exact_chosen_session(self):
        service = HostingService(SimpleNamespace(), SimpleNamespace(store=self.store), None)
        now = datetime(2026, 10, 8, 20, tzinfo=UTC).timestamp()
        slots = generate_slots(roster(), {}, now)
        service.snapshot = lambda: slots
        service.sync = AsyncMock()
        picker = SlotPicker(service, 1, slots, "add")
        chosen = slots[0].start
        interaction = SimpleNamespace(
            data={"values": [str(chosen)]},
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await picker.selected(interaction)
        self.assertEqual(self.store.host_votes(config.SCRIM_HOST_CHANNEL_ID), {chosen: {1}})
        service.sync.assert_awaited_once()
        picker.stop()

    async def test_public_board_has_only_player_controls(self):
        board = HostingBoard(SimpleNamespace())
        self.assertEqual([item.label for item in board.children], ["Choose a day", "My selections"])
        board.stop()

    def approval_fixture(self):
        async def history(**kwargs):
            if False:
                yield None

        dm = SimpleNamespace(history=history, send=AsyncMock(return_value=SimpleNamespace(id=55)))
        owner = SimpleNamespace(id=99, dm_channel=dm)
        guild = SimpleNamespace(owner_id=99, owner=owner)
        bot = SimpleNamespace(get_guild=lambda gid: guild, user=SimpleNamespace(id=44))
        service = HostingService(bot, SimpleNamespace(store=self.store, forms=config.load_forms()), None)
        slot = SimpleNamespace(
            start=self.start,
            end=self.start + 7200,
            confirmed=set(self.team),
            ready=True,
            lineup=self.team,
            order=(0,),
        )
        service.snapshot = lambda: [slot]
        service.sync = AsyncMock()
        return service, slot, dm

    async def test_owner_notified_once_and_no_does_not_publish(self):
        service, slot, dm = self.approval_fixture()
        await service.notify_ready()
        await service.notify_ready()
        dm.send.assert_awaited_once()
        view = dm.send.call_args.kwargs["view"]
        self.assertTrue(view.is_persistent())
        self.assertEqual([button.label for button in view.children], ["Yes, send advert", "No"])
        await service.decide_notice(SimpleNamespace(user=SimpleNamespace(id=99)), self.start, 1, False)
        await service.notify_ready()
        dm.send.assert_awaited_once()
        self.assertEqual(self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID), {})
        self.assertEqual(
            self.store.host_notices(config.SCRIM_HOST_CHANNEL_ID)[self.start]["status"], "declined"
        )
        view.stop()

    async def test_only_owner_yes_queues_anonymous_advert_once(self):
        service, slot, dm = self.approval_fixture()
        service.advert_ranks = AsyncMock(return_value=(("Grandmaster", "Celestial"),) * 6)
        await service.notify_ready()
        with self.assertRaisesRegex(ValueError, "Only the server owner"):
            await service.decide_notice(SimpleNamespace(user=SimpleNamespace(id=77)), self.start, 1, True)
        interaction = SimpleNamespace(user=SimpleNamespace(id=99))
        await service.decide_notice(interaction, self.start, 1, True)
        job = self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID)[self.start]
        self.assertIn("Player6 - Grandmaster; Celestial Peak", job["content"])
        self.assertEqual(job["requested_by"], 99)
        with self.assertRaisesRegex(ValueError, "already answered"):
            await service.decide_notice(interaction, self.start, 1, True)
        await service.notify_ready()
        dm.send.assert_awaited_once()

    async def test_approval_expires_when_team_breaks_and_rearms_with_new_generation(self):
        service, slot, dm = self.approval_fixture()
        await service.notify_ready()
        slot.ready = False
        with self.assertRaisesRegex(ValueError, "no longer has six"):
            await service.decide_notice(SimpleNamespace(user=SimpleNamespace(id=99)), self.start, 1, True)
        self.assertEqual(self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID), {})
        slot.ready = True
        await service.notify_ready()
        self.assertEqual(dm.send.await_count, 2)
        self.assertEqual(self.store.host_notices(config.SCRIM_HOST_CHANNEL_ID)[self.start]["generation"], 2)
        with self.assertRaisesRegex(ValueError, "no longer current"):
            await service.decide_notice(SimpleNamespace(user=SimpleNamespace(id=99)), self.start, 1, True)

    async def test_six_confirmations_without_valid_roles_do_not_notify(self):
        service, slot, dm = self.approval_fixture()
        slot.ready = False
        await service.notify_ready()
        dm.send.assert_not_awaited()

    async def test_interrupted_dm_send_recovers_without_duplicate(self):
        service, slot, dm = self.approval_fixture()
        dm.send.side_effect = OSError("Acknowledgement lost")
        with self.assertRaises(OSError):
            await service.notify_ready()
        recovered = SimpleNamespace(
            id=55,
            author=SimpleNamespace(id=44),
            edit=AsyncMock(),
            embeds=[SimpleNamespace(footer=SimpleNamespace(text=f"Scrim host approval · {self.start} · 1"))],
        )

        async def history(**kwargs):
            yield recovered

        dm.history = history
        await service.notify_ready()
        self.assertEqual(dm.send.await_count, 1)
        recovered.edit.assert_awaited_once()
        self.assertEqual(self.store.host_notices(config.SCRIM_HOST_CHANNEL_ID)[self.start]["message_id"], 55)

    async def test_pending_dm_approval_restores_after_restart_without_renotifying(self):
        service, slot, dm = self.approval_fixture()
        await service.notify_ready()
        restored, _, _ = self.approval_fixture()
        views = []
        restored.bot.add_view = lambda view, **kwargs: views.append((view, kwargs))
        restored.register_approvals()
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0][1]["message_id"], 55)
        self.assertTrue(views[0][0].is_persistent())
        await restored.notify_ready()
        views[0][0].stop()

    async def test_anonymous_rank_content_never_contains_identity(self):
        ranks = [("Grandmaster", "Celestial")] * 6
        self.store.queue_host_advert(
            config.SCRIM_HOST_CHANNEL_ID, self.start, self.settings, self.team, 99, ranks
        )
        job = self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID)[self.start]
        self.assertIn("Player1 - Grandmaster; Celestial Peak", job["content"])
        self.assertIn("Player6 - Grandmaster; Celestial Peak", job["content"])
        self.assertNotIn("<@", job["content"])
        self.assertEqual(len(job["content"].splitlines()), 8)

    async def test_rank_preview_uses_uid_and_strips_points(self):
        service = HostingService(SimpleNamespace(), SimpleNamespace(store=self.store), None)
        players = roster()
        for p in players:
            p["answers"].update(player_uid=str(100 + p["member_id"]), username="DO NOT PUBLISH")
        service.players = lambda: players
        slot = SimpleNamespace(lineup=self.team)
        with patch(
            "rionnag.integrations.rivals.fetch_player_ranks",
            return_value={
                "current_rank": "Grandmaster (4500 points)",
                "peak_rank": "Celestial (4800 points)",
            },
        ) as fetch:
            with patch("rionnag.integrations.rivals.queued_lookup", side_effect=lambda f, uid: f(uid)):
                ranks = await service.advert_ranks(slot)
        self.assertEqual(ranks, (("Grandmaster", "Celestial"),) * 6)
        self.assertEqual(fetch.call_args_list[0].args, ("101",))

    async def test_duration_change_requires_fresh_confirmation(self):
        service = HostingService(SimpleNamespace(), SimpleNamespace(store=self.store), None)
        self.store.configure_host(config.SCRIM_HOST_CHANNEL_ID, duration=10800)
        with self.assertRaisesRegex(ValueError, "length changed"):
            await service.change_votes(1, [self.start], True, expected_duration=7200)

    async def test_replacement_preserves_delivered_advert(self):
        self.queue()
        self.store.update_host_advert(config.SCRIM_HOST_CHANNEL_ID, self.start, "sent", message_id=77)
        service = HostingService(SimpleNamespace(), SimpleNamespace(store=self.store), None)
        new_team = {**self.team}
        del new_team[6]
        new_team[9] = "Support"
        slot = SimpleNamespace(start=self.start, ready=True, lineup=new_team)
        service.snapshot = lambda: [slot]
        service.sync = AsyncMock()
        service.manager = lambda i: True
        await service.publish(
            SimpleNamespace(user=SimpleNamespace(id=99)),
            self.start,
            service.preview_token(slot, self.settings),
        )
        job = self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID)[self.start]
        self.assertEqual(job["status"], "sent")
        self.assertEqual(job["message_id"], 77)
        self.assertEqual(json.loads(job["lineup"])["9"], "Support")
