import random
import sqlite3
import tempfile
import unittest
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rionnag.scrims import scrim_rivals
from rionnag.scrims.scrims import (
    Player,
    ScrimStore,
    make_team,
    manual_lineup,
    normalized_roles,
    reroll_team,
    rotate_team,
    substitute_one,
)


def players(per_role=3):
    return [
        Player(
            i * per_role + j + 1,
            f"p{i * per_role + j + 1}",
            (role,),
            str(i * per_role + j + 1),
            joined=i * per_role + j,
        )
        for i, role in enumerate(("Tank", "DPS", "Support"))
        for j in range(per_role)
    ]


class RotationTests(unittest.TestCase):
    def test_main_roles_beat_lower_completion_counts_and_rerolls_keep_preferences(self):
        pool = players(2)
        for p in pool:
            p.played = 99
        pool.append(Player(99, "low-count flex", ("Tank", "DPS")))
        for team in (make_team(pool), reroll_team(pool)):
            lookup = {p.member_id: p for p in pool}
            self.assertTrue(all(role == lookup[mid].roles[0] for mid, role in team.items()))
            self.assertEqual(Counter(team.values()), {"Tank": 2, "DPS": 2, "Support": 2})
        self.assertEqual(pool[-1].roles, ("Tank", "DPS"))
        self.assertEqual(pool[0].played, 99)

    def test_equal_count_subs_favor_older_bench_and_never_reduce_main_role_fit(self):
        pool = players()
        team = make_team(pool)
        history = {p.member_id: asdict(p) for p in pool}
        for p in pool:
            p.played = 1
            history[p.member_id]["played"] = 1
        initial_bench = set(history) - set(team)
        after, incoming, outgoing = substitute_one(pool, team, history)
        self.assertIn(incoming, initial_bench)
        next(p for p in pool if p.member_id == outgoing).benched_at = 1
        after2, incoming2, outgoing2 = substitute_one(pool, after, history, [incoming])
        self.assertIn(incoming2, initial_bench - {incoming})
        self.assertNotEqual(outgoing2, incoming)
        self.assertEqual(Counter(after2.values()), Counter(team.values()))
        # Only a secondary-role DPS substitute remains; current DPS mains stay.
        flex = Player(99, "flex", ("Tank", "DPS"), played=0)
        with self.assertRaisesRegex(ValueError, "No eligible"):
            substitute_one(
                [p for p in pool if p.member_id in team] + [flex],
                team,
                history,
                [mid for mid, role in team.items() if role == "Tank"],
            )

    def test_manual_in_out_and_swaps_preserve_counts_and_validate_roles(self):
        pool = players()
        team = make_team(pool)
        history = {p.member_id: asdict(p) for p in pool}
        starter = next(iter(team))
        bench = next(p for p in pool if p.member_id not in team and team[starter] in p.roles)
        bench.played = 20  # Explicit overrides may replace more-used/protected players.
        for operation, first, second in (
            ("in", bench.member_id, starter),
            ("out", starter, None),
            ("swap", starter, bench.member_id),
        ):
            after = manual_lineup(pool, team, history, operation, first, second)
            self.assertIn(bench.member_id, after)
            self.assertNotIn(starter, after)
            self.assertEqual(Counter(after.values()), Counter(team.values()))
        incompatible = next(p for p in pool if p.member_id not in team and team[starter] not in p.roles)
        with self.assertRaisesRegex(ValueError, "preferred roles"):
            manual_lineup(pool, team, history, "in", incompatible.member_id, starter)
        with self.assertRaisesRegex(ValueError, "No compatible"):
            manual_lineup([p for p in pool if p.member_id in team], team, history, "out", starter)
        self.assertEqual(history[bench.member_id]["played"], 0)
        self.assertEqual(len(team), 6)

    def test_manual_starter_swap_checks_both_roles(self):
        pool = players(2)
        pool[0].roles = ("Tank", "DPS")
        pool[2].roles = ("DPS", "Tank")
        team = make_team(pool)
        history = {p.member_id: asdict(p) for p in pool}
        swapped = manual_lineup(pool, team, history, "swap", 1, 3)
        self.assertEqual(swapped[1], "DPS")
        self.assertEqual(swapped[3], "Tank")
        with self.assertRaisesRegex(ValueError, "each other"):
            manual_lineup(pool, team, history, "swap", 2, 4)

    def test_required_real_host_is_in_initial_demo_lineup(self):
        pool = players()
        host = Player(999, "Host", ("Support",), joined=100)
        pool.append(host)
        self.assertIn(999, make_team(pool, required_ids={999}))

    def test_role_aliases_and_flexible_assignment(self):
        self.assertEqual(normalized_roles(["Vanguard", "Tank", "Strategist"]), ("Tank", "Support"))
        pool = [
            Player(1, "flex", ("Tank", "DPS")),
            Player(2, "tank1", ("Tank",)),
            Player(3, "tank2", ("Tank",)),
            Player(4, "dps", ("DPS",)),
            Player(5, "support1", ("Support",)),
            Player(6, "support2", ("Support",)),
        ]
        team = make_team(pool)
        self.assertEqual(team[1], "DPS")
        self.assertEqual(Counter(team.values()), {"Tank": 2, "DPS": 2, "Support": 2})

    def test_insufficient_role_pool_rejected(self):
        with self.assertRaisesRegex(ValueError, "2 Support"):
            make_team([Player(i, str(i), ("Tank", "DPS")) for i in range(10)])

    def test_exact_substitutions_preserve_incumbent_roles(self):
        pool = players()
        team = make_team(pool)
        for p in pool:
            if p.member_id in team:
                p.played, p.last_played = 1, 1
        after = rotate_team(pool, team, 2)
        self.assertEqual(len(set(after) - set(team)), 2)
        for mid in set(team) & set(after):
            self.assertEqual(team[mid], after[mid])

    def test_rotation_fairness_over_many_matches(self):
        pool = players()
        team = make_team(pool)
        for number in range(1, 31):
            for p in pool:
                if p.member_id in team:
                    p.played += 1
                    p.last_played = number
            team = rotate_team(pool, team, 2)
        counts = [p.played for p in pool]
        self.assertGreater(min(counts), 0)
        # Only two of three role queues can rotate per round; a small transient
        # difference is expected, but repeated rounds must not starve anyone.
        self.assertLessEqual(max(counts) - min(counts), 2)

    def test_no_subs_keeps_team_and_partial_subs_use_correct_role(self):
        pool = players(2)
        team = make_team(pool)
        self.assertEqual(rotate_team(pool, team, 2), team)
        pool.append(Player(99, "bench support", ("Support",)))
        after = rotate_team(pool, team, 2)
        self.assertEqual(set(after) - set(team), {99})
        self.assertEqual(after[99], "Support")
        self.assertEqual(
            {mid for mid in team if team[mid] != "Support"}, {mid for mid in after if after[mid] != "Support"}
        )

    def test_disconnected_player_cannot_be_selected(self):
        pool = players(2)
        team = make_team(pool)
        with self.assertRaises(ValueError):
            rotate_team(pool[1:], team, 2)


