import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rionnag.integrations.rivals import fetch_player_overview, profile_overview


class OverviewTests(unittest.TestCase):
    def fetch(self, cached_rate=None, direct_rate=57, quickplay_only=False):
        matches = Mock()
        calls = []

        def overall(**kwargs):
            calls.append(("overall", kwargs))
            return {"win_rate_pct": cached_rate}

        def categorized(kind):
            def fetch(**kwargs):
                calls.append((kind, kwargs))
                result = Mock()
                data = {
                    "data": [{"hero_id": "1016", "hero_name": "Loki", "matches": 12, "win_rate_pct": 58}]
                    if kind == "heroes"
                    else []
                }
                if quickplay_only and kwargs["mode"] == "competitive":
                    data = {"data": []}
                elif quickplay_only and kind == "classes":
                    data = {"data": [{"player_class": "support", "win_rate_pct": 58}]}
                result.to_dict.return_value = data
                result.get.side_effect = data.get
                return result

            return fetch

        matches.fetch_win_rate.side_effect = overall
        matches.fetch_hero_win_rates.side_effect = categorized("heroes")
        matches.fetch_class_win_rates.side_effect = categorized("classes")
        player = SimpleNamespace(
            uid=123,
            name="Test",
            win_rate=direct_rate,
            matches=matches,
            rank_game_season={"100120": SimpleNamespace(rank_game_id=20, rank_score=3600)},
            stats=SimpleNamespace(heroes=Mock(return_value=[SimpleNamespace(hero_id=1016, rank=324)])),
        )
        with patch("rionnag.integrations.rivals.RivalsClient") as client:
            client.return_value.__enter__.return_value.get_player.return_value = player
            result = fetch_player_overview("123")
        return result, calls

    def test_exact_first_then_all_rates_cached_and_season_fallback(self):
        result, calls = self.fetch()
        self.assertEqual(calls[0], ("overall", {"method": "exact"}))
        self.assertEqual([kwargs["method"] for _, kwargs in calls[1:]], ["cached"] * 3)
        self.assertEqual(calls[1][1]["season"], 20)
        self.assertEqual(result["win_rate"], 57)
        overview, _ = profile_overview(result)
        self.assertIn("58% WR `#324`", overview["competitive_heroes"])

    def test_valid_zero_cached_rate_is_preserved(self):
        result, _ = self.fetch(cached_rate=0)
        self.assertEqual(result["win_rate"], 0)

    def test_quickplay_only_account_uses_cached_fallback_and_truthful_labels(self):
        result, calls = self.fetch(quickplay_only=True)
        self.assertEqual(calls[0][1]["method"], "exact")
        self.assertTrue(all(kwargs["method"] == "cached" for _, kwargs in calls[1:]))
        self.assertEqual(result["stats_mode"], "quickplay")
        fields, _ = profile_overview(result)
        self.assertIn("Quick Play", fields["hero_win_rates_name"])
        self.assertIn("Quick Play", fields["role_win_rates_name"])
        self.assertIn("58% WR `#324`", fields["competitive_heroes"])
        self.assertIn("Strategist", fields["role_win_rates"])

    def test_missing_profile_and_history_rate_remains_unavailable(self):
        result, _ = self.fetch(direct_rate=None)
        overview, _ = profile_overview(result)
        self.assertEqual(overview["overall_win_rate"], "Unavailable")
