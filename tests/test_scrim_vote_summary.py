import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from rionnag.services.scrim_opportunities import OpportunityPublisher
from rionnag.storage import Store


class VoteSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = Store(Path(folder.name) / "members.db")
        self.messages = []
        self.serial = 100

        async def send(**kwargs):
            self.serial += 1
            message = SimpleNamespace(id=self.serial, author=SimpleNamespace(id=9),
                                      embeds=[kwargs["embed"]], delete=AsyncMock(), edit=AsyncMock())
            self.messages.insert(0, message)
            return message

        async def history(**kwargs):
            for message in self.messages:
                yield message

        self.channel = SimpleNamespace(send=AsyncMock(side_effect=send), history=history,
                                       fetch_message=AsyncMock())
        self.publisher = OpportunityPublisher(SimpleNamespace(user=SimpleNamespace(id=9)),
                                              self.store, None, {}, 1, 10)
        for key, mid, start in (("a", 11, 200), ("b", 12, 300), ("c", 13, 400)):
            self.store.save_opportunity(10, key, mid, "", "active", start)

    async def test_ties_links_and_leader_change_edit_without_ping(self):
        for key in ("a", "b"):
            self.store.vote_opportunity(10, key, 1, True)
        await self.publisher.sync_vote_summary(self.channel, 100)
        message = self.messages[0]
        self.channel.fetch_message.return_value = message
        description = message.embeds[0].description
        self.assertIn("1/6 votes", description)
        self.assertIn("/11)", description)
        self.assertIn("/12)", description)
        self.assertIn(", ", description)
        self.assertNotIn("/13)", description)
        self.store.vote_opportunity(10, "b", 2, True)
        await self.publisher.sync_vote_summary(self.channel, 100)
        updated = message.edit.call_args.kwargs
        self.assertIn("2/6 votes", updated["embed"].description)
        self.assertNotIn("/11)", updated["embed"].description)
        self.assertFalse(updated["allowed_mentions"].users)
        self.channel.send.assert_awaited_once()

    async def test_moves_to_bottom_and_recovers_after_restart(self):
        await self.publisher.sync_vote_summary(self.channel, 100)
        original = self.messages[0]
        self.channel.fetch_message.return_value = original
        self.messages.insert(0, SimpleNamespace(id=500, author=SimpleNamespace(id=8), embeds=[]))
        await self.publisher.sync_vote_summary(self.channel, 100)
        original.delete.assert_awaited_once()
        newest = self.messages[0]
        self.assertEqual(self.store.vote_summary_message(10), newest.id)
        self.messages = [newest]
        self.channel.fetch_message.return_value = newest
        self.publisher.store = Store(self.store.path)
        await self.publisher.sync_vote_summary(self.channel, 100)
        self.assertEqual(self.channel.send.await_count, 2)
        newest.edit.assert_not_awaited()

    async def test_expired_and_inactive_posts_excluded_and_empty_message(self):
        self.store.save_opportunity(10, "c", 13, "", "inactive", 400)
        await self.publisher.sync_vote_summary(self.channel, 300)
        self.assertEqual(self.messages[0].embeds[0].description, "No upcoming scrim posts.")

    async def test_recovers_unpersisted_summary_without_duplicate(self):
        embed = discord.Embed(title="Old summary")
        embed.set_footer(text="Scrim finder vote summary")
        self.messages = [SimpleNamespace(id=77, author=SimpleNamespace(id=9), embeds=[embed],
                                         edit=AsyncMock(), delete=AsyncMock())]
        await self.publisher.sync_vote_summary(self.channel, 100)
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.store.vote_summary_message(10), 77)
