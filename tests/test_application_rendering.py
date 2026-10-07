import asyncio
import re
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import discord

from rionnag import config
from rionnag.services.applications import Applications
from rionnag.ui.availability import schedule_text
from rionnag.ui.player_stats import player_stats_embed


class RenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_stats_do_not_block_publication_or_decision_and_ignore_stale_results(self):
        row = {
            "status": "pending", "game": "marvel-rivals", "channel_id": 100,
            "message_id": 7, "version": 1,
            "answers": {"username": "Test", "player_uid": "123"},
        }
        store = Mock()
        store.member.side_effect = lambda _: dict(row, answers=dict(row["answers"]))
        app = Applications(Mock(), store, config.load_forms())
        member = SimpleNamespace(id=42, mention="<@42>", guild=Mock())
        message = SimpleNamespace(id=7, edit=AsyncMock())
        channel = SimpleNamespace(
            id=100, edit=AsyncMock(), fetch_message=AsyncMock(return_value=message), send=AsyncMock()
        )
        started, release = asyncio.Event(), asyncio.Event()

        async def slow_stats(answers):
            started.set()
            await release.wait()
            answers["rivals_stats_embed"] = {"title": "New stats"}
            return discord.Embed(title="New stats")

        with (
            patch("rionnag.services.applications.ticket_overwrites", return_value={}),
            patch("rionnag.ui.player_stats.player_stats_embed", side_effect=slow_stats),
        ):
            await asyncio.wait_for(app.publish(member, channel), 1)
            await asyncio.wait_for(started.wait(), 1)
            self.assertIn("ready for review", message.edit.call_args.kwargs["embeds"][1].description)
            # Acceptance can obtain the member lock even while stats are still loading.
            await asyncio.wait_for(app.lock(42).acquire(), 1)
            row["status"] = "accepted"
            row["channel_id"] = None
            app.lock(42).release()
            release.set()
            await app.stats_tasks[42]
        message.edit.assert_awaited_once()
        store.update.assert_not_called()

    def test_timestamps_use_selected_zone_and_next_midnight(self):
        days = {"Sunday": {"start": 9, "end": 24}}
        now = datetime(2026, 10, 3, 18, tzinfo=UTC)
        text = schedule_text(days, "Pacific Time (PT)", now)
        stamps = re.findall(r"<t:(\d+):t>", text)
        self.assertEqual(len(stamps), 2)
        dates = [datetime.fromtimestamp(int(s), ZoneInfo("America/Los_Angeles")) for s in stamps]
        self.assertEqual((dates[0].weekday(), dates[0].hour), (6, 9))
        self.assertEqual((dates[1].weekday(), dates[1].hour), (0, 0))
        self.assertEqual((dates[1] - dates[0]).total_seconds(), 15 * 3600)

    async def test_stats_cache_is_bound_to_selected_account(self):
        answers = {"player_uid": "123", "username": "Test"}
        fields = dict(
            current_rank="Gold",
            peak_rank="Diamond",
            overall_win_rate="50%",
            role_win_rates_name="Roles",
            role_win_rates="Tank: 50%",
            top_characters="Thor",
        )
        with (
            patch(
                "rionnag.ui.player_stats.queued_lookup",
                return_value={
                    "player_name": "Test",
                    "player_uid": "123",
                    "match_hero_rates": [1],
                    "match_class_rates": [1],
                },
            ) as lookup,
            patch("rionnag.ui.player_stats.profile_overview", return_value=(fields, None)),
        ):
            await player_stats_embed(answers)
            await player_stats_embed(answers)
            self.assertEqual(lookup.call_count, 1)
            answers["player_uid"] = "456"
            await player_stats_embed(answers)
            self.assertEqual(lookup.call_count, 2)

    async def test_old_embed_cache_is_refreshed_without_resetting_form_answers(self):
        answers = {
            "player_uid": "123",
            "username": "Test",
            "rivals_stats_uid": "123",
            "rivals_stats_embed": discord.Embed(title="Old stats").to_dict(),
        }
        fields = dict(
            current_rank="Gold",
            peak_rank="Diamond",
            overall_win_rate="57%",
            role_win_rates_name="Roles",
            role_win_rates="Tank: 50%",
            top_characters="Thor",
        )
        with (
            patch(
                "rionnag.ui.player_stats.queued_lookup",
                return_value={"player_name": "Test", "player_uid": "123"},
            ) as lookup,
            patch("rionnag.ui.player_stats.profile_overview", return_value=(fields, None)),
        ):
            embed = await player_stats_embed(answers)
        lookup.assert_called_once()
        self.assertEqual(answers["username"], "Test")
        self.assertEqual(embed.fields[4].value, "57%")

    async def test_existing_application_has_two_embeds_and_attachment_removed(self):
        row = {
            "status": "pending",
            "channel_id": 100,
            "version": 1,
            "game": "marvel-rivals",
            "message_id": 7,
            "answers": {
                "username": "Test",
                "availability_days": {"Sunday": {"start": 9, "end": 24}},
                "time_zone": "Eastern Time (ET)",
            },
        }
        store = Mock()
        store.member.return_value = row
        app = Applications(Mock(), store, config.load_forms())
        member = SimpleNamespace(id=42, mention="<@42>", guild=Mock())
        channel = SimpleNamespace(id=100, edit=AsyncMock(), fetch_message=AsyncMock(), send=AsyncMock())
        message = SimpleNamespace(id=7, edit=AsyncMock())
        channel.fetch_message.return_value = message
        with (
            patch("rionnag.services.applications.ticket_overwrites", return_value={}),
            patch(
                "rionnag.ui.player_stats.player_stats_embed",
                new=AsyncMock(return_value=discord.Embed(title="Player data")),
            ),
        ):
            await app.publish(member, channel)
            payload = message.edit.call_args.kwargs
            await app.stats_tasks[42]
        self.assertEqual(len(payload["embeds"]), 2)
        self.assertEqual(payload["attachments"], [])
        self.assertIn("<t:", payload["embeds"][0].fields[-1].value)
        channel.send.assert_not_awaited()
