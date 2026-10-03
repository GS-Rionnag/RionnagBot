import tempfile
import unittest
from pathlib import Path

from rionnag.scrims.scrim_feed import FeedStore


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = FeedStore(Path(self.directory.name) / "feed.sqlite3")
        self.message = {"id": "123", "content": "GM scrim 8pm"}
        self.store.put(self.message)

    def result(self):
        return {"scrims": [{"source_message_id": "123", "rank_min": "GM"}]}

    def test_restart_and_retry_preserve_pending_messages(self):
        restarted = FeedStore(self.store.path)
        self.assertEqual(restarted.pending()[0][2], self.message)
        self.store.complete(self.store.pending(), self.result())
        self.assertEqual(restarted.pending(), [])
        self.assertEqual(restarted.offers()["scrims"][0]["messageContent"], self.message["content"])
        self.store.put(self.message)
        self.assertEqual(restarted.pending(), [])

    def test_edit_during_extraction_does_not_commit_stale_output(self):
        batch = self.store.pending()
        self.store.put({**self.message, "content": "cancelled"})
        self.store.complete(batch, self.result())
        self.assertEqual(self.store.offers(), {"scrims": []})
        self.assertEqual(len(self.store.pending()), 1)
        self.store.complete(self.store.pending(), {"scrims": []})
        self.assertEqual(self.store.pending(), [])

    def test_delete_during_extraction_cannot_resurrect_offer(self):
        batch = self.store.pending()
        self.store.delete("123")
        self.store.complete(batch, self.result())
        self.assertEqual(self.store.offers(), {"scrims": []})

    def test_multiple_slots_replace_previous_extraction(self):
        self.store.complete(self.store.pending(), {"scrims": self.result()["scrims"] * 2})
        self.assertEqual(len(self.store.offers()["scrims"]), 2)
        self.store.put({**self.message, "content": "filled"})
        self.store.complete(self.store.pending(), {"scrims": []})
        self.assertEqual(self.store.offers(), {"scrims": []})

    def test_unknown_source_rejects_entire_batch(self):
        with self.assertRaises(ValueError):
            self.store.complete(self.store.pending(), {"scrims": [{"source_message_id": "foreign"}]})
        self.assertEqual(len(self.store.pending()), 1)

    def test_history_checkpoint_survives_restart_and_never_moves_backwards(self):
        self.assertIsNone(self.store.history_cursor(42))
        self.store.cache_history({**self.message, "channel_id": "42"})
        self.store.complete(self.store.pending(), {"scrims": []})
        restarted = FeedStore(self.store.path)
        self.assertEqual(restarted.history_cursor(42), 123)
        restarted.cache_history({**self.message, "channel_id": "42"})
        self.assertEqual(restarted.pending(), [])
        restarted.cache_history({"id": "100", "channel_id": "42", "content": "older"})
        self.assertEqual(restarted.history_cursor(42), 123)
        restarted.cache_history({"id": "200", "channel_id": "42", "content": "missed"})
        self.assertEqual(FeedStore(self.store.path).history_cursor(42), 200)

    def test_stricter_extraction_requeues_old_offers_only_once(self):
        self.store.complete(self.store.pending(), self.result())
        self.assertEqual(self.store.upgrade_extraction("strict"), 1)
        self.assertEqual(self.store.offers(), {"scrims": []})
        self.assertEqual(len(self.store.pending()), 1)
        self.store.complete(self.store.pending(), self.result())
        self.assertEqual(self.store.upgrade_extraction("strict"), 0)
        self.assertEqual(len(self.store.offers()["scrims"]), 1)

    def test_expiry_deletes_offer_but_keeps_processed_cache(self):
        from rionnag.scrims.scrim_offer_rules import timestamp

        result = {"scrims": [{"source_message_id": "123", "starts_at": "2026-10-03T18:00:00-04:00"}]}
        self.store.complete(self.store.pending(), result)
        self.assertEqual(self.store.prune_expired(timestamp("2026-10-03T23:59:00-04:00")), 0)
        self.assertEqual(self.store.prune_expired(timestamp("2026-10-04T00:00:00-04:00")), 1)
        self.assertEqual(self.store.pending(), [])
        self.store.put(self.message)
        self.assertEqual(self.store.pending(), [])

    def test_old_backlog_is_retired_without_sending_to_model(self):
        self.store.put({"id": "1", "content": "old", "created_at": "2024-12-01T00:00:00Z"})
        self.store.put({"id": "2", "content": "new", "created_at": "2026-10-03T12:00:00Z"})
        from rionnag.scrims.scrim_offer_rules import timestamp

        self.store.retire_old_messages(timestamp("2026-10-02T12:00:00Z"))
        self.assertEqual([mid for mid, _, _ in self.store.pending()], ["2"])

    def test_export_is_exactly_seven_fields_and_metadata_is_trusted(self):
        message = {**self.message, "author_id": "42", "url": "https://discord.com/channels/1/2/123"}
        self.store.put(message)
        offer = {
            "source_message_id": "123",
            "rank_minimum": "Grandmaster",
            "rank_maximum": "Grandmaster",
            "Start_Time_timestamp": "2027-01-15T08:00:00Z",
            "End_Time_timestamp": None,
            "authorID": "spoof",
            "messageContent": "spoof",
        }
        self.store.complete(self.store.pending(), {"scrims": [offer]})
        result = self.store.offers()["scrims"][0]
        self.assertEqual(
            set(result),
            {
                "rank_minimum",
                "rank_maximum",
                "Start_Time_timestamp",
                "End_Time_timestamp",
                "authorID",
                "messageURL",
                "messageContent",
            },
        )
        self.assertEqual(result["authorID"], "42")
        self.assertEqual(result["messageContent"], message["content"])
