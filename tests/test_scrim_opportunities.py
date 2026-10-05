import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from rionnag.cogs.scrims import Scrims
from rionnag.scrims.scrim_feed import FeedStore


class OpportunityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.feed = FeedStore(self.root / "data" / "scrim_feed.sqlite3")
        self.cog = object.__new__(Scrims)
        self.cog.authorize = lambda interaction, game: True
        self.interaction = SimpleNamespace(
            user=SimpleNamespace(id=4),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

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

    async def test_non_manager_cannot_open_preview(self):
        self.cog.authorize = lambda interaction, game: False
        await self.invoke()
        self.interaction.response.send_message.assert_awaited_once_with(
            "Only Marvel Rivals Managers can view scrim offers.", ephemeral=True
        )
