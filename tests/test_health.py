import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from rionnag.cogs.health import Health
from rionnag.services.health import recent_errors


class HealthTests(unittest.TestCase):
    def test_window_privacy_and_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bot.log"
            path.write_text(
                "2026-10-04 12:00:00,000 ERROR old: private answer\n"
                "2026-10-06 11:00:00,000 WARNING warning: secret\n"
                + "".join(
                    f"2026-10-06 11:00:0{i},000 ERROR source{i}: private answer\n"
                    "Traceback private token\n"
                    for i in range(7)
                ), encoding="utf-8",
            )
            result = recent_errors(path, datetime(2026, 10, 6, 12))
            self.assertIn("7 ERROR/CRITICAL", result)
            self.assertNotIn("source0", result)
            self.assertIn("source6", result)
            for private in ("private", "token", "old", "warning"):
                self.assertNotIn(private, result)

    def test_empty_and_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bot.log"
            self.assertIn("unavailable", recent_errors(path))
            path.write_text("", encoding="utf-8")
            self.assertIn("No ERROR/CRITICAL", recent_errors(path))


class HealthCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_response(self):
        cog = Health(SimpleNamespace(latency=0.123))
        interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        with patch("rionnag.cogs.health.recent_errors", return_value="No recent errors"):
            await cog.ping.callback(cog, interaction)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        args, kwargs = interaction.followup.send.call_args
        self.assertIn("123 ms", args[0])
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(cog.ping.name, "ping")
