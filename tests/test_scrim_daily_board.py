import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from rionnag import config
from rionnag.scrims.scrim_feed import FeedStore
from rionnag.services.scrim_opportunities import OpportunityPublisher
from rionnag.storage import Store


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
        self.sent_messages = {}

        def make_message(message_id):
            message = SimpleNamespace(id=message_id, edit=AsyncMock(), delete=AsyncMock(),
                                      author=SimpleNamespace(id=999), embeds=[])
            self.sent_messages[message_id] = message
            return message

        self.message = make_message(99)

        async def send(**kwargs):
            message = make_message(max(self.sent_messages) + 1)
            message.embeds = [kwargs["embed"]] if kwargs.get("embed") else kwargs.get("embeds", [])
            return message

        async def fetch_message(message_id):
            return self.sent_messages.get(message_id, self.message)

        async def history(**kwargs):
            for message in self.sent_messages.values():
                yield message

        self.channel = SimpleNamespace(send=AsyncMock(side_effect=send),
                                       fetch_message=AsyncMock(side_effect=fetch_message),
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

    async def test_scrims_are_separate_posts_with_counts_and_survive_restart(self):
        self.feed.put({"id": "4", "channel_id": "2", "url": "https://discord.com/channels/1/2/4",
                       "author_id": "12"})
        self.feed.complete(self.feed.pending(), {"scrims": [
            {**offer(start="2027-06-08T01:00:00Z", end="2027-06-08T02:00:00Z"),
             "source_message_id": "4"}]})
        await self.publisher.sync(self.now)
        calls = self.channel.send.await_args_list
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("embed" in call.kwargs and "embeds" not in call.kwargs for call in calls))
        self.assertIn("1 scrim found at", calls[0].kwargs["embed"].title)
        self.assertIn("Available players (7)", [f.name for f in calls[0].kwargs["embed"].fields])
        self.assertEqual(len(self.store.opportunity_posts(10)), 2)
        self.assertEqual(len({p["message_id"] for p in self.store.opportunity_posts(10).values()}), 2)
        for call in calls:
            self.assertEqual(call.kwargs["allowed_mentions"].to_dict(),
                             discord.AllowedMentions.none().to_dict())
            self.assertNotIn("<@", str(call.kwargs.get("content", "")))
        self.publisher = OpportunityPublisher(
            self.bot, Store(self.store.path), FeedStore(self.feed.path),
            {"marvel-rivals": self.form}, 1, 10)
        await self.publisher.sync(self.now)
        self.assertEqual(self.channel.send.await_count, 2)
        self.assertFalse(any(message.edit.await_count for message in self.sent_messages.values()))

    async def test_future_times_across_days_are_listed_without_player_mentions(self):
        self.guild.get_member = lambda _member_id: None
        self.feed.put({"id": "4", "channel_id": "2", "author_id": "12"})
        self.feed.complete(self.feed.pending(), {"scrims": [
            {**offer(start="2027-06-09T00:00:00Z", end="2027-06-09T01:00:00Z"),
             "source_message_id": "4"}]})
        await self.publisher.sync(self.now)
        calls = self.channel.send.await_args_list
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("embed" in call.kwargs for call in calls))
        self.assertTrue(all(call.kwargs["allowed_mentions"].to_dict()
                            == discord.AllowedMentions.none().to_dict() for call in calls))
        self.assertTrue(all(any(field.name == "Opponent rank" for field in call.kwargs["embed"].fields)
                            for call in calls))
        self.assertTrue(all(any(field.name == "Available players (0)"
                                for field in call.kwargs["embed"].fields) for call in calls))
        await self.publisher.sync(instant("2027-06-08T00:00:00+00:00"))
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
        self.message.edit.assert_not_awaited()
        with patch("rionnag.services.scrim_opportunities.time.time", return_value=self.now):
            await self.publisher.vote(self.interaction(1), "3:0", False)
        self.assertEqual(self.store.opportunity_votes(10, "3:0"), [])
        self.assertEqual(self.store.finder_overrides(10, "3:0"), {})

    async def test_public_listing_does_not_notify_players_or_owner(self):
        await self.publisher.sync(self.now)
        for mid in range(1, 7):
            self.store.vote_opportunity(10, "3:0", mid, True)
        await self.publisher.sync(self.now)
        self.owner.send.assert_not_awaited()
        self.assertEqual(self.store.finder_notices(10), {})
        listing = self.channel.send.await_args.kwargs["embed"]
        available = next(field for field in listing.fields if field.name == "Available players (7)")
        self.assertIn("<@1>", available.value)
        self.assertEqual(self.channel.send.await_args.kwargs["allowed_mentions"].to_dict(),
                         discord.AllowedMentions.none().to_dict())

    async def test_withdrawals_do_not_create_owner_prompts(self):
        await self.publisher.sync(self.now)
        for mid in range(1, 7):
            self.store.vote_opportunity(10, "3:0", mid, True)
        await self.publisher.sync(self.now)
        self.store.vote_opportunity(10, "3:0", 6, False)
        await self.publisher.sync(self.now)
        start = instant(offer()["Start_Time_timestamp"])
        self.assertNotIn(start, self.store.finder_notices(10))
        self.owner.send.assert_not_awaited()

    async def test_three_players_do_not_trigger_invitation_dms(self):
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
        dm.send.assert_not_awaited()
        await self.publisher.sync(self.now)
        dm.send.assert_not_awaited()

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

    async def test_no_invitation_dm_is_sent_after_restart(self):
        dm = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=77)))
        member = SimpleNamespace(bot=False, roles=[SimpleNamespace(id=7)],
                                 create_dm=AsyncMock(return_value=dm))
        original = self.guild.get_member
        self.guild.get_member = lambda mid: member if mid == 7 else original(mid)
        await self.publisher.sync(self.now)
        for mid in (1, 2, 3):
            self.store.vote_opportunity(10, "3:0", mid, True)
        await self.publisher.sync(self.now)
        dm.send.assert_not_awaited()
        self.publisher = OpportunityPublisher(
            self.bot, Store(self.store.path), FeedStore(self.feed.path),
            {"marvel-rivals": self.form}, 1, 10)
        await self.publisher.sync(self.now)
        dm.send.assert_not_awaited()

    async def test_old_no_decisions_migrate_to_unavailable_windows(self):
        start = instant(offer()["Start_Time_timestamp"])
        self.store.finder_notification(10, start, 7, "interest", 7200)
        self.store.complete_finder_notification(10, start, 7, "interest", 7200, "declined")
        reopened = Store(self.store.path)
        self.assertEqual(reopened.finder_declined(10, start, start + 7200), {7})

    async def test_existing_per_time_post_is_reused(self):
        self.store.save_opportunity(10, "3:0", 77, "", "active",
                                    instant(offer()["Start_Time_timestamp"]))
        await self.publisher.sync(self.now)
        self.message.delete.assert_not_awaited()
        self.assertEqual(self.store.opportunity_posts(10)["3:0"]["message_id"], 99)

    async def test_host_official_day_suppresses_finder(self):
        self.store.book_host(config.SCRIM_HOST_CHANNEL_ID,
                             instant("2027-06-08T01:00:00+00:00"), 7200, 100)
        await self.publisher.sync(self.now)
        self.channel.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
