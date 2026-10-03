import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import discord
from rivals_api import RivalsAPIError

from rionnag.integrations.rivals import add_hero_fields, fetch_player_overview, profile_overview


class OverviewTests(unittest.TestCase):
    def fetch(self, rate=57, partial=False, blocked_season=False, blocked_rank=False):
        metadata = {"provider_errors": ["offline"] if partial else [], "unresolved": []}

        def response(data):
            value = Mock()
            value.to_dict.return_value = data
            return value

        stats = SimpleNamespace(
            win_rate=Mock(return_value=response({"win_rate_pct": rate, "metadata": metadata})),
            hero_win_rates=Mock(
                return_value=response(
                    {
                        "data": [
                            {"hero_id": "1016", "hero_name": "Loki", "games": 12, "win_rate_pct": 58},
                            {"hero_id": "1015", "hero_name": "Iron Man", "games": 2, "win_rate_pct": 50},
                        ],
                        "metadata": metadata,
                    }
                )
            ),
            class_win_rates=Mock(
                return_value=response(
                    {
                        "data": [{"player_class": "support", "games": 12, "win_rate_pct": 58}],
                        "metadata": metadata,
                    }
                )
            ),
            summary_heroes=Mock(return_value=[SimpleNamespace(hero_id=1016, rank=324)]),
        )
        player = SimpleNamespace(
            uid=123,
            name="Test",
            stats=stats,
            rank_game_season={"100120": SimpleNamespace(rank_game_id=20, rank_score=3600)},
        )
        if blocked_rank:
            stats.summary_heroes.side_effect = [
                RivalsAPIError("summary blocked"),
                [SimpleNamespace(hero_id=1016, rank=324)],
            ]
        if not blocked_season:

            def rate_after_ranks(**kwargs):
                self.assertEqual(stats.summary_heroes.call_count, 2)
                return response({"win_rate_pct": rate, "metadata": metadata})

            stats.win_rate.side_effect = rate_after_ranks
        if blocked_season:
            stats.win_rate.side_effect = [RivalsAPIError("season blocked"), response({"win_rate_pct": 61})]
        with patch("rionnag.integrations.rivals.RivalsClient") as client:
            client.return_value.__enter__.return_value.get_player.return_value = player
            result = fetch_player_overview("123")
        return result, stats

    def test_canonical_calls_use_default_modes_and_current_season(self):
        result, stats = self.fetch()
        stats.win_rate.assert_called_once_with(season="current")
        stats.hero_win_rates.assert_called_once_with(season="current")
        stats.class_win_rates.assert_called_once_with(season="current")
        self.assertEqual(result["stats_mode"], "all")
        fields, _ = profile_overview(result)
        self.assertEqual(fields["hero_win_rates_name"], "Top 6 Characters (Current Season)")
        self.assertIn("58% WR `#324`", fields["competitive_heroes"])
        self.assertTrue(fields["competitive_heroes"].startswith("**Loki"))
        self.assertIn("Strategist", fields["role_win_rates"])
        stats.summary_heroes.assert_any_call(mode="competitive", season="all")
        stats.summary_heroes.assert_any_call(mode="quickplay", season="all")

    def test_zero_rate_is_preserved_and_missing_rate_not_replaced_with_competitive_snapshot(self):
        for rate, expected in [(0, "0%"), (None, "Unavailable")]:
            result, _ = self.fetch(rate=rate)
            fields, _ = profile_overview(result)
            self.assertEqual(fields["overall_win_rate"], expected)

    def test_partial_provider_coverage_is_visible(self):
        result, _ = self.fetch(partial=True)
        self.assertIn("coverage is partial", result["win_rate_note"])

    def test_profile_adds_one_combined_hero_field_with_rank(self):
        result, _ = self.fetch()
        fields, _ = profile_overview(result)
        embed = discord.Embed()
        add_hero_fields(embed, fields)
        hero_fields = [field for field in embed.fields if "Characters" in field.name]
        self.assertEqual(len(hero_fields), 1)
        self.assertEqual(hero_fields[0].name, "Top 6 Characters (Current Season)")
        self.assertIn("58% WR `#324`", hero_fields[0].value)

    def test_failed_rank_mode_does_not_skip_other_mode(self):
        with self.assertLogs("rionnag.integrations.rivals", level="WARNING"):
            result, _ = self.fetch(blocked_rank=True)
        fields, _ = profile_overview(result)
        self.assertIn("58% WR `#324`", fields["competitive_heroes"])
        self.assertIn("leaderboard placements", result["win_rate_note"])

    def test_blocked_current_season_never_falls_back_to_all_seasons(self):
        with self.assertLogs("rionnag.integrations.rivals", level="ERROR"):
            result, stats = self.fetch(blocked_season=True)
        stats.win_rate.assert_called_once_with(season="current")
        fields, _ = profile_overview(result)
        self.assertEqual(fields["overall_win_rate"], "Unavailable")
        self.assertEqual(fields["overall_win_rates_name"], "Current Season Win Rate")
