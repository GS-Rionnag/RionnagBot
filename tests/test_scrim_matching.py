import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from rionnag.scrims.scrim_feed import FeedStore
from rionnag.services.scrim_opportunities import (
    OpportunityPublisher,
    covers_interval,
    match_offer,
    offer_interval,
)
from rionnag.storage import Store
from rionnag.ui.scrim_opportunities import matched_offer_embed


def instant(text):
    return int(datetime.fromisoformat(text).timestamp())


def answers(zone="Eastern Time (ET)", day="Monday", start=20, end=22):
    return {"time_zone": zone, "availability_days": {day: {"start": start, "end": end}},
            "username": "Example", "preferred_role_1": "Tank", "preferred_role_2": "DPS"}


def offer(start="2027-06-08T00:00:00Z", end=None):
    return {"Start_Time_timestamp": start, "End_Time_timestamp": end,
            "rank_minimum": "Diamond", "rank_maximum": "Grandmaster",
            "messageURL": "https://discord.com/channels/1/2/3"}


class MatchingTests(unittest.TestCase):
    def test_minimum_hour_and_full_advertised_duration(self):
        start, end = offer_interval(offer())
        self.assertEqual(end - start, 3600)
        self.assertTrue(covers_interval(answers(end=21), start, end))
        longer = offer(end="2027-06-08T02:00:00Z")
        self.assertEqual(match_offer(longer, [{"member_id": 1, "answers": answers(end=21)}], start-1), [])
        self.assertIsNone(offer_interval(offer(end="2027-06-08T00:30:00Z")))
        self.assertIsNone(offer_interval(offer(end="invalid")))

    def test_four_real_players_not_partial_or_rotating_coverage(self):
        players = [{"member_id": n, "answers": answers(end=21)} for n in range(1, 5)]
        start, _ = offer_interval(offer())
        self.assertEqual(match_offer(offer(), players, start-1), [1, 2, 3, 4])
        players[-1]["answers"] = answers(start=21)
        self.assertEqual(match_offer(offer(), players, start-1), [1, 2, 3])
        self.assertEqual(match_offer(offer(), players + [players[0]], start-1), [1, 2, 3])
        self.assertEqual(match_offer(offer(), players, start), [1, 2, 3])
        self.assertEqual(match_offer(offer(), players, start+3600), [])

    def test_cross_zone_weekday_and_overnight(self):
        start, end = offer_interval(offer("2027-06-08T06:00:00Z"))
        self.assertTrue(covers_interval(answers("Pacific Time (PT)", start=23, end=1), start, end))
        self.assertFalse(covers_interval(answers("Eastern Time (ET)", start=23, end=1), start, end))
        start, end = offer_interval(offer())
        self.assertTrue(covers_interval(answers("Central Time (CT)", start=19, end=20), start, end))
        self.assertFalse(covers_interval(answers("Central Time (CT)", start=20, end=21), start, end))

    def test_contiguous_days_cover_midnight_but_gap_does_not(self):
        a = answers(start=23, end=24)
        a["availability_days"]["Tuesday"] = {"start": 0, "end": 2}
        start = instant("2027-06-08T03:30:00+00:00")
        self.assertTrue(covers_interval(a, start, start+3600))
        a["availability_days"]["Tuesday"]["start"] = 1
        self.assertFalse(covers_interval(a, start, start+3600))

    def test_dst_uses_scrim_date_and_conservative_transition_boundaries(self):
        summer = instant("2027-06-08T00:00:00+00:00")
        winter = instant("2027-01-05T01:00:00+00:00")
        self.assertTrue(covers_interval(answers(), summer, summer+3600))
        self.assertTrue(covers_interval(answers(), winter, winter+3600))
        self.assertFalse(covers_interval(answers(), winter-3600, winter))
        spring = instant("2027-03-14T07:00:00+00:00")
        self.assertFalse(covers_interval(answers(day="Sunday", start=2, end=4), spring, spring+3600))
        fall_first = instant("2027-11-07T05:00:00+00:00")
        self.assertFalse(covers_interval(answers(day="Sunday", start=1, end=3), fall_first, fall_first+3600))
        self.assertTrue(covers_interval(
            answers(day="Sunday", start=1, end=3), fall_first+3600, fall_first+7200
        ))

    def test_missing_or_invalid_schedule_is_not_assumed_available(self):
        start, end = offer_interval(offer())
        for a in ({}, {"time_zone": "Unknown", "availability_days": {"Monday": {"start": 20, "end": 22}}},
                  {"time_zone": "Eastern Time (ET)", "availability_days": "everyday"},
                  answers(start=True), answers(end=30)):
            self.assertFalse(covers_interval(a, start, end))

    def test_embed_shows_date_source_and_assumed_hour_without_private_answers(self):
        embed = matched_offer_embed(offer(), [1, 2, 3, 4], "3:0")
        self.assertIn("1 hour", embed.description)
        self.assertIn(":F>", embed.description)
        self.assertNotIn("Available players", {field.name for field in embed.fields})
        self.assertNotIn("Based on saved availability", embed.description)
        self.assertEqual(embed.url, offer()["messageURL"])
        self.assertEqual(embed.footer.text, "Scrim finder • 3:0")


class PublisherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.store, self.feed = Store(root / "members.db"), FeedStore(root / "feed.db")
        self.form = {"name": "Marvel Rivals", "version": 1,
                     "tryout_role": 7, "team_role": 8, "manager_role": 9}
        for mid in range(1, 6):
            self.store.update(mid, status="accepted", game="marvel-rivals", version=1, answers=answers())
            self.store.save_profile(1, mid, self.form, answers())
        self.source = {"id": "3", "channel_id": "2", "url": offer()["messageURL"]}
        self.feed.put(self.source)
        self.feed.complete(self.feed.pending(), {"scrims": [{**offer(), "source_message_id": "3"}]})
        self.message = SimpleNamespace(id=99, edit=AsyncMock(), delete=AsyncMock(),
                                       author=SimpleNamespace(id=999), embeds=[])
        self.channel = SimpleNamespace(
            send=AsyncMock(return_value=self.message),
            fetch_message=AsyncMock(return_value=self.message), history=MagicMock()
        )
        self.guild = SimpleNamespace(get_channel=lambda cid: self.channel,
                                     get_member=lambda mid: SimpleNamespace(bot=False,
                                                                          roles=[SimpleNamespace(id=7)]))
        self.bot = SimpleNamespace(get_guild=lambda gid: self.guild, user=SimpleNamespace(id=999),
                                   add_view=MagicMock())
        self.publisher = OpportunityPublisher(self.bot, self.store, self.feed,
                                              {"marvel-rivals": self.form}, 1, 10)
        self.now = instant("2027-06-07T23:00:00+00:00")

    async def test_publishes_once_across_restart_and_updates_roster(self):
        await self.publisher.sync(self.now)
        self.channel.send.assert_awaited_once()
        sent = self.channel.send.call_args.kwargs
        self.assertEqual({u.id for u in sent["allowed_mentions"].users}, {1, 2, 3, 4, 5})
        self.assertEqual(sent["content"], "<@1> <@2> <@3> <@4> <@5>")
        self.assertEqual(self.channel.send.call_args.kwargs["embed"].title,
                         "Monday, June 7, 2027 8:00 PM – 9:00 PM")
        self.publisher = OpportunityPublisher(self.bot, Store(self.store.path), self.feed,
                                              {"marvel-rivals": self.form}, 1, 10)
        await self.publisher.sync(self.now)
        self.channel.send.assert_awaited_once()
        self.message.edit.assert_not_awaited()
        self.store.update(5, answers=answers(start=22))
        await self.publisher.sync(self.now)
        self.assertEqual(self.message.edit.call_args.kwargs["content"], "<@1> <@2> <@3> <@4>")
        self.assertFalse(self.message.edit.call_args.kwargs["allowed_mentions"].users)
        self.channel.send.assert_awaited_once()

    async def test_under_four_never_posts_and_stale_members_are_excluded(self):
        self.store.update(4, status="pending")
        self.store.update(5, version=0)
        await self.publisher.sync(self.now)
        self.channel.send.assert_not_awaited()
        self.store.update(4, status="accepted", restore_roles=[7])
        self.store.update(5, version=1)
        self.guild.get_member = lambda mid: None if mid == 5 else SimpleNamespace(
            bot=False, roles=[SimpleNamespace(id=7)]
        )
        await self.publisher.sync(self.now)
        self.channel.send.assert_not_awaited()

    async def test_deletes_after_availability_changes_and_can_post_again(self):
        await self.publisher.sync(self.now)
        for mid in (4, 5):
            self.store.update(mid, answers=answers(start=22))
        await self.publisher.sync(self.now)
        self.message.delete.assert_awaited_once()
        self.assertEqual(self.store.opportunity_posts(10)["3:0"]["status"], "inactive")
        self.store.update(4, answers=answers())
        await self.publisher.sync(self.now)
        self.assertEqual(self.channel.send.call_args.kwargs["content"], "<@1> <@2> <@3> <@4>")
        self.assertEqual(self.channel.send.await_count, 2)

    async def test_source_edit_updates_same_message_and_deletion_withdraws(self):
        await self.publisher.sync(self.now)
        self.feed.put({**self.source, "content": "Updated"})
        self.feed.complete(self.feed.pending(), {"scrims": [
            {**offer(), "source_message_id": "3", "rank_maximum": "Celestial"}
        ]})
        await self.publisher.sync(self.now)
        self.assertIn("Celestial", self.message.edit.call_args.kwargs["embed"].fields[0].value)
        self.feed.delete("3")
        await self.publisher.sync(self.now)
        self.message.delete.assert_awaited_once()
        self.channel.send.assert_awaited_once()

    async def test_ongoing_offers_remain_until_end_then_are_deleted(self):
        await self.publisher.sync(self.now)
        await self.publisher.sync(instant(offer()["Start_Time_timestamp"]))
        self.message.delete.assert_not_awaited()
        await self.publisher.sync(instant(offer()["Start_Time_timestamp"]) + 3600)
        self.message.delete.assert_awaited_once()

    async def test_saved_rank_filter_removes_out_of_range_posts_and_blocks_votes(self):
        await self.publisher.sync(self.now)
        self.store.set_scrim_rank_filter(10, ("Celestial", "Eternity"))
        blocked = self.interaction(1)
        with patch("rionnag.services.scrim_opportunities.time.time", return_value=self.now):
            await self.publisher.vote(blocked, "3:0", True)
        self.assertIn("no longer", blocked.followup.send.call_args.args[0])
        self.assertEqual(self.store.opportunity_votes(10, "3:0"), [])
        await self.publisher.sync(self.now)
        self.message.delete.assert_awaited_once()
        self.store.set_scrim_rank_filter(10, ("Grandmaster", "Grandmaster"))
        await self.publisher.sync(self.now)
        self.assertEqual(self.channel.send.await_count, 2)
        self.assertFalse(self.channel.send.call_args.kwargs["allowed_mentions"].users)

    async def test_lost_send_ack_recovers_without_duplicate(self):
        self.store.reserve_opportunity(10, "3:0")
        self.message.embeds = [matched_offer_embed(offer(), [1, 2, 3, 4, 5], "3:0")]

        async def history(**kwargs):
            yield self.message

        self.channel.history = history
        await self.publisher.sync(self.now)
        self.channel.send.assert_not_awaited()
        self.message.edit.assert_awaited_once()
        self.assertEqual(self.store.opportunity_posts(10)["3:0"]["message_id"], 99)

    def interaction(self, mid, guild_id=1):
        return SimpleNamespace(user=SimpleNamespace(id=mid), guild_id=guild_id, channel_id=10,
                               message=self.message, response=SimpleNamespace(defer=AsyncMock()),
                               followup=SimpleNamespace(send=AsyncMock()))

    async def test_votes_persist_are_idempotent_and_only_remove_own_vote(self):
        await self.publisher.sync(self.now)
        with patch("rionnag.services.scrim_opportunities.time.time", return_value=self.now):
            one, two = self.interaction(1), self.interaction(2)
            await self.publisher.vote(one, "3:0", True)
            await self.publisher.vote(one, "3:0", True)
            await self.publisher.vote(two, "3:0", True)
            self.assertEqual(self.store.opportunity_votes(10, "3:0"), [1, 2])
            self.assertEqual(self.message.edit.call_args.kwargs["embed"].fields[1].name, "Voted 2/6")
            await self.publisher.vote(one, "3:0", False)
            self.assertEqual(Store(self.store.path).opportunity_votes(10, "3:0"), [2])
        self.publisher.register_views()
        self.bot.add_view.assert_called_once()
        view = self.bot.add_view.call_args.args[0]
        self.assertTrue(view.is_persistent())
        self.assertEqual([b.style.name for b in view.children], ["success", "danger"])

    async def test_vote_restrictions_include_roles_and_completed_registry(self):
        await self.publisher.sync(self.now)
        self.store.update(5, status="pending")
        self.guild.get_member = lambda mid: SimpleNamespace(
            bot=False, roles=[] if mid == 4 else [SimpleNamespace(id=7)]
        )
        with patch("rionnag.services.scrim_opportunities.time.time", return_value=self.now):
            for interaction in (self.interaction(5), self.interaction(4), self.interaction(1, guild_id=2)):
                await self.publisher.vote(interaction, "3:0", True)
                self.assertIn("Only Marvel Rivals", interaction.followup.send.call_args.args[0])
        self.assertEqual(self.store.opportunity_votes(10, "3:0"), [])

    async def test_six_vote_cap_and_ended_offer_rejects_votes(self):
        for mid in (6, 7):
            self.store.update(mid, status="accepted", game="marvel-rivals", version=1, answers=answers())
            self.store.save_profile(1, mid, self.form, answers())
        await self.publisher.sync(self.now)
        with patch("rionnag.services.scrim_opportunities.time.time", return_value=self.now) as clock:
            for mid in range(1, 7):
                await self.publisher.vote(self.interaction(mid), "3:0", True)
            seventh = self.interaction(7)
            await self.publisher.vote(seventh, "3:0", True)
            self.assertIn("Six players", seventh.followup.send.call_args.args[0])
            self.assertEqual(len(self.store.opportunity_votes(10, "3:0")), 6)
            clock.return_value = instant(offer()["Start_Time_timestamp"])+3600
            ended = self.interaction(1)
            await self.publisher.vote(ended, "3:0", False)
            self.assertIn("no longer", ended.followup.send.call_args.args[0])

    async def test_departure_and_reset_remove_votes_and_deleted_source_clears_them(self):
        await self.publisher.sync(self.now)
        self.store.vote_opportunity(10, "3:0", 1, True)
        self.store.vote_opportunity(10, "3:0", 2, True)
        self.store.delete_member(1, 1)
        self.assertEqual(self.store.opportunity_votes(10, "3:0"), [2])
        self.store.update(2, status="reset")
        await self.publisher.sync(self.now)
        self.assertEqual(self.store.opportunity_votes(10, "3:0"), [])
        self.message.delete.assert_awaited_once()
