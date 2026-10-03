import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from rionnag import config
from rionnag.services.applications import Applications, ticket_name
from rionnag.storage import Store
from rionnag.ui.accounts import AccountPicker, continue_to_availability
from rionnag.ui.availability import AvailabilityView, TimeWindow, schedule_text, validate_days
from rionnag.ui.forms import FormPage
from rionnag.ui.onboarding import GameButton


class PickerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = Store(Path(folder.name) / "test.sqlite3")
        self.app = Applications(Mock(), self.store, config.load_forms())
        self.store.update(42, game="marvel-rivals")
        self.modal = FormPage(
            self.app, 42, "marvel-rivals", 0, dict(username="Test", time_zone="Eastern Time (ET)")
        )
        self.interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            guild_id=config.GUILD_ID,
            response=SimpleNamespace(edit_message=AsyncMock(), defer=AsyncMock(), send_message=AsyncMock()),
            edit_original_response=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_add_two_days_then_finish_saves_and_submits_once(self):
        for day, start, end in [("Monday", 18, 22), ("Friday", 22, 24)]:
            view = AvailabilityView(self.modal)
            window = TimeWindow(view, day, start, end)
            await window.save_day(self.interaction)
        days = self.store.member(42)["answers"]["availability_days"]
        self.assertEqual(days["Friday"], dict(start=22, end=24))
        self.assertIn("<t:", schedule_text(days))
        view = AvailabilityView(self.modal)
        self.assertEqual(len(view.days), 2)
        with patch.object(self.app, "submit", new_callable=AsyncMock) as submit:
            await view.finish(self.interaction)
        submit.assert_awaited_once()
        self.assertEqual(submit.call_args.args[3]["availability_days"], days)

    async def test_reopen_prefills_selected_day_hours(self):
        self.modal.answers["availability_days"] = {"Monday": {"start": 18, "end": 22}}
        window = TimeWindow(AvailabilityView(self.modal), "Monday")
        self.assertEqual([o.value for o in window.start.options if o.default], ["18"])
        self.assertEqual([o.value for o in window.end.options if o.default], ["22"])

    async def test_pending_edit_draft_does_not_replace_reviewed_answers_until_finish(self):
        self.store.update(
            42,
            status="pending",
            answers=dict(self.modal.answers, availability_days={"Sunday": {"start": 9, "end": 12}}),
        )
        self.modal.editing = True
        view = AvailabilityView(self.modal)
        view.days = {"Monday": {"start": 18, "end": 22}}
        await view.save()
        self.assertIn("Sunday", self.store.member(42)["answers"]["availability_days"])
        self.assertIn("Monday", self.modal.answers["availability_days"])

    async def test_empty_schedule_cannot_finish_and_equal_times_rejected(self):
        view = AvailabilityView(self.modal)
        self.assertTrue(view.children[-1].disabled)
        with self.assertRaises(ValueError):
            await view.finish(self.interaction)
        with self.assertRaises(ValueError):
            validate_days({"Monday": {"start": 18, "end": 18}})

    async def test_start_filters_end_times_and_clears_too_early_end(self):
        window = TimeWindow(AvailabilityView(self.modal), "Monday", 15, 17)
        self.assertEqual([int(option.value) for option in window.end.options], list(range(16, 25)))
        window.start._values = ["18"]
        await window.choose_start(self.interaction)
        changed = self.interaction.response.edit_message.call_args.kwargs["view"]
        self.assertIsNone(changed.end_hour)
        self.assertTrue(changed.children[2].disabled)
        self.assertEqual([int(option.value) for option in changed.end.options], list(range(19, 25)))

    async def test_wrong_user_cannot_change_schedule(self):
        self.interaction.user.id = 99
        view = AvailabilityView(self.modal)
        with self.assertRaises(ValueError):
            await view.interaction_check(self.interaction)

    async def test_search_note_and_no_availability_text_box(self):
        self.assertIsInstance(self.modal.children[0], discord.ui.TextDisplay)
        self.assertIn("normal characters", self.modal.children[0].content)
        self.assertNotIn("availability", self.modal.fields)
        self.assertEqual(len(self.modal.children), 5)

    async def test_search_results_select_exact_uid_not_search_name(self):
        matches = [{"name": "Ｔｅｓｔ✦", "uid": "123"}, {"name": "Test Two", "uid": "456"}]
        with patch("rionnag.ui.accounts.queued_lookup", return_value=matches) as search:
            await continue_to_availability(self.interaction, self.modal)
        self.assertEqual(search.call_args.args[1], "Test")
        picker = self.interaction.followup.send.call_args.kwargs["view"]
        self.assertIsInstance(picker, AccountPicker)
        picker.children[0]._values = ["0"]
        await picker.choose(self.interaction)
        self.assertEqual(self.modal.answers["player_uid"], "123")
        self.assertEqual(self.modal.answers["username"], "Ｔｅｓｔ✦")
        self.assertIsInstance(
            self.interaction.response.edit_message.call_args.kwargs["view"], AvailabilityView
        )

    async def test_emoji_and_username_channel_name(self):
        self.assertEqual(GameButton(self.app, "marvel-rivals").emoji.id, 1554291577701146634)
        self.assertEqual(ticket_name(SimpleNamespace(name="Player.Name", id=12345678)), "player-name-5678")

    async def test_welcome_mentions_applicant_above_embed_with_ping_permission(self):
        member = SimpleNamespace(id=42, name="Test", mention="<@42>")
        channel = SimpleNamespace(
            send=AsyncMock(return_value=SimpleNamespace(id=100)),
            fetch_message=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
        )
        with patch.object(self.app, "ticket", AsyncMock(return_value=channel)):
            await self.app.welcome(member)
            sent = channel.send.call_args.kwargs
            self.assertEqual(sent["content"], "<@42>")
            self.assertEqual(sent["embed"].title, "Welcome to Rionnag")
            self.assertEqual(sent["allowed_mentions"].users, [member])
            await self.app.welcome(member)
        message = channel.fetch_message.return_value
        self.assertEqual(message.edit.call_args.kwargs["content"], "<@42>")


if __name__ == "__main__":
    unittest.main()