class StoreTests(unittest.TestCase):
    def test_test_settings_survive_restart_and_can_be_disabled(self):
        self.store.arm_test(10, "Marvel Rivals", 55, [{"member_id": -123, "simulated": True}])
        restored = ScrimStore(self.store.connection)
        restored.initialize()
        self.assertTrue(restored.test_settings(10, "Marvel Rivals")["enabled"])
        self.assertEqual(restored.test_settings(10, "Marvel Rivals")["member_id"], 55)
        self.store.disable_test(10, "Marvel Rivals")
        self.assertFalse(restored.test_settings(10, "Marvel Rivals")["enabled"])

    def test_test_session_and_roster_are_marked_in_export(self):
        self.store.end(self.data, 1)
        pool = players(2)
        for p in pool:
            p.simulated = True
        data = self.store.create(10, "Marvel Rivals", self.config, pool, make_team(pool), 55, test_mode=True)
        self.store.start(data, 55)
        exported = self.store.export(data["id"])
        self.assertTrue(exported["session"]["test_mode"])
        self.assertEqual(exported["session"]["test_host"], 55)
        self.assertTrue(all(p["simulated"] for p in exported["matches"][0]["roster"]))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "rionnag.scrims.scrims.sqlite3"

        @contextmanager
        def connection():
            db = sqlite3.connect(self.path)
            try:
                with db:
                    yield db
            finally:
                db.close()

        self.store = ScrimStore(connection)
        self.store.initialize()
        self.pool = players()
        self.config = {"control_id": 11, "waiting_id": 12, "stage_id": 13}
        self.data = self.store.create(10, "Marvel Rivals", self.config, self.pool, make_team(self.pool), 1)

    def tearDown(self):
        self.directory.cleanup()

    def test_restart_restores_roster_and_completed_counts(self):
        self.store.start(self.data, 1)
        match_id = self.data["match_id"]
        self.store.finish(self.data, 1)
        restored = ScrimStore(self.store.connection)
        restored.initialize()
        data = restored.get(self.data["id"])
        self.assertEqual(data["number"], 2)
        self.assertEqual(data["status"], "prepared")
        self.assertTrue(all(data["players"][mid]["played"] == 1 for mid in data["roster"]))
        self.assertEqual(restored.match(match_id)["status"], "completed")
        self.assertEqual(restored.match(match_id)["roster"][0]["played"], 0)

    def test_duplicate_start_and_finish_do_not_count_twice(self):
        self.store.start(self.data, 1)
        with self.assertRaises(ValueError):
            self.store.start(self.data, 1)
        stale = self.store.get(self.data["id"])
        self.store.finish(self.data, 1)
        with self.assertRaises(ValueError):
            self.store.finish(stale, 1)
        self.assertTrue(
            all(self.store.get(self.data["id"])["players"][mid]["played"] == 1 for mid in self.data["roster"])
        )

    def test_abort_does_not_count_and_clears_active_stage(self):
        self.store.start(self.data, 1)
        self.store.finish(self.data, 1, aborted=True)
        self.store.end(self.data, 1)
        self.assertEqual(self.store.active(), [])
        self.assertTrue(all(p["played"] == 0 for p in self.data["players"].values()))
        self.assertEqual(self.store.export(self.data["id"])["matches"][0]["status"], "aborted")

    def test_active_stage_cannot_be_shared(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create(10, "Other", self.config, self.pool, make_team(self.pool), 1)
        self.assertEqual(len(self.store.active()), 1)

    def test_raw_snapshot_and_manual_notes_are_exported(self):
        self.store.start(self.data, 1)
        mid = self.data["match_id"]
        self.store.snapshot(mid, "live", {"unknown_source_field": 42}, "source1")
        self.store.snapshot(mid, "details", {"teams": [], "draft": [], "map_id": 7}, "source1")
        self.store.snapshot(mid, "manual", {"bans": "Loki", "recorded_by": 1})
        exported = self.store.export(self.data["id"])
        self.assertEqual(len(exported["snapshots"]), 3)
        self.assertEqual(exported["snapshots"][0]["data"]["unknown_source_field"], 42)
        self.assertEqual(exported["matches"][0]["data"]["map_id"], 7)
        self.assertEqual(self.store.used_ids(10), {"source1"})

    def test_same_source_cannot_be_reused_or_overwritten(self):
        self.store.start(self.data, 1)
        self.store.snapshot(self.data["match_id"], "live", {}, "source1")
        with self.assertRaisesRegex(ValueError, "different game"):
            self.store.snapshot(self.data["match_id"], "details", {}, "source2")
        self.store.finish(self.data, 1)
        self.store.start(self.data, 1)
        with self.assertRaisesRegex(ValueError, "already logged"):
            self.store.snapshot(self.data["match_id"], "live", {}, "source1")

    def test_finished_matches_continue_result_retries(self):
        self.store.start(self.data, 1)
        self.store.finish(self.data, 1)
        self.assertEqual(len(self.store.pending_matches()), 1)
        mid = self.store.pending_matches()[0]["id"]
        self.store.snapshot(mid, "details", {"teams": [{"players": []}]}, "s1")
        self.assertEqual(self.store.pending_matches(), [])


class RivalsTests(unittest.TestCase):
    def test_random_leaderboard_pool_contains_three_per_role_and_negative_ids(self):
        rows = [
            {"uid": i * 10 + j, "name": f"Source {i}-{j}", "heroes": [hero]}
            for i, hero in enumerate((1018, 1014, 1016), 1)
            for j in range(1, 5)
        ]
        pool = scrim_rivals.test_pool({"players": rows}, ("Source 1-1",), rng=random.Random(12))
        self.assertEqual(len(pool), 9)
        self.assertEqual(Counter(p["roles"][0] for p in pool), {"Tank": 3, "DPS": 3, "Support": 3})
        self.assertTrue(all(p["member_id"] < 0 and p["simulated"] for p in pool))
        self.assertNotIn("Source 1-1", [p["username"] for p in pool])
        self.assertEqual(len({p["uid"] for p in pool}), 9)

    def test_insufficient_leaderboard_roles_fail_without_guessing(self):
        with self.assertRaisesRegex(ValueError, "three identifiable"):
            scrim_rivals.test_pool({"players": [{"uid": 1, "name": "Unknown", "heroes": [999999]}]})

    def roster(self):
        return [{"uid": str(i), "username": f"p{i}"} for i in range(1, 7)]

    def live(self):
        return {"players": {str(i): {"uid": i, "team_id": 1 if i < 7 else 2} for i in range(1, 13)}}

    def test_full_team_verification_rejects_split_roster(self):
        self.assertTrue(scrim_rivals.same_team(self.live(), set("123456"), live=True))
        self.assertFalse(scrim_rivals.same_team(self.live(), set("123457"), live=True))
        self.assertFalse(scrim_rivals.same_team(self.live(), set("12345"), live=True))

    def test_live_probe_checks_fresh_status_and_all_six_players(self):
        player = SimpleNamespace(raw={"status": {"battle_id": "game1"}}, live_game=MagicMock())
        player.live_game.fetch.return_value.to_dict.return_value = self.live()
        with patch.object(scrim_rivals, "RivalsClient") as client:
            client.return_value.__enter__.return_value.get_player.return_value = player
            found = scrim_rivals.discover(self.roster(), 2, 100)
            self.assertEqual(found[:2], ("game1", "live"))
            client.return_value.__enter__.return_value.get_player.assert_called_once_with("3")
            self.assertIsNone(scrim_rivals.discover(self.roster(), 0, 100, used_ids={"game1"}))

    def test_completed_match_does_not_bind_next_live_game(self):
        player = SimpleNamespace(live_game=MagicMock(), matches=MagicMock())
        player.matches.fetch.return_value.to_dict.return_value = {"matches": []}
        with patch.object(scrim_rivals, "RivalsClient") as client:
            client.return_value.__enter__.return_value.get_player.return_value = player
            self.assertIsNone(scrim_rivals.discover(self.roster(), 0, 100, 200))
        player.live_game.fetch.assert_not_called()
        player.matches.fetch.assert_called_once_with(limit=10, mode="custom", cached=False)

    def test_live_response_without_source_id_cannot_bind_a_match(self):
        player = SimpleNamespace(raw={"status": None}, live_game=MagicMock())
        player.live_game.fetch.return_value.to_dict.return_value = self.live()
        with patch.object(scrim_rivals, "RivalsClient") as client:
            client.return_value.__enter__.return_value.get_player.return_value = player
            self.assertIsNone(scrim_rivals.discover(self.roster(), 0, 100))

    def test_history_links_only_matching_team_in_time_window(self):
        player = SimpleNamespace(matches=MagicMock())
        player.matches.fetch.return_value.to_dict.return_value = {
            "matches": [
                {"match_uid": "old", "timestamp": 1},
                {"match_uid": "used", "timestamp": 1000},
                {"match_uid": "found", "timestamp": 1100000},
            ]
        }
        payload = {"teams": [{"players": [{"uid": i} for i in range(1, 7)]}]}
        with patch.object(scrim_rivals, "RivalsClient") as client:
            rd = client.return_value.__enter__.return_value
            rd.get_player.return_value = player
            rd.matches.get.return_value.to_dict.return_value = payload
            # Seconds and milliseconds are tested separately; an out-of-range candidate is ignored.
            self.assertIsNone(scrim_rivals.discover(self.roster(), 0, 1000, 1200, {"used"}))
            player.matches.fetch.return_value.to_dict.return_value["matches"][-1]["timestamp"] = 1100
            self.assertEqual(
                scrim_rivals.discover(self.roster(), 0, 1000, 1200, {"used"})[:2], ("found", "details")
            )
