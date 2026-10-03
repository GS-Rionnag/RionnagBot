import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import test_scrim_queue
from rivals_api import RivalsClient

from rionnag.scrims import scrim_rivals
from rionnag.scrims.scrim_monitor import ScrimMonitor


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_scrim_queue.QueueTests.setUp
    tearDown = test_scrim_queue.QueueTests.tearDown
    click = test_scrim_queue.QueueTests.click
    begin = test_scrim_queue.QueueTests.begin
    interaction = test_scrim_queue.QueueTests.interaction

    async def live(self, monitor, result):
        data = self.store.active()[0]
        with patch.object(
            scrim_rivals,
            "check_live",
            side_effect=lambda p, _: {
                "uid": str(p["member_id"]),
                "custom": result,
                "battle_id": "custom-1" if result is True else None,
                "payload": {"status": result},
            },
        ):
            await monitor.probe("live", data)

    async def test_any_one_starter_starts_and_two_full_idle_sweeps_finish_once(self):
        data = await self.begin(start=False)
        monitor = ScrimMonitor(self.controller)
        await self.live(monitor, False)
        self.assertEqual(self.store.get(data["id"])["status"], "prepared")
        await self.live(monitor, True)
        playing = self.store.get(data["id"])
        self.assertEqual(playing["status"], "playing")
        self.assertEqual(len(self.stage.members), 9)
        self.assertIn("Game Started", playing["note"])
        self.assertEqual(len(self.store.match(playing["match_id"])["roster"]), 6)
        for _ in range(11):
            await self.live(monitor, False)
        self.assertEqual(self.store.get(data["id"])["status"], "playing")
        await self.live(monitor, False)
        finished = self.store.get(data["id"])
        self.assertEqual(finished["status"], "prepared")
        self.assertEqual(sum(p["played"] for p in finished["players"].values()), 6)
        self.assertEqual(len(self.waiting.members), 9)
        self.assertIn("Game Ended", finished["note"])
        self.assertIsNone(self.store.export(data["id"])["matches"][0]["external_id"])
        await self.live(monitor, True)  # stale original battle ID cannot start round two
        self.assertEqual(len(self.store.export(data["id"])["matches"]), 1)

    async def test_one_disconnect_unknown_and_request_failure_do_not_end_game(self):
        data = await self.begin(start=False)
        monitor = ScrimMonitor(self.controller)
        await self.live(monitor, True)
        origin = list(data["roster"])[0]
        for _ in range(12):
            current = self.store.get(data["id"])
            with patch.object(
                scrim_rivals,
                "check_live",
                side_effect=lambda p, _: {
                    "uid": str(p["member_id"]),
                    "custom": p["member_id"] != origin,
                    "battle_id": None if p["member_id"] == origin else "custom-1",
                    "payload": {},
                },
            ):
                await monitor.probe("live", current)
        await self.live(monitor, None)
        with patch.object(scrim_rivals, "check_live", side_effect=TimeoutError):
            await monitor.probe("live", self.store.get(data["id"]))
        current = self.store.get(data["id"])
        self.assertEqual(current["status"], "playing")
        self.assertGreater(current["detection"]["next_at"], time.time() + 50)
        self.assertEqual(sum(p["played"] for p in current["players"].values()), 0)

    async def test_restart_retains_confirmation_and_does_not_duplicate_finish(self):
        data = await self.begin(start=False)
        monitor = ScrimMonitor(self.controller)
        await self.live(monitor, True)
        for _ in range(11):
            await self.live(monitor, False)
        restarted = ScrimMonitor(self.controller)
        await self.live(restarted, False)
        await self.live(restarted, False)
        self.assertEqual(self.store.get(data["id"])["number"], 2)
        self.assertEqual(len(self.store.export(data["id"])["matches"]), 1)

    async def test_history_retries_then_stores_full_verified_payload_once(self):
        data = await self.begin()
        await self.controller.game_ended(self.guild, data)
        monitor = ScrimMonitor(self.controller)
        match = self.store.pending_matches()[0]
        with patch.object(scrim_rivals, "recent_result", return_value=None):
            await monitor.probe("history", self.store.get(data["id"]), match)
        self.assertIsNone(self.store.match(match["id"])["external_id"])
        payload = {"teams": [{"players": [{"uid": i} for i in range(1, 7)]}], "map_id": 7}
        with patch.object(scrim_rivals, "recent_result", return_value=("verified", payload)):
            await monitor.probe("history", self.store.get(data["id"]), match)
        self.assertEqual(self.store.match(match["id"])["data"], payload)
        self.assertEqual(self.store.pending_matches(), [])
        self.assertEqual(sum(p["played"] for p in self.store.get(data["id"])["players"].values()), 6)

    async def test_changed_lineup_or_ended_session_discards_in_flight_response(self):
        data = await self.begin(start=False)
        monitor = ScrimMonitor(self.controller)

        async def edited(*_):
            current = self.store.get(data["id"])
            current["revision"] += 1
            self.store.save(current)
            return {"uid": "1", "custom": True, "battle_id": "1", "payload": {}}

        with patch("rionnag.scrims.scrim_monitor.asyncio.to_thread", side_effect=edited):
            await monitor.probe("live", data)
        self.assertEqual(self.store.export(data["id"])["matches"], [])
        current = self.store.get(data["id"])
        self.store.end(current, None)
        with patch.object(
            scrim_rivals,
            "check_live",
            return_value={
                "uid": "1",
                "custom": True,
                "battle_id": "1",
                "payload": {},
            },
        ):
            await monitor.probe("live", data)
        self.assertEqual(self.store.export(data["id"])["matches"], [])

    async def test_scheduler_respects_persisted_backoff_and_ignores_simulated_rosters(self):
        data = await self.begin(start=False)
        data["detection"] = {"next_at": time.time() + 1000}
        self.store.save(data)
        monitor = ScrimMonitor(self.controller)
        with patch.object(scrim_rivals, "check_live") as live:
            await monitor.tick()
            live.assert_not_called()
            data["test_mode"] = True
            data["detection"] = {}
            for mid in data["roster"]:
                data["players"][mid]["simulated"] = True
            self.store.save(data)
            await monitor.tick()
            live.assert_not_called()

    async def test_test_session_detects_start_and_end_by_real_starter_without_probing_demo_accounts(self):
        self.waiting.members = [self.members[1]]
        self.controller.queue(self.guild, self.lobby)
        self.store.save_lobby(self.lobby)
        pool = test_scrim_queue.QueueTests.example_pool(self)
        with patch.object(scrim_rivals, "fetch_test_pool", return_value=pool):
            await self.click("test", user=1)
        data = self.store.active()[0]
        monitor = ScrimMonitor(self.controller)
        self.assertTrue(monitor.supported(data))
        with patch.object(
            scrim_rivals,
            "check_live",
            return_value={
                "uid": "1",
                "custom": True,
                "battle_id": "custom-test",
                "payload": {},
                "started_at": time.time() - 600,
            },
        ) as probe:
            await monitor.tick()
        self.assertEqual(probe.call_args.args[0]["member_id"], 1)
        current = self.store.get(data["id"])
        self.assertEqual(current["status"], "playing")
        self.assertLess(self.store.match(current["match_id"])["started_at"], time.time() - 590)
        # The actual post-game Rivals Data response has state 5 and no battle_id.
        with (
            RivalsClient(enrich=False) as client,
            patch.object(scrim_rivals, "RivalsClient", return_value=client),
            patch.object(client, "_post_json", return_value={"uid": 1, "status": {"state": 5}}),
        ):
            await monitor.probe("live", self.store.get(data["id"]))
            self.assertEqual(self.store.get(data["id"])["status"], "playing")
            await monitor.probe("live", self.store.get(data["id"]))
        self.assertEqual(self.store.get(data["id"])["status"], "prepared")
        self.assertIn("Game Ended", self.store.get(data["id"])["note"])
        self.assertEqual(len(self.store.export(data["id"])["matches"]), 1)
        self.assertEqual(self.members[1].voice.channel, self.waiting)

    async def test_removed_button_actions_are_rejected_even_from_old_panels(self):
        data = await self.begin(start=False)
        for action in ("start", "finish"):
            interaction = self.interaction()
            await self.controller.action(
                interaction,
                self.lobby["id"],
                data["id"],
                data["revision"],
                action,
            )
            self.assertIn("automatically", interaction.followup.send.call_args.args[0])
        self.assertEqual(self.store.export(data["id"])["matches"], [])

    async def test_monitor_start_is_idempotent(self):
        self.controller.bot.is_closed = lambda: False
        monitor = ScrimMonitor(self.controller)
        with patch.object(monitor, "tick", side_effect=asyncio.CancelledError):
            monitor.start()
            first = monitor.task
            monitor.start()
            self.assertIs(monitor.task, first)
            await asyncio.gather(first, return_exceptions=True)


