import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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
