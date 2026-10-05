import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from rionnag.cogs.scrims import Scrims
from rionnag.scrims.scrim_feed import FeedStore
from rionnag.storage import Store


class OpportunityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.feed = FeedStore(self.root / "data" / "scrim_feed.sqlite3")
        self.cog = object.__new__(Scrims)
        self.cog.service = SimpleNamespace(store=Store(self.root / "members.db"))
        self.cog.publisher = SimpleNamespace(sync=AsyncMock())
        self.cog.authorize = lambda interaction, game: True
        self.interaction = SimpleNamespace(
            user=SimpleNamespace(id=4),
            response=SimpleNamespace(send_message=AsyncMock()),
            guild=SimpleNamespace(owner_id=4), guild_id=1554260744327929987,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        self.interaction.response.defer = AsyncMock()

    async def invoke(self):
        with patch("rionnag.cogs.scrims.config.ROOT", self.root):
            await Scrims.opportunities.callback(self.cog, self.interaction)

    async def test_empty_feed_reports_no_offers(self):
        await self.invoke()
        self.interaction.response.send_message.assert_awaited_once_with(
            "No collected scrim offers are available.", ephemeral=True
        )

    async def test_collected_offer_opens_preview(self):
        self.feed.put({
            "id": "3", "content": "Diamond scrim", "author_id": "5",
            "url": "https://discord.com/channels/1/2/3",
        })
        self.feed.complete(self.feed.pending(), {"scrims": [{
            "source_message_id": "3", "rank_minimum": "Diamond",
            "rank_maximum": "Diamond", "Start_Time_timestamp": "2099-01-15T08:00:00Z",
            "End_Time_timestamp": None,
        }]})
        await self.invoke()
        self.interaction.response.send_message.assert_awaited_once()
        sent = self.interaction.response.send_message.call_args.kwargs
        self.assertTrue(sent["ephemeral"])
        self.assertEqual(sent["embed"].title, "Scrim opportunity")
        self.assertEqual(sent["view"].offers[0]["messageContent"], "Diamond scrim")
        sent["view"].stop()
        self.interaction.response.send_message.reset_mock()
        self.cog.service.store.set_scrim_rank_filter(1555989494451146802, ("Grandmaster", "Grandmaster"))
        await self.invoke()
        self.interaction.response.send_message.assert_awaited_once_with(
            "No collected scrim offers are available.", ephemeral=True
        )

    async def test_non_manager_cannot_open_preview(self):
        self.cog.authorize = lambda interaction, game: False
        await self.invoke()
        self.interaction.response.send_message.assert_awaited_once_with(
            "Only Marvel Rivals Managers can view scrim offers.", ephemeral=True
        )

    async def test_single_rank_command_saves_default_and_refreshes(self):
        await Scrims.rank_filter.callback(self.cog, self.interaction, "gm")
        self.assertEqual(self.cog.service.store.scrim_rank_filter(1555989494451146802),
                         ("Grandmaster", "Grandmaster"))
        self.cog.publisher.sync.assert_awaited_once()

    async def test_rank_range_clear_and_invalid_range(self):
        await Scrims.rank_filter.callback(self.cog, self.interaction, "dia", "gm")
        self.assertEqual(self.cog.service.store.scrim_rank_filter(1555989494451146802),
                         ("Diamond", "Grandmaster"))
        await Scrims.rank_filter.callback(self.cog, self.interaction, "gm", "dia")
        self.assertIn("below", self.interaction.response.send_message.call_args.args[0])
        self.assertEqual(self.cog.service.store.scrim_rank_filter(1555989494451146802),
                         ("Diamond", "Grandmaster"))
        await Scrims.rank_filter.callback(self.cog, self.interaction, "Any")
        self.assertIsNone(self.cog.service.store.scrim_rank_filter(1555989494451146802))

    async def test_rank_command_denies_non_managers_and_other_guilds(self):
        self.cog.authorize = lambda interaction, game: False
        self.interaction.guild.owner_id = 99
        await Scrims.rank_filter.callback(self.cog, self.interaction, "gm")
        self.assertIsNone(self.cog.service.store.scrim_rank_filter(1555989494451146802))
        self.interaction.guild.owner_id = 4
        self.interaction.guild_id = 123
        await Scrims.rank_filter.callback(self.cog, self.interaction, "gm")
        self.cog.publisher.sync.assert_not_awaited()