class AdapterTests(unittest.TestCase):
    def test_verified_result_keeps_match_values_without_provider_provenance(self):
        history = {"matches": [{"match_uid": "raw", "timestamp": 1600, "game_mode_id": 3}]}
        detail = {
            "match_uid": "raw",
            "timestamp": 1600,
            "replay_id": "987654321",
            "unrecognized": {"nested": [1, 2, 3]},
            "teams": [{"players": [{"player_uid": "1", "enemy_future_stat": 123}]}],
        }

        def response(data):
            body = json.dumps(data)
            return SimpleNamespace(
                status_code=200,
                text=body,
                ok=True,
                headers={},
                json=lambda: json.loads(body),
            )

        with (
            RivalsClient(enrich=True) as client,
            patch.object(scrim_rivals, "RivalsClient", return_value=client),
            patch.object(client, "_match_detail_cache", {}),
            patch.object(client, "_optional_provider", return_value=None),
            patch.object(client.providers.rt, "request", return_value=detail),
            patch.object(client.providers.tracker, "request", return_value={}),
            patch.object(client.session, "post", side_effect=[response(history), response(detail)]),
            patch.object(scrim_rivals.time, "sleep"),
        ):
            result = scrim_rivals.recent_result(
                {
                    "roster": [{"uid": "1", "username": "real"}],
                    "test_mode": True,
                    "started_at": 1000,
                    "ended_at": 1600,
                },
                0,
                set(),
                scrim_rivals.RequestBudget(),
            )
        self.assertNotIn("_scrim_source_data", result[1])
        self.assertNotIn("provider_metadata", json.dumps(result[1]))
        self.assertEqual(result[1]["unrecognized"], detail["unrecognized"])
        self.assertEqual(result[1]["replay_id"], "987654321")

    def test_fresh_custom_status_starts_without_requiring_live_roster(self):
        with (
            RivalsClient(enrich=False) as client,
            patch.object(scrim_rivals, "RivalsClient", return_value=client),
            patch.object(
                client,
                "_post_json",
                return_value={
                    "uid": 1,
                    "status": {"status": "In Custom Game", "battle_id": "custom"},
                },
            ) as request,
        ):
            result = scrim_rivals.check_live({"uid": "1", "username": "p1"}, scrim_rivals.RequestBudget())
        self.assertTrue(result["custom"])
        request.assert_called_once_with("/player", {"uid": 1})

    def test_real_custom_room_status_uses_game_play_mode_300_without_live_endpoint(self):
        started_at = int(time.time() - 120)
        for status in (
            {"game_play_mode_id": 300, "state": 6, "battle_id": "opaque-custom-id", "start_time": started_at},
            "In game (Custom Room)-   ",
        ):
            with (
                self.subTest(status=status),
                RivalsClient(enrich=False) as client,
                patch.object(scrim_rivals, "RivalsClient", return_value=client),
                patch.object(client, "_post_json", return_value={"uid": 1, "status": status}) as request,
            ):
                result = scrim_rivals.check_live({"uid": "1", "username": "p1"}, scrim_rivals.RequestBudget())
            self.assertTrue(result["custom"])
            request.assert_called_once_with("/player", {"uid": 1})
            if isinstance(status, dict):
                self.assertEqual(result["started_at"], started_at)

    def test_explicit_idle_statuses_include_post_game_state_but_missing_status_remains_unknown(self):
        for payload, expected in (
            ({"uid": 1, "status": None}, False),
            ({"uid": 1, "status": {"state": 4, "team_member_count": 1}}, False),
            ({"uid": 1, "status": {"state": 4, "game_play_mode_id": 300}}, False),
            ({"uid": 1, "status": {"state": 5}}, False),
            ({"uid": 1, "status": {"state": 6}}, None),
            ({"uid": 1}, None),
        ):
            with (
                self.subTest(payload=payload),
                RivalsClient(enrich=False) as client,
                patch.object(scrim_rivals, "RivalsClient", return_value=client),
                patch.object(client, "_post_json", return_value=payload),
            ):
                result = scrim_rivals.check_live({"uid": "1", "username": "p1"}, scrim_rivals.RequestBudget())
            self.assertIs(result["custom"], expected)

    def test_mode_evidence_requires_custom_and_missing_status_is_unknown(self):
        self.assertIsNone(scrim_rivals.custom_game({"players": []}))
        self.assertFalse(scrim_rivals.custom_game({"game_mode_id": 2}))
        self.assertTrue(scrim_rivals.custom_game({"game_mode_id": 3}))
        self.assertIsNone(scrim_rivals.custom_game({"status": "Custom Lobby"}))
        self.assertIsNone(scrim_rivals.custom_game({"status": 3}))
        self.assertIsNone(scrim_rivals.custom_game({"status": "Not in a custom game"}))
        with (
            RivalsClient(enrich=False) as client,
            patch.object(scrim_rivals, "RivalsClient", return_value=client),
            patch.object(client, "_post_json", return_value={"uid": 1}),
        ):
            result = scrim_rivals.check_live({"uid": "1", "username": "p1"}, scrim_rivals.RequestBudget())
        self.assertIsNone(result["custom"])

    def test_requests_share_spacing_and_rate_limit_prevents_next_request(self):
        budget = scrim_rivals.RequestBudget()
        ok = MagicMock(return_value=SimpleNamespace(status_code=200))
        limited = MagicMock(return_value=SimpleNamespace(status_code=429, headers={"Retry-After": "120"}))
        with (
            patch.object(scrim_rivals.time, "monotonic", return_value=100),
            patch.object(scrim_rivals.time, "sleep") as sleep,
        ):
            budget.wrap(ok)()
            budget.wrap(ok)()
            self.assertEqual(sleep.call_args.args, (5,))
            with self.assertRaises(scrim_rivals.MonitorRateLimit) as caught:
                budget.wrap(limited)()
            self.assertEqual(caught.exception.delay, 120)
            with self.assertRaises(scrim_rivals.MonitorRateLimit):
                budget.wrap(ok)()
            self.assertEqual(ok.call_count, 2)

    def test_recent_result_uses_real_sdk_uncached_custom_history_and_details(self):
        roster = [{"uid": str(i), "username": f"p{i}"} for i in range(1, 7)]
        payload = {
            "match_uid": "custom-1",
            "game_mode_id": 3,
            "timestamp": 1000,
            "teams": [{"players": [{"uid": i} for i in range(1, 7)]}],
        }
        history = {
            "matches": [
                {"match_uid": "custom-1", "game_mode_id": 3, "timestamp": 1000, "duration_seconds": 600},
                {"match_uid": "older", "game_mode_id": 3, "timestamp": 100},
            ]
        }
        with (
            RivalsClient(enrich=True) as client,
            patch.object(scrim_rivals, "RivalsClient", return_value=client),
            patch.object(client, "_match_detail_cache", {}),
            patch.object(client, "_optional_provider", return_value=None),
            patch.object(client.providers.rt, "request", return_value=payload),
            patch.object(client.providers.tracker, "request", return_value={}),
            patch.object(client, "_post_json", side_effect=[history, payload]) as request,
        ):
            result = scrim_rivals.recent_result(
                {"roster": roster, "started_at": 1000, "ended_at": 1600},
                2,
                set(),
                scrim_rivals.RequestBudget(),
                120,
            )
            self.assertEqual(result[0], "custom-1")
            self.assertEqual(
                request.call_args_list[0].args,
                ("/player/matches", {"uid": 3, "cursor": None, "mode": 3}),
            )
            self.assertEqual(request.call_args_list[1].args, ("/match", {"match_id": "custom-1"}))

    def test_test_history_requires_real_starters_and_never_resolves_simulated_accounts(self):
        payload = {
            "match_uid": "custom-1",
            "game_mode_id": 3,
            "timestamp": 1000,
            "teams": [{"players": [{"uid": 1}, {"uid": 999}]}],
        }
        history = {
            "matches": [
                {"match_uid": "custom-1", "game_mode_id": 3, "timestamp": 1000, "duration_seconds": 600},
            ]
        }
        roster = [
            {"uid": "1", "username": "real", "simulated": False},
            {"uid": None, "username": "virtual", "simulated": True},
        ]
        with (
            RivalsClient(enrich=True) as client,
            patch.object(scrim_rivals, "RivalsClient", return_value=client),
            patch.object(client, "_match_detail_cache", {}),
            patch.object(client, "_optional_provider", return_value=None),
            patch.object(client.providers.rt, "request", return_value=payload),
            patch.object(client.providers.tracker, "request", return_value={}),
            patch.object(client, "get_player", side_effect=AssertionError("Do not resolve demo accounts")),
            patch.object(client, "_post_json", side_effect=[history, payload]) as request,
        ):
            result = scrim_rivals.recent_result(
                {"roster": roster, "started_at": 1000, "ended_at": 1600, "test_mode": True},
                5,
                set(),
                scrim_rivals.RequestBudget(),
                120,
            )
            self.assertEqual(result[0], "custom-1")
            self.assertEqual(request.call_args_list[0].args[1]["uid"], 1)
        self.assertFalse(scrim_rivals.same_team(payload, {"1"}))

    def result(self, candidate, *, used=(), payload=None, battle_id=None):
        match = {
            "roster": [{"uid": str(i), "username": f"p{i}"} for i in range(1, 7)],
            "started_at": 1000,
            "ended_at": 1600,
            "data": {"detection": {"battle_id": battle_id}},
        }
        payload = (
            payload
            if payload is not None
            else {
                "match_uid": "found",
                "game_mode_id": 3,
                "teams": [{"players": [{"uid": i} for i in range(1, 7)]}],
            }
        )
        with (
            patch.object(scrim_rivals, "monitoring_client") as context,
            patch("rivals_api.resources.PlayerMatches") as resource,
        ):
            client = context.return_value.__enter__.return_value
            resource.return_value.fetch.return_value.to_dict.return_value = {"matches": [candidate]}
            client.matches.get.return_value.to_dict.return_value = payload
            result = scrim_rivals.recent_result(match, 2, used, scrim_rivals.RequestBudget(), 120)
            resource.assert_called_once_with(client, 3)
            resource.return_value.fetch.assert_called_once_with(limit=1, mode="custom", cached=False)
            client.get_player.assert_not_called()
            return result

    def test_latest_custom_match_verifies_time_all_six_and_complete_payload(self):
        candidate = {"match_uid": "found", "timestamp": 1000, "duration_seconds": 600, "game_mode_id": 3}
        self.assertEqual(self.result(candidate)[0], "found")
        self.assertIsNone(self.result({**candidate, "timestamp": 1}))
        self.assertIsNone(self.result({**candidate, "timestamp": 2000}))
        self.assertIsNone(self.result({**candidate, "duration_seconds": 10}))
        self.assertIsNone(self.result(candidate, used={"found"}))
        self.assertIsNone(self.result(candidate, battle_id="different"))
        self.assertIsNone(
            self.result(
                candidate,
                payload={
                    "teams": [
                        {"players": [{"uid": i} for i in range(1, 6)]},
                        {"players": [{"uid": 6}]},
                    ]
                },
            )
        )
        self.assertIsNone(self.result({**candidate, "game_mode_id": 2}))

    def test_timestamp_supports_seconds_milliseconds_iso_and_rejects_invalid(self):
        self.assertEqual(scrim_rivals.match_timestamp(1_800_000_000_000), 1_800_000_000)
        self.assertEqual(scrim_rivals.match_timestamp("1970-01-01T00:16:40Z"), 1000)
        self.assertIsNone(scrim_rivals.match_timestamp("invalid"))
        self.assertIsNone(scrim_rivals.match_timestamp(float("nan")))

    def test_actual_battle_and_history_ids_allow_delayed_end_but_require_roster(self):
        live_id = "1790969327:opaque-custom-battle"
        history_id = "5513448_1790969327_1288041_11001_12"
        history = {
            "matches": [
                {
                    "match_uid": history_id,
                    "timestamp": 1790970127,
                    "game_mode_id": 3,
                    "duration_seconds": 586,
                }
            ]
        }
        payload = {
            "match_uid": history_id,
            "timestamp": 1790970127,
            "game_mode_id": 3,
            "teams": [{"players": [{"player_uid": "1970288503"}]}],
        }
        match = {
            "roster": [{"uid": "1970288503", "username": "GS-"}],
            "started_at": 1790969328,
            "ended_at": 1790970824,
            "data": {"detection": {"battle_id": live_id, "test_mode": True}},
        }
        with (
            RivalsClient(enrich=True) as client,
            patch.object(scrim_rivals, "RivalsClient", return_value=client),
            patch.object(client, "_match_detail_cache", {}),
            patch.object(client, "_optional_provider", return_value=None),
            patch.object(client.providers.rt, "request", return_value=payload),
            patch.object(client.providers.tracker, "request", return_value={}),
            patch.object(client, "_post_json", side_effect=[history, payload]) as request,
        ):
            result = scrim_rivals.recent_result(match, 0, set(), scrim_rivals.RequestBudget(), 120)
        self.assertEqual(result[0], history_id)
        self.assertEqual(result[1]["match_uid"], history_id)
        self.assertEqual(result[1]["timestamp"], payload["timestamp"])
        self.assertEqual(result[1]["game_mode"]["name"], "Custom")
        self.assertEqual(result[1]["teams"][0]["players"][0]["player_uid"], "1970288503")
        self.assertNotIn("provider_metadata", result[1])
        self.assertEqual(request.call_args_list[0].args[1]["uid"], 1970288503)
        self.assertTrue(scrim_rivals.same_battle(live_id, history_id, match["started_at"], 120))
        self.assertFalse(
            scrim_rivals.same_battle(
                live_id,
                history_id.replace("9327", "9326"),
                match["started_at"],
                120,
            )
        )
        self.assertFalse(scrim_rivals.same_battle(live_id, history_id, match["started_at"] + 1000, 120))
        self.assertFalse(scrim_rivals.same_team(payload, {"999"}, expected_size=1))
