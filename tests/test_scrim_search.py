import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from rionnag import config
from rionnag.services.permissions import apply_server_policy
from rionnag.services.scrim_search import normalize_filter, rank_matches, rank_suggestions
from rionnag.storage import Store
from rionnag.ui.scrim_opportunities import matched_offer_embed


class RankSearchTests(unittest.TestCase):
    def test_single_tier_alias_range_and_divisions(self):
        self.assertEqual(normalize_filter("gm"), ("Grandmaster", "Grandmaster"))
        self.assertEqual(normalize_filter("dia", "cel"), ("Diamond", "Celestial"))
        self.assertFalse(rank_matches({"rank_minimum": "Diamond", "rank_maximum": "Grandmaster"},
                                      normalize_filter("gm")))
        self.assertTrue(rank_matches({"rank_minimum": "Grandmaster III", "rank_maximum": "Grandmaster III"},
                                     normalize_filter("gm")))
        self.assertFalse(rank_matches({"rank_minimum": "Celestial", "rank_maximum": "Eternity"},
                                      normalize_filter("gm")))
        self.assertFalse(rank_matches({"rank_minimum": "Grandmaster III", "rank_maximum": "Grandmaster III"},
                                      normalize_filter("gm I")))

    def test_diamond_to_celestial_requires_both_endpoints_inside(self):
        ranks = normalize_filter("Diamond", "Celestial")
        for low, high in (("Diamond", "Grandmaster"), ("Diamond", "Celestial"),
                          ("Celestial", "Celestial"), ("Grandmaster III", "Celestial I")):
            self.assertTrue(rank_matches({"rank_minimum": low, "rank_maximum": high}, ranks))
        for low, high in (("Celestial", "Eternity"), ("Platinum", "Diamond"),
                          ("Bronze", "One Above All"), ("Eternity", "Celestial")):
            self.assertFalse(rank_matches({"rank_minimum": low, "rank_maximum": high}, ranks))

    def test_invalid_ranges_and_clear(self):
        for minimum, maximum in (("gm", "dia"), ("gm I", "gm III"), ("Masters", None), ("Any", "gm")):
            with self.assertRaises(ValueError):
                normalize_filter(minimum, maximum)
        self.assertIsNone(normalize_filter("Any"))
        self.assertTrue(rank_matches({}, None))
        self.assertFalse(rank_matches({}, normalize_filter("gm")))

    def test_settings_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "members.db"
            store = Store(path)
            self.assertIsNone(store.scrim_rank_filter(1))
            store.set_scrim_rank_filter(1, normalize_filter("gm"))
            self.assertEqual(Store(path).scrim_rank_filter(1), ("Grandmaster", "Grandmaster"))
            store.set_scrim_rank_filter(1, None)
            self.assertIsNone(Store(path).scrim_rank_filter(1))

    def test_explicit_rebuild_clears_only_selected_channel_posts_and_votes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "members.db")
            for channel in (1, 2):
                store.save_opportunity(channel, "3:0", 99, "fingerprint", "active")
                store.vote_opportunity(channel, "3:0", 4, True)
                store.set_scrim_rank_filter(channel, normalize_filter("gm"))
            store.reset_opportunity_posts(1)
            self.assertEqual(store.opportunity_posts(1), {})
            self.assertEqual(store.opportunity_votes(1, "3:0"), [])
            self.assertEqual(store.scrim_rank_filter(1), ("Grandmaster", "Grandmaster"))
            self.assertEqual(store.opportunity_posts(2)["3:0"]["message_id"], 99)
            self.assertEqual(store.opportunity_votes(2, "3:0"), [4])

    def test_autocomplete_supports_aliases_and_limit(self):
        self.assertIn("Grandmaster", rank_suggestions("gm"))
        self.assertIn("Diamond", rank_suggestions("dia"))
        self.assertLessEqual(len(rank_suggestions("")), 25)

    def test_time_labels_distinguish_future_and_in_progress(self):
        offer = {"Start_Time_timestamp": "2026-10-06T00:00:00Z", "End_Time_timestamp": None}
        from rionnag.scrims.scrim_offer_rules import timestamp
        start = timestamp(offer["Start_Time_timestamp"])
        before = matched_offer_embed(offer, [1, 2, 3, 4], "3:0", now=start-3600)
        after = matched_offer_embed(offer, [1, 2, 3, 4], "3:0", now=start)
        self.assertIn("Upcoming", before.description)
        self.assertIn("In progress", after.description)
        self.assertIn(f"<t:{start}:R>", before.description)
        self.assertEqual(before.title, "Scrim Found!")
        self.assertNotIn("9:00 PM", before.description + before.title)
        self.assertNotIn("Ends", after.description)
        offer["End_Time_timestamp"] = "2026-10-06T02:00:00Z"
        full = matched_offer_embed(offer, [1, 2, 3, 4], "3:0", now=start-3600)
        self.assertEqual(full.title, "Scrim Found!")
        self.assertIn(f"<t:{start + 7200}:t>", full.description)
        offer["End_Time_timestamp"] = "2026-10-06T05:00:00Z"
        overnight = matched_offer_embed(offer, [1, 2, 3, 4], "3:0", now=start-3600)
        self.assertEqual(overnight.title, "Scrim Found!")
        self.assertIn(f"<t:{start + 18000}:t>", overnight.description)


class FinderPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_repair_preserves_owner_managed_finder_overwrites(self):
        overwrite = discord.PermissionOverwrite(view_channel=False)
        channel = SimpleNamespace(id=config.SCRIM_OPPORTUNITIES_CHANNEL_ID, category_id=1555382746992353290,
                                  overwrites={7: overwrite}, edit=AsyncMock())
        guild = SimpleNamespace(channels=[channel], get_channel=lambda cid: None)
        await apply_server_policy(guild, {}, None)
        channel.edit.assert_not_awaited()
        self.assertFalse(channel.overwrites[7].view_channel)
