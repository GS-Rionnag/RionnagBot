import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from rionnag import service


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name)
        self.patcher = patch.object(service, "STATE", self.state)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_stale_or_missing_heartbeat_is_offline(self):
        self.assertEqual(service.status()["state"], "offline")
        service.write_json(self.state / "status.json", {
            "state": "running", "heartbeat": time.time() - 30,
        })
        self.assertEqual(service.status()["state"], "offline")

    def test_running_start_does_not_launch_duplicate(self):
        service.write_json(self.state / "status.json", {
            "state": "running", "heartbeat": time.time(),
        })
        with patch.object(service.subprocess, "Popen") as launch:
            service.start()
        launch.assert_not_called()

    def test_restart_requires_acknowledgement(self):
        service.write_json(self.state / "status.json", {
            "state": "running", "heartbeat": time.time(),
        })

        def acknowledge(_):
            request, = self.state.glob("request-*.json")
            self.assertEqual(json.loads(request.read_text())["action"], "restart")
            service.write_json(self.state / f"ack-{request.stem}.json", {})
            request.unlink()

        with patch.object(service.time, "sleep", side_effect=acknowledge):
            service.control("restart")
        self.assertFalse(list(self.state.glob("ack-*.json")))

    def test_stop_targets_only_owned_live_child_tree(self):
        child = Mock()
        child.pid = 12345
        child.poll.return_value = None
        with patch.object(service.subprocess, "run") as terminate:
            service.stop_child(child)
        self.assertEqual(terminate.call_args.args[0], [
            "taskkill.exe", "/PID", "12345", "/T", "/F",
        ])
        child.poll.return_value = 0
        with patch.object(service.subprocess, "run") as terminate:
            service.stop_child(child)
        terminate.assert_not_called()

    def test_configured_collector_uses_separate_environment_and_no_token_arguments(self):
        with patch.object(service, "ROOT", self.state):
            collector = self.state / "scrim_collector"
            collector.mkdir()
            self.assertEqual(set(service.managed_commands()), {"bot"})
            (collector / ".env").write_text("DISCORD_USER_TOKEN=example\nSCRIM_SOURCE_CHANNEL_IDS=123\n")
            commands = service.managed_commands()
        args, cwd = commands["collector"]
        self.assertEqual(cwd, collector)
        self.assertEqual(args, [str(collector / ".venv/Scripts/python.exe"), str(collector / "main.py")])
        self.assertNotIn("example", " ".join(args))

    def run_supervisor(self, launches, on_sleep):
        commands = {name: ([name], self.state) for name in ("bot", "collector")}
        with (
            patch.object(service, "ROOT", self.state),
            patch.object(service, "managed_commands", return_value=commands),
            patch.object(service, "SingleInstance", return_value=MagicMock()),
            patch.object(service.subprocess, "Popen", side_effect=launches) as launch,
            patch.object(service, "stop_child") as stop,
            patch.object(service.time, "sleep", side_effect=on_sleep),
        ):
            service.supervise()
        return launch, stop

    def child(self, pid):
        child = Mock(pid=pid, returncode=1)
        child.poll.return_value = None
        return child

    def request(self, action):
        service.write_json(self.state / "request-test.json", {"action": action})

    def test_supervisor_starts_and_stops_both_owned_children(self):
        bot, collector = self.child(1), self.child(2)

        def sleep(_):
            running = service.status()
            self.assertEqual(running["bot_pid"], 1)
            self.assertEqual(running["collector_pid"], 2)
            self.assertEqual(running["collector_state"], "running")
            self.request("stop")

        launch, stop = self.run_supervisor([bot, collector], sleep)
        self.assertEqual([call.args[0] for call in launch.call_args_list], [["bot"], ["collector"]])
        self.assertEqual([call.args[0] for call in stop.call_args_list if call.args[0]], [bot, collector])
        self.assertEqual(service.status()["state"], "stopped")

    def test_collector_crash_restarts_after_delay_without_restarting_bot(self):
        bot, collector, replacement = self.child(1), self.child(2), self.child(3)
        clock, statuses = [0], []

        def sleep(_):
            statuses.append(service.status())
            if len(statuses) == 1:
                collector.poll.return_value = 1
            elif len(statuses) == 2:
                clock[0] += 16
            else:
                self.request("stop")

        with patch.object(service.time, "monotonic", side_effect=lambda: clock[0]):
            launch, stop = self.run_supervisor([bot, collector, replacement], sleep)
        self.assertEqual([state["collector_state"] for state in statuses], ["running", "retrying", "running"])
        self.assertTrue(all(state["bot_pid"] == 1 for state in statuses))
        self.assertEqual(statuses[-1]["collector_pid"], 3)
        self.assertEqual(statuses[-1]["collector_last_exit"], 1)
        self.assertEqual(launch.call_count, 3)
        self.assertNotIn(collector, [call.args[0] for call in stop.call_args_list])

    def test_shared_restart_replaces_both_children_and_acknowledges(self):
        children = [self.child(pid) for pid in range(1, 5)]
        states = []

        def sleep(_):
            states.append(service.status())
            self.request("restart" if len(states) == 1 else "stop")

        launch, stop = self.run_supervisor(children, sleep)
        self.assertEqual([(state["bot_pid"], state["collector_pid"]) for state in states], [(1, 2), (3, 4)])
        self.assertEqual([call.args[0] for call in stop.call_args_list if call.args[0]], children)
        self.assertEqual(launch.call_count, 4)
        self.assertTrue((self.state / "ack-request-test.json").exists())

    def test_missing_collector_runtime_retries_without_bringing_down_bot(self):
        bot = self.child(1)

        def sleep(_):
            running = service.status()
            self.assertEqual(running["state"], "running")
            self.assertEqual(running["collector_state"], "retrying")
            self.assertIsNone(running["collector_pid"])
            self.request("stop")

        self.run_supervisor([bot, FileNotFoundError("example")], sleep)
