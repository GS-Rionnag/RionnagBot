import json
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from rionnag import config
from rionnag.cogs.scrim_hosting import ScrimHosting
from rionnag.services.scrim_hosting import HostingService, HostSlot, generate_slots, role_team
from rionnag.storage import Store
from rionnag.ui.scrim_hosting import (
    DayPicker,
    HostApproval,
    HostingBoard,
    SessionCard,
    SlotPicker,
    day_groups,
)
from scrim_collector.hosting import invalid_request, process_bump, process_job


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

    def test_days_rank_soonest_first_without_double_counting(self):
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
        self.assertEqual(list(groups), ["2026-10-08", "2026-10-09", "2026-10-10"])
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

    async def test_member_day_command_uses_target_availability_read_only(self):
        service = SimpleNamespace(
            players=lambda: [player(2, "Tank", "DPS")],
            snapshot=lambda: [HostSlot(self.start, self.start + 7200, {2}, set(), {}, 0, False)],
            store=self.store, channel_id=config.SCRIM_HOST_CHANNEL_ID,
            change_votes=AsyncMock(),
        )
        interaction = SimpleNamespace(
            guild_id=config.GUILD_ID, user=SimpleNamespace(id=1),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await ScrimHosting.board_for.callback(ScrimHosting(service), interaction, SimpleNamespace(id=2))
        view = interaction.response.send_message.call_args.kwargs["view"]
        self.assertEqual(view.owner, 1)
        self.assertEqual(view.subject, 2)
        self.assertEqual(view.zone.key, "America/New_York")
        self.assertIn("<@2>", view.embed().title)
        self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])
        day = next(iter(view.groups))
        next_interaction = SimpleNamespace(
            user=SimpleNamespace(id=1), data={"values": [day]},
            response=SimpleNamespace(defer=AsyncMock()), edit_original_response=AsyncMock(),
        )
        await view.selected(next_interaction)
        times = next_interaction.edit_original_response.call_args.kwargs["view"]
        self.assertEqual(times.mode, "inspect")
        self.assertEqual(times.subject, 2)
        self.assertIn("cannot change", times.embed().description)
        service.change_votes.assert_not_awaited()

    async def test_one_global_official_slot_and_durable_notice(self):
        cid = config.SCRIM_HOST_CHANNEL_ID
        self.store.book_host(cid, self.start, 7200, 99)
        self.assertEqual(self.store.official_host(cid), self.start)
        notice = self.store.host_notification(cid, self.start, 1, "confirmed")
        self.assertEqual(notice["status"], "pending")
        self.store.complete_host_notification(cid, self.start, 1, "confirmed", "sent", 123)
        saved = Store(self.store.path).host_notification(cid, self.start, 1, "confirmed")
        self.assertEqual(saved["message_id"], 123)
        self.store.book_host(cid, self.start + 3600, 7200, 99)
        self.assertEqual(Store(self.store.path).official_host(cid), self.start + 3600)

    async def test_official_announcement_sends_once_to_confirmed_then_available(self):
        async def history(**kwargs):
            if False:
                yield None

        sent = []

        def send_for(mid):
            async def send(**kwargs):
                sent.append((mid, kwargs["embed"].description))
                return SimpleNamespace(id=100 + mid)
            return send

        users = {
            mid: SimpleNamespace(dm_channel=SimpleNamespace(
                history=history, send=AsyncMock(side_effect=send_for(mid))
            )) for mid in (1, 2)
        }
        bot = SimpleNamespace(get_user=users.get, user=SimpleNamespace(id=44))
        service = HostingService(bot, SimpleNamespace(store=self.store), None)
        service.players = lambda: [dict(member_id=mid, answers={}) for mid in (1, 2)]
        self.store.book_host(service.channel_id, self.start, 7200, 99)
        self.store.set_host_vote(service.channel_id, self.start, 1, True)
        with patch("rionnag.services.scrim_hosting.covers_interval", return_value=True):
            await service.notify_official()
            await service.notify_official()
        self.assertEqual([mid for mid, _ in sent], [1, 2])
        self.assertIn("officially confirmed", sent[0][1])
        self.assertIn("Can you make it?", sent[1][1])

    async def test_idempotent_persisted_queue_and_departure_cleanup(self):
        self.assertTrue(self.queue())
        self.assertFalse(self.queue())
        job = Store(self.store.path).host_adverts(config.SCRIM_HOST_CHANNEL_ID)[self.start]
        self.assertEqual(job["content"], f"LFS Grandmaster - Celestial at <t:{self.start}:F>")
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
            content="",
            author=SimpleNamespace(id=44),
            embeds=[SimpleNamespace(footer=SimpleNamespace(text="Rionnag scrim hosting board"))],
            edit=AsyncMock(),
        )

        async def history(**kwargs):
            yield message

        cards = [SimpleNamespace(id=200 + i, edit=AsyncMock(), delete=AsyncMock()) for i in range(5)]
        by_id = {card.id: card for card in cards}
        for i, card in enumerate(cards):
            card.edit.return_value = card
            self.store.save_host_card(config.SCRIM_HOST_CHANNEL_ID, i, card.id)
        channel = SimpleNamespace(history=history, send=AsyncMock(),
                                  fetch_message=AsyncMock(side_effect=lambda mid: by_id[mid]))
        guild = SimpleNamespace(get_channel=lambda cid: channel)
        bot = SimpleNamespace(get_guild=lambda gid: guild, user=SimpleNamespace(id=44))
        service = HostingService(bot, SimpleNamespace(store=self.store, forms=config.load_forms()), None)
        service.players = roster
        service.players = lambda: roster()
        service.snapshot = lambda: []
        await service.sync()
        await service.sync()
        channel.send.assert_not_awaited()
        message.edit.assert_awaited_once()
        self.assertEqual(len(message.edit.call_args.kwargs["embeds"]), 1)
        for card in cards[:3]:
            card.edit.assert_awaited_once()
            self.assertTrue(all(b.disabled for b in card.edit.call_args.kwargs["view"].children))
        for card in cards[3:]:
            card.delete.assert_awaited_once()
            card.edit.assert_not_awaited()
        self.assertEqual(len(self.store.host_cards(config.SCRIM_HOST_CHANNEL_ID)), 3)
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
        self.assertEqual(len(view.groups), 5)
        self.assertEqual(list(view.groups), sorted(view.groups))
        day_slots = next(iter(view.groups.values()))
        day_slots[0].confirmed.add(1)
        day_text = view.embed().fields[0].value
        self.assertIn("**Available players** (5)", day_text)
        self.assertIn("<@2>", day_text)
        self.assertNotIn("<@1>", day_text)
        self.assertGreater(len(DayPicker(service, 1, slots, "remove").groups), 5)
        self.assertNotIn(":F>", json.dumps(view.embed().to_dict()))
        self.assertIn("available", view.children[0].options[0].description)
        day = view.children[0].options[0].value
        interaction = SimpleNamespace(
            data={"values": [day]}, response=SimpleNamespace(defer=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        def acknowledged_snapshot():
            interaction.response.defer.assert_awaited_once()
            return slots
        service.snapshot = acknowledged_snapshot
        await view.selected(interaction)
        picker = interaction.edit_original_response.call_args.kwargs["view"]
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
        self.assertIn("**Available players** (5)\n<@2> <@3> <@4> <@5> <@6>", embed.fields[0].value)
        self.assertNotIn("6 available", embed.fields[0].value)

    async def test_confirmed_players_group_by_best_role_in_tank_dps_support_order(self):
        service = HostingService(SimpleNamespace(), SimpleNamespace(store=self.store), None)
        service.players = roster
        slot = HostSlot(self.start, self.start + 7200, set(range(1, 7)), {1, 2, 3, 4, 5, 6}, {}, 0, True)
        value = service.board_embed([slot]).fields[0].value
        self.assertIn("Tank: <@1>, <@2>\nDPS: <@3>, <@4>\nSupport: <@5>, <@6>", value)
        self.assertNotIn("Best role:", value)
        self.assertIn("**Available players** (0)\nNone remaining.", value)

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
        self.assertTrue(all(card.footer.text is None for card in cards[1:]))
        self.assertEqual(cards[0].footer.text, "Use /edit_form to view more days")
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
            followup=SimpleNamespace(send=AsyncMock(), edit_message=AsyncMock()),
            message=SimpleNamespace(id=123, edit=AsyncMock(side_effect=AssertionError("Wrong edit API"))),
        )
        await picker.selected(interaction)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.assertEqual(self.store.host_votes(config.SCRIM_HOST_CHANNEL_ID), {chosen: {1}})
        service.sync.assert_awaited_once()
        picker.stop()

    async def test_unchecking_confirmed_time_withdraws_without_touching_other_pages(self):
        service = HostingService(SimpleNamespace(), SimpleNamespace(store=self.store), None)
        now = datetime(2026, 10, 8, 20, tzinfo=UTC).timestamp()
        slots = generate_slots(roster(), {}, now)
        first, other_page = slots[0], slots[10]
        for slot in (first, other_page):
            slot.confirmed.add(1)
            self.store.set_host_vote(service.channel_id, slot.start, 1, True)
        service.snapshot = lambda: slots
        service.sync = AsyncMock()
        picker = SlotPicker(service, 1, slots, "add")
        self.assertTrue(picker.children[0].options[0].default)
        self.assertEqual(picker.children[0].min_values, 0)
        interaction = SimpleNamespace(
            data={"values": []}, response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock(), edit_message=AsyncMock()),
            message=SimpleNamespace(id=123, edit=AsyncMock(side_effect=AssertionError("Wrong edit API"))),
        )
        await picker.selected(interaction)
        self.assertEqual(self.store.host_votes(service.channel_id), {other_page.start: {1}})
        self.assertFalse(picker.children[0].options[0].default)
        interaction.followup.edit_message.assert_awaited_once()
        self.assertEqual(interaction.followup.edit_message.call_args.args, (123,))
        picker.stop()

    async def test_my_selections_withdraws_confirmed_time_and_closes_stale_picker(self):
        service = HostingService(SimpleNamespace(), SimpleNamespace(store=self.store), None)
        slot = HostSlot(self.start, self.start + 7200, {1}, {1}, {}, 0, False)
        self.store.set_host_vote(service.channel_id, slot.start, 1, True)
        service.snapshot = lambda: [slot]
        service.sync = AsyncMock()
        picker = SlotPicker(service, 1, [slot], "remove")
        interaction = SimpleNamespace(
            data={"values": [str(slot.start)]}, response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock(), edit_message=AsyncMock()),
            message=SimpleNamespace(id=123, edit=AsyncMock(side_effect=AssertionError("Wrong edit API"))),
        )
        await picker.selected(interaction)
        self.assertEqual(self.store.host_votes(service.channel_id), {})
        self.assertEqual(interaction.followup.edit_message.call_args.args, (123,))
        self.assertIsNone(interaction.followup.edit_message.call_args.kwargs["view"])
        picker.stop()

    async def test_public_board_has_only_player_controls(self):
        board = HostingBoard(SimpleNamespace())
        self.assertEqual([item.label for item in board.children], ["Choose a day", "My selections"])
        board.stop()

    async def test_direct_join_requires_schedule_confirmation_and_override_survives_refresh(self):
        service = HostingService(SimpleNamespace(), SimpleNamespace(store=self.store), None)
        players = roster() + [player(7, "Support", "DPS")]
        players[-1]["answers"]["availability_days"] = {}
        service.players = lambda: players
        service.sync = AsyncMock()
        slot = service.snapshot()[0]
        self.assertFalse(await service.direct_join(7, slot.start, 7200))
        self.assertEqual(self.store.host_votes(service.channel_id), {})
        self.assertTrue(await service.direct_join(7, slot.start, 7200, override=True))
        restored = Store(self.store.path)
        self.assertEqual(restored.host_overrides(service.channel_id, 7200), {slot.start: {7}})
        confirmed = next(s for s in service.snapshot() if s.start == slot.start)
        self.assertIn(7, confirmed.confirmed)
        self.assertIn(7, confirmed.available)
        self.store.prune_host_votes(service.channel_id, {s.start: s.available for s in service.snapshot()})
        self.assertEqual(self.store.host_votes(service.channel_id), {slot.start: {7}})
        await service.change_votes(7, [slot.start], False)
        self.assertEqual(restored.host_overrides(service.channel_id, 7200), {})
        with self.assertRaises(ValueError):
            await service.direct_join(99, slot.start, 7200, override=True)
        with self.assertRaises(ValueError):
            await service.direct_join(7, slot.start, 3600, override=True)

    async def test_direct_button_warns_before_outside_schedule_join(self):
        service = SimpleNamespace(
            store=self.store, channel_id=config.SCRIM_HOST_CHANNEL_ID,
            direct_join=AsyncMock(return_value=False),
        )
        view = SessionCard(service, 0)
        self.assertTrue(view.is_persistent())
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=7), response=SimpleNamespace(defer=AsyncMock()),
            message=SimpleNamespace(embeds=[SimpleNamespace(
                title=f"<t:{self.start}:F>", description=f"<t:{self.start + 7200}:t>"
            )]),
            followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock(),
        )
        await view.children[0].callback(interaction)
        service.direct_join.assert_awaited_once_with(7, self.start, 7200)
        warning = interaction.followup.send.call_args
        self.assertIn("not in your saved schedule", warning.args[0])
        prompt = warning.kwargs["view"]
        await prompt.children[0].callback(interaction)
        self.assertEqual(service.direct_join.call_args.kwargs, {"override": True})
        view.stop()

    async def test_delivered_owner_panel_hides_send_and_no(self):
        service, slot, dm = self.approval_fixture()
        self.store.queue_host_advert(
            service.channel_id, self.start, self.store.host_settings(service.channel_id), self.team, 99
        )
        self.store.update_host_advert(service.channel_id, self.start, "sent", message_id=77)
        view = HostApproval(service, self.start, 1)
        self.assertEqual([button.label for button in view.children], ["Bump post", "Confirm scrim"])
        advert = self.store.host_adverts(service.channel_id)[self.start]
        panel = service.owner_panel(slot, 1)
        self.assertIn(f"/{advert['destination']}/77)", panel.fields[0].value)
        self.store.update_host_advert(service.channel_id, self.start, "sent", message_id=88)
        self.assertIn(f"/{advert['destination']}/88)", service.owner_panel(slot, 1).fields[0].value)
        view.stop()

    async def test_outbound_accepts_explicit_schedule_override_only_for_exact_duration(self):
        self.queue()
        job = self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID)[self.start]
        for mid in self.team:
            self.store.set_host_vote(config.SCRIM_HOST_CHANNEL_ID, self.start, mid, True)
        players = {p["member_id"]: p for p in roster()}
        for p in players.values():
            p["answers"]["availability_days"] = {}
            self.store.set_host_override(config.SCRIM_HOST_CHANNEL_ID, self.start, p["member_id"], 7200)
        role = config.load_forms()["marvel-rivals"]["team_role"]
        members = {mid: SimpleNamespace(id=mid, bot=False, roles=[SimpleNamespace(id=role)])
                   for mid in (*self.team, 99)}
        self.assertIsNone(invalid_request(self.store, job, players, members, 99, 88))
        self.store.set_host_override(config.SCRIM_HOST_CHANNEL_ID, self.start, 1, 3600)
        self.assertIn("availability", invalid_request(self.store, job, players, members, 99, 88))

    async def test_three_confirmations_invite_remaining_players_once_and_persist_decisions(self):
        async def history(**kwargs):
            if False:
                yield None

        users = {mid: SimpleNamespace(dm_channel=SimpleNamespace(
            history=history, send=AsyncMock(return_value=SimpleNamespace(id=100 + mid))
        )) for mid in range(1, 7)}
        bot = SimpleNamespace(get_user=users.get, user=SimpleNamespace(id=44), add_view=Mock())
        service = HostingService(bot, SimpleNamespace(store=self.store), None)
        service.players = roster
        slot = HostSlot(self.start, self.start + 7200, set(range(1, 7)), {1, 2}, {}, 0, False)
        service.snapshot = lambda: [slot]
        service.sync = AsyncMock()
        await service.invite_available()
        self.assertEqual(self.store.host_invites(service.channel_id), [])
        slot.confirmed.add(3)
        await service.invite_available()
        await service.invite_available()
        for mid in (1, 2, 3):
            users[mid].dm_channel.send.assert_not_awaited()
        for mid in (4, 5, 6):
            users[mid].dm_channel.send.assert_awaited_once()
            embed = users[mid].dm_channel.send.call_args.kwargs["embed"]
            self.assertIn(f"<t:{slot.start}:F> – <t:{slot.end}:t>", embed.description)
            self.assertTrue(users[mid].dm_channel.send.call_args.kwargs["view"].is_persistent())
        await service.answer_invite(4, slot.start, 7200, False)
        self.assertEqual(self.store.host_votes(service.channel_id), {})
        with patch("rionnag.services.scrim_hosting.covers_interval", return_value=True):
            await service.answer_invite(5, slot.start, 7200, True)
        self.assertEqual(self.store.host_votes(service.channel_id), {slot.start: {5}})
        with self.assertRaises(ValueError):
            await service.answer_invite(4, slot.start, 7200, True)
        restored = HostingService(bot, SimpleNamespace(store=Store(self.store.path)), None)
        restored.register_approvals()
        bot.add_view.assert_called_once()
        self.assertEqual(bot.add_view.call_args.kwargs["message_id"], 106)
        await service.invite_available()
        self.assertEqual(users[4].dm_channel.send.await_count, 1)

    async def test_invite_rejects_changed_schedule_and_expired_interval(self):
        service = HostingService(SimpleNamespace(), SimpleNamespace(store=self.store), None)
        service.players = roster
        service.sync = AsyncMock()
        slot = HostSlot(self.start, self.start + 7200, set(range(1, 7)), {1, 2, 3}, {}, 0, False)
        service.snapshot = lambda: [slot]
        self.store.reserve_host_invite(service.channel_id, self.start, 4, 7200)
        with patch("rionnag.services.scrim_hosting.covers_interval", return_value=False):
            with self.assertRaises(ValueError):
                await service.answer_invite(4, self.start, 7200, True)
        service.snapshot = lambda: []
        with self.assertRaises(ValueError):
            await service.answer_invite(4, self.start, 7200, True)
        self.assertEqual(self.store.host_votes(service.channel_id), {})

    async def test_invite_recovers_interrupted_send_from_dm_history(self):
        marker = f"Scrim invitation · {self.start} · 6 · 7200"
        message = SimpleNamespace(
            id=106, author=SimpleNamespace(id=44),
            embeds=[SimpleNamespace(footer=SimpleNamespace(text=marker))], edit=AsyncMock()
        )
        message.edit.return_value = message

        async def history(**kwargs):
            yield message

        dm = SimpleNamespace(history=history, send=AsyncMock())
        service = HostingService(SimpleNamespace(get_user=lambda mid: SimpleNamespace(dm_channel=dm),
                                                user=SimpleNamespace(id=44)),
                                 SimpleNamespace(store=self.store), None)
        service.snapshot = lambda: [HostSlot(self.start, self.start + 7200, set(range(1, 7)),
                                            set(range(1, 6)), {}, 0, False)]
        self.store.reserve_host_invite(service.channel_id, self.start, 6, 7200)
        await service.invite_available()
        dm.send.assert_not_awaited()
        message.edit.assert_awaited_once()
        self.assertEqual(self.store.host_invites(service.channel_id)[0]["message_id"], 106)

    def approval_fixture(self):
        async def history(**kwargs):
            if False:
                yield None

        dm = SimpleNamespace(history=history, send=AsyncMock(return_value=SimpleNamespace(id=55)))
        owner = SimpleNamespace(id=99, dm_channel=dm)
        guild = SimpleNamespace(owner_id=99, owner=owner)
        bot = SimpleNamespace(get_guild=lambda gid: guild, user=SimpleNamespace(id=44))
        service = HostingService(bot, SimpleNamespace(store=self.store, forms=config.load_forms()), None)
        service.players = roster
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
        self.assertEqual([button.label for button in view.children],
                         ["Enter ranks & send", "Bump post", "Confirm scrim"])
        self.assertIn("Tank: <@1>, <@2>\nDPS: <@3>, <@4>\nSupport: <@5>, <@6>",
                      dm.send.call_args.kwargs["embed"].description)
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
        self.assertEqual(job["content"], f"LFS Grandmaster - Celestial at <t:{self.start}:F>")
        service.advert_ranks.assert_not_awaited()
        self.assertEqual(job["requested_by"], 99)
        with self.assertRaisesRegex(ValueError, "already answered"):
            await service.decide_notice(interaction, self.start, 1, True)
        await service.notify_ready()
        dm.send.assert_awaited_once()

    async def test_owner_button_opens_prefilled_rank_form_and_submit_uses_entered_range(self):
        service, slot, dm = self.approval_fixture()
        await service.notify_ready()
        view = dm.send.call_args.kwargs["view"]
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=99), response=SimpleNamespace(send_modal=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await view.children[0].callback(interaction)
        modal = interaction.response.send_modal.call_args.args[0]
        self.assertEqual(modal.minimum.default, "Grandmaster")
        self.assertEqual(modal.maximum.default, "Celestial")
        self.assertEqual(self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID), {})
        modal.minimum._value = "Diamond"
        modal.maximum._value = "Grandmaster"
        await modal.on_submit(interaction)
        job = self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID)[self.start]
        self.assertEqual(job["content"], f"LFS Diamond - Grandmaster at <t:{self.start}:F>")
        self.assertNotIn("Player", job["content"])
        view.stop()

    async def test_invalid_advert_ranks_do_not_queue(self):
        with self.assertRaises(ValueError):
            self.store.queue_host_advert(
                config.SCRIM_HOST_CHANNEL_ID, self.start, self.settings, self.team, 99,
                "@everyone - Celestial"
            )
        self.assertEqual(self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID), {})

    async def test_bump_deletes_own_old_advert_before_reposting_and_updates_link(self):
        self.queue()
        cid = config.SCRIM_HOST_CHANNEL_ID
        self.store.update_host_advert(cid, self.start, "sent", message_id=77)
        self.assertTrue(self.store.queue_host_bump(cid, self.start))
        self.assertFalse(self.store.queue_host_bump(cid, self.start))
        calls = []

        async def delete():
            calls.append("delete")

        async def send(*args, **kwargs):
            calls.append("send")
            return SimpleNamespace(id=88)

        old = SimpleNamespace(author=SimpleNamespace(id=99), delete=AsyncMock(side_effect=delete))
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=old), send=AsyncMock(side_effect=send))
        client = SimpleNamespace(get_channel=lambda mid: channel, user=SimpleNamespace(id=99))
        with patch("scrim_collector.hosting.live_reason", AsyncMock(return_value=None)):
            await process_bump(client, self.store, self.store.host_bumps(cid)[0], {42})
        self.assertEqual(calls, ["delete", "send"])
        self.assertEqual(self.store.host_adverts(cid)[self.start]["message_id"], 88)
        self.assertEqual(self.store.host_bumps(cid)[0]["status"], "sent")
        self.assertTrue(self.store.queue_host_bump(cid, self.start))
        self.assertEqual(self.store.host_bumps(cid)[1]["old_message_id"], 88)

    async def test_bump_revalidates_before_delete_and_uncertain_delivery_never_resends(self):
        self.queue()
        cid = config.SCRIM_HOST_CHANNEL_ID
        self.store.update_host_advert(cid, self.start, "sent", message_id=77)
        self.store.queue_host_bump(cid, self.start)
        channel = SimpleNamespace(fetch_message=AsyncMock(), send=AsyncMock())
        client = SimpleNamespace(get_channel=lambda mid: channel, user=SimpleNamespace(id=99))
        with patch("scrim_collector.hosting.live_reason", AsyncMock(return_value="Team changed")):
            await process_bump(client, self.store, self.store.host_bumps(cid)[0], {42})
        channel.fetch_message.assert_not_awaited()
        channel.send.assert_not_awaited()
        self.assertEqual(self.store.host_bumps(cid)[0]["status"], "failed")
        self.store.queue_host_bump(cid, self.start)
        bump = self.store.host_bumps(cid)[1]
        self.store.update_host_bump(cid, self.start, bump["generation"], "uncertain")
        bump = self.store.host_bumps(cid)[1]

        async def history(**kwargs):
            yield SimpleNamespace(id=77, author=SimpleNamespace(id=99),
                                  content=self.store.host_adverts(cid)[self.start]["content"])

        channel.history = history
        await process_bump(client, self.store, bump, {42})
        channel.send.assert_not_awaited()
        self.assertEqual(self.store.host_bumps(cid)[1]["status"], "uncertain")

        async def recovered_history(**kwargs):
            yield SimpleNamespace(id=88, author=SimpleNamespace(id=99),
                                  content=self.store.host_adverts(cid)[self.start]["content"])

        channel.history = recovered_history
        await process_bump(client, self.store, self.store.host_bumps(cid)[1], {42})
        channel.send.assert_not_awaited()
        self.assertEqual(self.store.host_adverts(cid)[self.start]["message_id"], 88)
        self.assertEqual(self.store.host_bumps(cid)[1]["status"], "sent")

    async def test_official_confirmation_is_owner_only_persisted_and_closes_advertising(self):
        service, slot, dm = self.approval_fixture()
        await service.notify_ready()
        with self.assertRaises(ValueError):
            await service.manage_session(SimpleNamespace(user=SimpleNamespace(id=77)), self.start, 1, False)
        await service.manage_session(SimpleNamespace(user=SimpleNamespace(id=99)), self.start, 1, False)
        booking = Store(self.store.path).host_bookings(service.channel_id)[self.start]
        self.assertEqual(booking["confirmed_by"], 99)
        self.assertIn("Officially confirmed", service.owner_panel(slot, 1).description)
        with self.assertRaises(ValueError):
            await service.publish(SimpleNamespace(user=SimpleNamespace(id=99), guild_id=config.GUILD_ID,
                                                  guild=service.bot.get_guild(config.GUILD_ID)),
                                  self.start, service.preview_token(slot, self.settings))
        self.queue()
        self.store.update_host_advert(service.channel_id, self.start, "sent", message_id=77)
        self.assertFalse(self.store.queue_host_bump(service.channel_id, self.start))

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
        self.store.queue_host_advert(
            config.SCRIM_HOST_CHANNEL_ID, self.start, self.settings, self.team, 99, "Grandmaster - Celestial"
        )
        job = self.store.host_adverts(config.SCRIM_HOST_CHANNEL_ID)[self.start]
        self.assertEqual(job["content"], f"LFS Grandmaster - Celestial at <t:{self.start}:F>")
        self.assertNotIn("<@", job["content"])
        self.assertEqual(len(job["content"].splitlines()), 1)

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
