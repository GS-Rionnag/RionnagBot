import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import main


class ExtractionTests(unittest.TestCase):
    def test_four_model_fields_and_trusted_metadata(self):
        fields = {
            "rank_minimum": "Diamond",
            "rank_maximum": "Grandmaster",
            "Start_Time_timestamp": "2027-01-15T08:00:00Z",
            "End_Time_timestamp": None,
        }

        def run(args, **kwargs):
            self.assertIn("gpt-5.5", args)
            self.assertIn('model_reasoning_effort="medium"', args)
            schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
            self.assertEqual(set(schema["properties"]["scrims"]["items"]["properties"]), set(fields))
            output = Path(args[args.index("--output-last-message") + 1])
            output.write_text(json.dumps({"scrims": [fields]}), encoding="utf-8")
            return subprocess.CompletedProcess(args, 0)

        with (
            patch.dict(main.os.environ, {"CODEX_MODEL": "gpt-5.5", "CODEX_REASONING_EFFORT": "medium"}),
            patch.object(main.shutil, "which", return_value="codex.exe"),
            patch.object(main.subprocess, "run", side_effect=run),
        ):
            result = main.extract([{"id": "123", "content": "GM to Dia <t:1800000000:F>"}])["scrims"]
        self.assertEqual(result[0], {**fields, "source_message_id": "123"})


class CollectorEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_own_advert_is_removed_from_feed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = main.FeedStore(Path(directory) / "feed.db")
            collector = main.Collector(store, {2})
            message = SimpleNamespace(id=3, channel=SimpleNamespace(id=2), guild=SimpleNamespace(id=1),
                                      author=SimpleNamespace(id=4), created_at=datetime.now(UTC),
                                      content="LFS Diamond-GM <t:1800000000:F>", embeds=[],
                                      jump_url="https://discord.com/channels/1/2/3")
            store.put(main.snapshot(message))
            with patch.object(type(collector), "user", new_callable=PropertyMock,
                              return_value=SimpleNamespace(id=4)):
                await collector.on_message(message)
            self.assertEqual(store.pending(), [])
            await collector.close()

    async def test_new_message_is_cached_and_wakes_extraction_only_in_watched_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            store = main.FeedStore(Path(directory) / "feed.db")
            collector = main.Collector(store, {2})
            message = SimpleNamespace(id=3, channel=SimpleNamespace(id=2), guild=SimpleNamespace(id=1),
                                      author=SimpleNamespace(id=4), created_at=datetime.now(UTC),
                                      content="LFS Diamond-GM today 7pm EST", embeds=[],
                                      jump_url="https://discord.com/channels/1/2/3")
            await collector.on_message(message)
            self.assertTrue(collector.inbox_ready.is_set())
            self.assertEqual(store.pending()[0][2]["content"], message.content)
            collector.inbox_ready.clear()
            message.channel.id = 5
            message.id = 6
            await collector.on_message(message)
            self.assertFalse(collector.inbox_ready.is_set())
            self.assertEqual(len(store.pending()), 1)
            await collector.close()


if __name__ == "__main__":
    unittest.main()
