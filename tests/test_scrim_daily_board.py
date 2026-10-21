import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from rionnag import config
from rionnag.scrims.scrim_feed import FeedStore
from rionnag.services.scrim_opportunities import OpportunityPublisher
from rionnag.storage import Store
from rionnag.ui.scrim_opportunities import BOARD_MARKER


def instant(value):
    return int(datetime.fromisoformat(value).timestamp())


def answers(role="Tank", start=20):
    return {"time_zone": "Eastern Time (ET)",
            "availability_days": {"Monday": {"start": start, "end": 23}},
            "username": "Example", "preferred_role_1": role,
            "preferred_role_2": role}


def offer(start="2027-06-08T00:00:00Z", end="2027-06-08T02:00:00Z"):
    return {"Start_Time_timestamp": start, "End_Time_timestamp": end,
            "rank_minimum": "Diamond", "rank_maximum": "Grandmaster",
            "messageURL": "https://discord.com/channels/1/2/3", "authorID": "11"}


class DailyBoardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.store = Store(root / "members.db")
        self.feed = FeedStore(root / "feed.db")
        self.form = {"name": "Marvel Rivals", "version": 1,
                     "tryout_role": 7, "team_role": 8, "manager_role": 9}
        for mid in range(1, 8):
            role = ("Tank", "DPS", "Support")[(mid - 1) // 2] if mid <= 6 else "Tank"
            self.store.update(mid, status="accepted", game="marvel-rivals",
                              version=1, answers=answers(role))
            self.store.save_profile(1, mid, self.form, answers(role))
        self.source = {"id": "3", "channel_id": "2", "url": offer()["messageURL"],
                       "author_id": "11"}
        self.feed.put(self.source)
        self.feed.complete(self.feed.pending(), {"scrims": [{**offer(), "source_message_id": "3"}]})
        self.message = SimpleNamespace(id=99, edit=AsyncMock(), delete=AsyncMock(),
                                       author=SimpleNamespace(id=999), embeds=[])

        async def history(**kwargs):
            if False:
                yield self.message

        self.channel = SimpleNamespace(send=AsyncMock(return_value=self.message),
                                       fetch_message=AsyncMock(return_value=self.message),
                                       history=history)
        self.owner = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=80)))
        self.guild = SimpleNamespace(
            owner_id=100, get_channel=lambda cid: self.channel,
            get_member=lambda mid: self.owner if mid == 100 else SimpleNamespace(
                bot=False, roles=[SimpleNamespace(id=7)]))
        self.bot = SimpleNamespace(get_guild=lambda gid: self.guild,
                                   user=SimpleNamespace(id=999), add_view=MagicMock())
        self.publisher = OpportunityPublisher(
            self.bot, self.store, self.feed, {"marvel-rivals": self.form}, 1, 10)
        self.now = instant("2027-06-07T23:00:00+00:00")

    def interaction(self, member_id):
        return SimpleNamespace(
            guild_id=1, user=SimpleNamespace(id=member_id),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_one_board_collects_todays_times_and_survives_restart(self):
        self.feed.put({"id": "4", "channel_id": "2", "url": "https://discord.com/channels/1/2/4",
                       "author_id": "12"})
        self.feed.complete(self.feed.pending(), {"scrims": [
            {**offer(start="2027-06-08T01:00:00Z", end="2027-06-08T02:00:00Z"),
             "source_message_id": "4"}]})
        await self.publisher.sync(self.now)
        sent = self.channel.send.call_args.kwargs
        self.assertEqual(len(sent["embeds"]), 3)
        self.assertEqual(sent["embeds"][0].footer.text, BOARD_MARKER)
        self.assertEqual(len(self.store.opportunity_posts(10)), 2)
        self.assertEqual({p["message_id"] for p in self.store.opportunity_posts(10).values()}, {99})
        self.publisher = OpportunityPublisher(
            self.bot, Store(self.store.path), FeedStore(self.feed.path),
            {"marvel-rivals": self.form}, 1, 10)
        await self.publisher.sync(self.now)
        self.channel.send.assert_awaited_once()
        self.message.edit.assert_not_awaited()

    async def test_only_todays_future_times_are_shown(self):
        self.feed.put({"id": "4", "channel_id": "2", "author_id": "12"})
        self.feed.complete(self.feed.pending(), {"scrims": [
            {**offer(start="2027-06-09T00:00:00Z", end="2027-06-09T01:00:00Z"),
             "source_message_id": "4"}]})
        await self.publisher.sync(self.now)
        self.assertEqual(len(self.channel.send.call_args.kwargs["embeds"]), 2)
        await self.publisher.sync(instant("2027-06-08T00:00:00+00:00"))
        self.assertEqual(len(self.message.edit.call_args.kwargs["embeds"]), 1)
        self.assertEqual(self.store.opportunity_posts(10)["3:0"]["status"], "inactive")

    async def test_schedule_override_requires_explicit_yes(self):
        await self.publisher.sync(self.now)
        self.store.update(1, answers=answers(start=21))
        with patch("rionnag.services.scrim_opportunities.time.time", return_value=self.now):
            first = self.interaction(1)
            await self.publisher.vote(first, "3:0", True)
            self.assertEqual(self.store.opportunity_votes(10, "3:0"), [])
            self.assertIn("outside your saved availability",
                          first.followup.send.call_args.args[0])
            second = self.interaction(1)
            await self.publisher.vote(second, "3:0", True, override=True)
        self.assertEqual(self.store.opportunity_votes(10, "3:0"), [1])
        self.assertEqual(self.store.finder_overrides(10, "3:0")[1], 7200)
        self.message.edit.assert_awaited()
        with patch("rionnag.services.scrim_opportunities.time.time", return_value=self.now):
            await self.publisher.vote(self.interaction(1), "3:0", False)
        self.assertEqual(self.store.opportunity_votes(10, "3:0"), [])
        self.assertEqual(self.store.finder_overrides(10, "3:0"), {})

    async def test_six_valid_players_prompt_owner_once_and_owner_books(self):
        await self.publisher.sync(self.now)
        for mid in range(1, 7):
            self.store.vote_opportunity(10, "3:0", mid, True)
        await self.publisher.sync(self.now)
        self.owner.send.assert_awaited_once()
        notice = self.store.finder_notices(10)[instant(offer()["Start_Time_timestamp"])]
        self.assertEqual(notice["status"], "pending")
        await self.publisher.sync(self.now)
        self.owner.send.assert_awaited_once()
        with patch("rionnag.services.scrim_opportunities.time.time", return_value=self.now):
            await self.publisher.book(self.interaction(100), notice["start"], notice["generation"])
        self.assertIn(notice["start"], self.store.finder_bookings(10))
        self.assertEqual(self.store.finder_notices(10)[notice["start"]]["status"], "booked")
        self.assertEqual(await self.publisher.current_slots(), [])
        board = self.channel.fetch_message.return_value
        self.assertEqual(len(board.edit.call_args.kwargs["embeds"]), 1)
        with patch("rionnag.services.scrim_opportunities.time.time", return_value=self.now):
            with self.assertRaises(ValueError):
                await self.publisher.book(self.interaction(100), notice["start"], notice["generation"])

    async def test_withdrawal_invalidates_owner_prompt(self):
        await self.publisher.sync(self.now)
        for mid in range(1, 7):
            self.store.vote_opportunity(10, "3:0", mid, True)
        await self.publisher.sync(self.now)
        self.store.vote_opportunity(10, "3:0", 6, False)
        await self.publisher.sync(self.now)
        start = instant(offer()["Start_Time_timestamp"])
        self.assertEqual(self.store.finder_notices(10)[start]["status"], "expired")

    async def test_three_player_invite_is_durable_and_yes_joins(self):
        async def history(**kwargs):
            if False:
                yield None

        dm = SimpleNamespace(history=history, send=AsyncMock(return_value=SimpleNamespace(id=77)))
        member = SimpleNamespace(bot=False, roles=[SimpleNamespace(id=7)],
                                 create_dm=AsyncMock(return_value=dm))
        original = self.guild.get_member
        self.guild.get_member = lambda mid: member if mid == 7 else original(mid)
        await self.publisher.sync(self.now)
        for mid in (1, 2, 3):
            self.store.vote_opportunity(10, "3:0", mid, True)
        await self.publisher.sync(self.now)
        dm.send.assert_awaited_once()
        await self.publisher.sync(self.now)
        dm.send.assert_awaited_once()
        with patch("rionnag.services.scrim_opportunities.time.time", return_value=self.now):
            await self.publisher.answer_invite(
                7, instant(offer()["Start_Time_timestamp"]), 7200, "interest", True)
        self.assertEqual(self.store.opportunity_votes(10, "3:0"), [1, 2, 3, 7])
        notice = self.store.finder_notification(
            10, instant(offer()["Start_Time_timestamp"]), 7, "interest", 7200)
        self.assertEqual(notice["status"], "accepted")

    async def test_official_status_edits_existing_available_invite(self):
        start = instant(offer()["Start_Time_timestamp"])
        prior = self.store.finder_notification(10, start, 7, "interest", 7200)
        self.store.complete_finder_notification(10, start, 7, "interest", 7200, "sent", 77)
        existing = SimpleNamespace(id=77, author=SimpleNamespace(id=999), edit=AsyncMock(), embeds=[])
        async def history(**kwargs):
            if False:
                yield None

        dm = SimpleNamespace(fetch_message=AsyncMock(return_value=existing), history=history)
        member = SimpleNamespace(create_dm=AsyncMock(return_value=dm), bot=False,
                                 roles=[SimpleNamespace(id=7)])
        self.guild.get_member = lambda mid: member
        await self.publisher._deliver_player_notice(
            self.guild, start, 7, "official", 7200, offer())
        existing.edit.assert_awaited_once()
        self.assertEqual(existing.edit.call_args.kwargs["embed"].title, "Your scrim is official")
        self.assertIsNone(existing.edit.call_args.kwargs["view"])
        official = self.store.finder_notification(10, start, 7, "official", 7200)
        self.assertEqual(official["message_id"], prior["message_id"] or 77)

    async def test_completed_scrim_dm_messages_are_deleted(self):
        start = self.now - 10800
        self.store.host_notification(10, start, 7, "confirmed")
        self.store.complete_host_notification(10, start, 7, "confirmed", "sent", 77)
        old = SimpleNamespace(author=SimpleNamespace(id=999), delete=AsyncMock())
        dm = SimpleNamespace(fetch_message=AsyncMock(return_value=old))
        user = SimpleNamespace(dm_channel=dm, create_dm=AsyncMock(return_value=dm))
        self.bot.get_user = lambda mid: user if mid == 7 else None
        self.bot.fetch_user = AsyncMock(return_value=user)
        await self.publisher.cleanup_completed_scrim_dms(self.now)
        old.delete.assert_awaited_once()

    async def test_already_answered_host_invitation_dm_is_deleted_immediately(self):
        start = self.now + 86400
        self.store.reserve_host_invite(10, start, 7, 7200)
        self.store.update_host_invite(10, start, 7, 7200, "accepted", 77)
        old = SimpleNamespace(author=SimpleNamespace(id=999), delete=AsyncMock())
        dm = SimpleNamespace(fetch_message=AsyncMock(return_value=old))
        user = SimpleNamespace(dm_channel=dm, create_dm=AsyncMock(return_value=dm))
        self.bot.get_user = lambda mid: user if mid == 7 else None
        await self.publisher.cleanup_completed_scrim_dms(self.now)
        old.delete.assert_awaited_once()

    async def test_thirty_minute_scrim_dm_is_deleted_when_scrim_starts(self):
        start = self.now
        self.store.host_notification(10, start, 7, "thirty")
        self.store.complete_host_notification(10, start, 7, "thirty", "sent", 77)
        old = SimpleNamespace(author=SimpleNamespace(id=999), delete=AsyncMock())
        dm = SimpleNamespace(fetch_message=AsyncMock(return_value=old))
        user = SimpleNamespace(dm_channel=dm, create_dm=AsyncMock(return_value=dm))
        self.bot.get_user = lambda mid: user if mid == 7 else None
        await self.publisher.cleanup_completed_scrim_dms(start)
        old.delete.assert_awaited_once()

    async def test_expired_owner_scrim_prompt_is_deleted_before_its_start_time(self):
        start = self.now + 86400
        generation = self.store.reserve_host_notice(10, start)
        self.store.update_host_notice(10, start, generation, "expired", 78)
        old = SimpleNamespace(author=SimpleNamespace(id=999), delete=AsyncMock())
        dm = SimpleNamespace(fetch_message=AsyncMock(return_value=old))
        user = SimpleNamespace(dm_channel=dm)
        self.bot.get_user = lambda mid: user if mid == 100 else None
        self.bot.fetch_user = AsyncMock(return_value=user)
        await self.publisher.cleanup_completed_scrim_dms(self.now)
        old.delete.assert_awaited_once()

    async def test_legacy_untracked_scrim_dms_are_deleted_by_history_scan(self):
        start = self.now - 10800
        old = SimpleNamespace(
            author=SimpleNamespace(id=999),
            embeds=[SimpleNamespace(footer=SimpleNamespace(
                text=f"Scrim finder notice • 10:{start}:7:interest:7200"))],
            delete=AsyncMock(), id=1234,
        )
        dm = SimpleNamespace(history=AsyncMock())

        async def history(**kwargs):
            yield old

        dm.history = history
        member = SimpleNamespace(bot=False, dm_channel=dm)
        self.guild.members = [member]
        await self.publisher._scan_legacy_scrim_dms(self.guild, self.now)
        old.delete.assert_awaited_once()

    async def test_legacy_scrim_workflow_replies_are_deleted(self):
        messages = [
            SimpleNamespace(author=SimpleNamespace(id=999), content=content, embeds=[],
                            delete=AsyncMock(), id=index)
            for index, content in enumerate((
                "Bump queued. The old advert will be deleted and reposted.",
                "This session no longer has a confirmed 2–2–2 team.",
                "Advert queued. Delivery status will appear on the scrim board.",
                "Advert queued with your rank range.",
            ), start=1)
        ]
        dm = SimpleNamespace()

        async def history(**kwargs):
            for message in messages:
                yield message

        dm.history = history
        self.guild.members = [SimpleNamespace(bot=False, dm_channel=dm)]
        await self.publisher._scan_legacy_scrim_dms(self.guild, self.now)
        for message in messages:
            message.delete.assert_awaited_once()

    async def test_legacy_six_player_owner_panel_is_removed_after_its_scrim(self):
        start = self.now - 3 * 3600
        old = SimpleNamespace(
            author=SimpleNamespace(id=999), content="", id=8765, delete=AsyncMock(),
            embeds=[SimpleNamespace(
                title="Six players confirmed for a scrim",
                description=f"<t:{start}:F> – <t:{start + 7200}:t>",
                footer=SimpleNamespace(text="Legacy scrim approval"),
            )],
        )
        dm = SimpleNamespace()

        async def history(**kwargs):
            yield old

        dm.history = history
        self.guild.members = [SimpleNamespace(id=100, bot=False, dm_channel=dm)]
        await self.publisher._scan_legacy_scrim_dms(self.guild, self.now)
        old.delete.assert_awaited_once()

    async def test_legacy_owner_panel_from_replaced_generation_is_removed(self):
        start = self.now + 86400
        self.store.reserve_host_notice(config.SCRIM_HOST_CHANNEL_ID, start)
        self.store.update_host_notice(config.SCRIM_HOST_CHANNEL_ID, start, 1, "expired")
        self.store.reserve_host_notice(config.SCRIM_HOST_CHANNEL_ID, start)
        self.store.update_host_notice(config.SCRIM_HOST_CHANNEL_ID, start, 2, "pending", 90)
        old = SimpleNamespace(
            author=SimpleNamespace(id=999), content="", id=89, delete=AsyncMock(),
            embeds=[SimpleNamespace(
                title="Six players confirmed for a scrim",
                description=f"<t:{start}:F>",
                footer=SimpleNamespace(text=f"Scrim host approval · {start} · 1"),
            )],
        )
        dm = SimpleNamespace()

        async def history(**kwargs):
            yield old

        dm.history = history
        self.guild.members = [SimpleNamespace(id=100, bot=False, dm_channel=dm)]
        await self.publisher._scan_legacy_scrim_dms(self.guild, self.now)
        old.delete.assert_awaited_once()

    async def test_no_removes_availability_and_prevents_repeat_invites(self):
        async def history(**kwargs):
            if False:
                yield None

        dm = SimpleNamespace(history=history, send=AsyncMock(return_value=SimpleNamespace(id=77)))
        member = SimpleNamespace(bot=False, roles=[SimpleNamespace(id=7)],
                                 create_dm=AsyncMock(return_value=dm))
        original = self.guild.get_member
        self.guild.get_member = lambda mid: member if mid == 7 else original(mid)
        await self.publisher.sync(self.now)
        for mid in (1, 2, 3):
            self.store.vote_opportunity(10, "3:0", mid, True)
        await self.publisher.sync(self.now)
        dm.send.assert_awaited_once()
        start, end = instant(offer()["Start_Time_timestamp"]), instant(offer()["End_Time_timestamp"])
        with patch("rionnag.services.scrim_opportunities.time.time", return_value=self.now):
            await self.publisher.answer_invite(7, start, end - start, "interest", False)
        self.assertEqual(self.store.finder_declined(10, start, end), {7})
        self.assertEqual(self.store.finder_declined(10, start + 1800, end + 1800), {7})
        self.assertEqual(self.store.finder_declined(10, end, end + 3600), set())
        fields = self.message.edit.call_args.kwargs["embeds"][1].fields
        available = next(field for field in fields if field.name.startswith("Available players"))
        self.assertNotIn("<@7>", available.value)
        self.store.vote_opportunity(10, "3:0", 3, False)
        await self.publisher.sync(self.now)
        self.store.vote_opportunity(10, "3:0", 3, True)
        await self.publisher.sync(self.now)
        dm.send.assert_awaited_once()
        self.publisher = OpportunityPublisher(
            self.bot, Store(self.store.path), FeedStore(self.feed.path),
            {"marvel-rivals": self.form}, 1, 10)
        await self.publisher.sync(self.now)
        dm.send.assert_awaited_once()
        with patch("rionnag.services.scrim_opportunities.time.time", return_value=self.now):
            blocked = self.interaction(7)
            await self.publisher.vote(blocked, "3:0", True)
        self.assertIn("marked unavailable", blocked.followup.send.call_args.args[0])

    async def test_old_no_decisions_migrate_to_unavailable_windows(self):
        start = instant(offer()["Start_Time_timestamp"])
        self.store.finder_notification(10, start, 7, "interest", 7200)
        self.store.complete_finder_notification(10, start, 7, "interest", 7200, "declined")
        reopened = Store(self.store.path)
        self.assertEqual(reopened.finder_declined(10, start, start + 7200), {7})

    async def test_existing_per_time_post_is_removed_after_board_send(self):
        self.store.save_opportunity(10, "3:0", 77, "", "active",
                                    instant(offer()["Start_Time_timestamp"]))
        await self.publisher.sync(self.now)
        self.message.delete.assert_awaited_once()
        self.assertEqual(self.store.opportunity_posts(10)["3:0"]["message_id"], 99)

    async def test_host_official_day_suppresses_finder(self):
        self.store.book_host(config.SCRIM_HOST_CHANNEL_ID,
                             instant("2027-06-08T01:00:00+00:00"), 7200, 100)
        await self.publisher.sync(self.now)
        self.assertEqual(len(self.channel.send.call_args.kwargs["embeds"]), 1)


if __name__ == "__main__":
    unittest.main()
