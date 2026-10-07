import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from rionnag import config
from rionnag.cogs.onboarding import Onboarding
from rionnag.services.applications import Applications
from rionnag.services.permissions import apply_server_policy, ticket_overwrites, visitor_eligible
from rionnag.services.resets import Resets
from rionnag.storage import Store
from rionnag.ui.forms import FormPage
from rionnag.ui.onboarding import ReviewView


def role(role_id, name="role"):
    value = Mock(spec=discord.Role)
    value.id, value.name, value.managed = role_id, name, False
    value.is_default.return_value = role_id == config.GUILD_ID
    value.__ge__ = Mock(return_value=False)
    return value


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "test.sqlite3")
        self.forms = config.load_forms()
        self.form = self.forms["marvel-rivals"]
        self.app = Applications(Mock(), self.store, self.forms)
        self.roles = {
            rid: role(rid)
            for rid in (
                config.GUILD_ID,
                config.OWNER_ROLE_ID,
                config.VISITOR_ROLE_ID,
                self.form["team_role"],
                self.form["tryout_role"],
                self.form["manager_role"],
            )
        }
        self.guild = Mock(id=config.GUILD_ID)
        self.guild.roles = list(self.roles.values())
        self.guild.default_role = self.roles[config.GUILD_ID]
        self.guild.get_role.side_effect = self.roles.get
        self.member = Mock(spec=discord.Member)
        self.member.id, self.member.guild, self.member.bot = 42, self.guild, False
        self.member.name = "test-user"
        self.member.roles = [self.guild.default_role]
        self.member.add_roles = AsyncMock()
        self.member.remove_roles = AsyncMock()
        self.member.send = AsyncMock()
        self.answers = dict(
            username="Player",
            time_zone="Eastern Time (ET)",
            preferred_role_1="Tank",
            preferred_role_2="DPS",
            availability="Monday 6 PM–10 PM",
            availability_days={"Monday": {"start": 18, "end": 22}},
        )

    def test_missing_new_question_blocks_submit(self):
        form = copy.deepcopy(self.form)
        form["questions"].append(dict(key="new", label="New question", required=True))
        with self.assertRaisesRegex(ValueError, "New question"):
            self.app.validate(form, self.answers)
        self.app.validate(form, dict(self.answers, new="answered"))

    async def test_departure_erases_saved_data_even_if_ticket_delete_fails(self):
        self.store.update(
            42,
            status="accepted",
            game="marvel-rivals",
            answers=self.answers,
            restore_roles=[self.form["manager_role"]],
            dm_pending="private",
        )
        self.store.save_profile(config.GUILD_ID, 42, self.form, self.answers)
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO player_availability VALUES(?,?,?,?)",
                (config.GUILD_ID, 42, "Marvel Rivals", "{}"),
            )
        cog = Onboarding(Mock(), self.app)
        with patch.object(self.app, "close_ticket", AsyncMock(side_effect=RuntimeError("Discord offline"))):
            with self.assertRaises(RuntimeError):
                await cog.on_member_remove(self.member)
        self.assertNotIn(42, self.store.ids())
        self.assertIsNone(self.store.saved_profile(config.GUILD_ID, 42))
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM player_availability").fetchone()[0], 0)
        row = self.store.member(42)
        self.assertEqual(row["status"], "new")
        self.assertEqual(row["answers"], {})
        self.assertIsNone(row["restore_roles"])

    def test_version_guard_requires_increment(self):
        self.store.record_form("marvel-rivals", self.form)
        modified = copy.deepcopy(self.form)
        modified["questions"].append(dict(key="new", label="New question"))
        with self.assertRaises(ValueError):
            self.store.check_form("marvel-rivals", modified)
        modified["version"] = 2
        self.store.check_form("marvel-rivals", modified)

    def test_ticket_only_applicant_then_selected_manager(self):
        before = ticket_overwrites(self.member)
        self.assertTrue(before[self.member].view_channel)
        manager = self.roles[self.form["manager_role"]]
        self.assertFalse(before[manager].view_channel)
        self.assertFalse(before[self.roles[config.VISITOR_ROLE_ID]].view_channel)
        after = ticket_overwrites(self.member, self.form)
        self.assertTrue(after[manager].view_channel)
        self.assertFalse(after[self.roles[self.form["team_role"]]].view_channel)

    def test_team_with_visitor_role_cannot_apply(self):
        self.member.roles.append(self.roles[config.VISITOR_ROLE_ID])
        self.assertTrue(visitor_eligible(self.member, self.forms))
        self.member.roles.append(self.roles[self.form["team_role"]])
        self.assertFalse(visitor_eligible(self.member, self.forms))

    async def test_visitors_read_marvel_category_text_and_voice_without_posting_or_joining(self):
        category_id = 1555382746992353290
        channels = [
            SimpleNamespace(id=category_id, category_id=None, overwrites={}, edit=AsyncMock()),
            SimpleNamespace(id=10, category_id=category_id, overwrites={}, edit=AsyncMock()),
            SimpleNamespace(id=11, category_id=category_id, overwrites={}, edit=AsyncMock()),
        ]
        self.guild.channels = channels
        self.guild.get_channel.return_value = None
        await apply_server_policy(self.guild, self.forms, self.store)
        for channel in channels:
            overwrite = channel.edit.call_args.kwargs["overwrites"][self.roles[config.VISITOR_ROLE_ID]]
            self.assertTrue(overwrite.view_channel)
            self.assertTrue(overwrite.read_message_history)
            for permission in (
                "send_messages",
                "send_messages_in_threads",
                "create_public_threads",
                "create_private_threads",
                "connect",
                "speak",
            ):
                self.assertFalse(getattr(overwrite, permission))

    async def test_owner_reset_removes_manager_but_preserves_rionnag(self):
        self.member.roles += [
            self.roles[config.OWNER_ROLE_ID],
            self.roles[self.form["manager_role"]],
            self.roles[self.form["team_role"]],
        ]
        self.store.update(42, status="accepted", game="marvel-rivals", version=1, answers=self.answers)
        self.form["version"] = 2
        with patch.object(self.app, "welcome", new_callable=AsyncMock):
            await Resets(self.app).reset_member(self.member, "marvel-rivals")
        row = self.store.member(42)
        self.assertEqual(row["status"], "reset")
        self.assertEqual(row["answers"], self.answers)
        self.assertIn(self.form["manager_role"], row["restore_roles"])
        self.assertNotIn(config.OWNER_ROLE_ID, row["restore_roles"])
        self.assertNotIn(self.roles[config.OWNER_ROLE_ID], self.member.remove_roles.call_args.args)

    async def test_interrupted_reset_preserves_original_snapshot(self):
        self.store.update(
            42,
            status="reset",
            game="marvel-rivals",
            answers=self.answers,
            restore_roles=[self.form["manager_role"]],
            restore_status="accepted",
        )
        self.member.roles = [self.guild.default_role]
        with patch.object(self.app, "welcome", new_callable=AsyncMock):
            await Resets(self.app).reset_member(self.member, "marvel-rivals")
        self.assertEqual(self.store.member(42)["restore_roles"], [self.form["manager_role"]])

    async def test_reset_manager_auto_restores_without_review(self):
        self.store.update(
            42,
            status="reset",
            game="marvel-rivals",
            answers=self.answers,
            restore_roles=[self.form["manager_role"], self.form["team_role"]],
            restore_status="accepted",
        )
        with patch.object(self.app, "close_ticket", new_callable=AsyncMock):
            await self.app.restore_member(self.member, self.form, self.answers)
        self.assertIn(self.roles[self.form["manager_role"]], self.member.add_roles.call_args.args)
        self.assertEqual(self.store.member(42)["status"], "accepted")
        self.assertIsNone(self.store.member(42)["restore_roles"])
        self.member.send.assert_awaited_once()

    async def test_no_data_with_existing_roles_requires_ticket(self):
        self.member.roles += [self.roles[config.OWNER_ROLE_ID], self.roles[self.form["team_role"]]]
        with patch.object(self.app, "welcome", new_callable=AsyncMock) as welcome:
            await Resets(self.app).reconcile_member(self.member)
        welcome.assert_awaited_once_with(self.member)
        self.assertEqual(self.store.member(42)["restore_roles"], [self.form["team_role"]])
        self.member.remove_roles.assert_awaited_once_with(
            self.roles[self.form["team_role"]], reason="No completed onboarding data"
        )

    async def test_completed_visitor_does_not_get_new_ticket(self):
        self.store.update(42, status="visitor", answers={"membership": "visitor"})
        with (
            patch.object(self.app, "welcome", new_callable=AsyncMock) as welcome,
            patch.object(self.app, "close_ticket", new_callable=AsyncMock),
        ):
            await Resets(self.app).reconcile_member(self.member)
        welcome.assert_not_awaited()

    async def test_rejection_grants_only_visitor_and_sends_rejected_dm(self):
        self.store.update(
            42, status="deciding", game="marvel-rivals", answers=self.answers, restore_status="rejected"
        )
        with patch.object(self.app, "close_ticket", new_callable=AsyncMock):
            await self.app.finish_decision(self.member)
        self.member.add_roles.assert_awaited_once_with(
            self.roles[config.VISITOR_ROLE_ID], reason="Tryout rejected"
        )
        self.assertIn("rejected", self.member.send.call_args.args[0])
        self.assertEqual(self.store.member(42)["status"], "rejected")

    async def test_acceptance_removes_visitor_hides_entry_and_sends_dm(self):
        self.store.update(
            42, status="deciding", game="marvel-rivals", answers=self.answers, restore_status="accepted"
        )
        with patch.object(self.app, "close_ticket", new_callable=AsyncMock):
            await self.app.finish_decision(self.member)
        self.member.remove_roles.assert_awaited_once_with(
            self.roles[config.VISITOR_ROLE_ID], reason="Tryout accepted"
        )
        self.member.add_roles.assert_awaited_once_with(
            self.roles[self.form["tryout_role"]], reason="Tryout accepted"
        )
        self.assertIn("accepted", self.member.send.call_args.args[0])
        self.assertIsNotNone(self.store.saved_profile(config.GUILD_ID, 42))

    async def test_decision_confirmation_precedes_dm_and_ticket_deletion(self):
        self.store.update(
            42, status="deciding", game="marvel-rivals", answers=self.answers, restore_status="accepted"
        )
        events = []

        async def confirm():
            self.assertEqual(self.store.member(42)["status"], "accepted")
            events.append("confirmed")

        async def notify(member):
            events.append("notified")

        async def close(member):
            events.append("deleted")

        with (
            patch.object(self.app, "notify", side_effect=notify),
            patch.object(self.app, "close_ticket", side_effect=close),
        ):
            await self.app.finish_decision(self.member, on_complete=confirm)
        self.assertEqual(events, ["confirmed", "notified", "deleted"])

    async def test_self_review_forbidden_even_for_manager(self):
        self.member.roles += [self.roles[self.form["manager_role"]]]
        self.store.update(42, status="pending", game="marvel-rivals", channel_id=100)
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        interaction = SimpleNamespace(channel_id=100, guild=self.guild, user=self.member)
        with self.assertRaisesRegex(ValueError, "cannot review themselves"):
            await self.app.decide(interaction, True)
        self.member.add_roles.assert_not_awaited()

    async def test_owner_can_accept_self_without_manager_role(self):
        self.guild.owner_id = self.member.id
        self.store.update(42, status="pending", game="marvel-rivals", channel_id=100, answers=self.answers)
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        interaction = SimpleNamespace(channel_id=100, guild=self.guild, user=self.member)
        with patch.object(self.app, "close_ticket", new_callable=AsyncMock):
            await self.app.decide(interaction, True)
        self.assertEqual(self.store.member(42)["status"], "accepted")

    async def test_owner_form_auto_accepts_without_review(self):
        self.guild.owner_id = self.member.id
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        self.store.update(42, game="marvel-rivals")
        interaction = SimpleNamespace(user=self.member, guild_id=config.GUILD_ID, guild=self.guild)
        identity = dict(uid="12345", name="Player")
        with (
            patch("rionnag.integrations.accounts.verify_account", AsyncMock(return_value=identity)),
            patch.object(self.app, "ticket", AsyncMock(return_value=Mock(id=100))),
            patch.object(self.app, "close_ticket", AsyncMock()),
            patch.object(self.app, "publish", AsyncMock()) as review,
        ):
            await self.app.submit(interaction, 42, "marvel-rivals", self.answers, 1)
        review.assert_not_awaited()
        self.assertEqual(self.store.member(42)["status"], "accepted")
        self.assertIn("automatically", self.member.send.call_args.args[0])

    async def test_reset_submission_restores_roles_without_review(self):
        self.guild.owner_id = 99
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        self.store.update(
            42,
            status="reset",
            game="marvel-rivals",
            restore_roles=[self.form["manager_role"]],
            restore_status="accepted",
        )
        interaction = SimpleNamespace(user=self.member, guild_id=config.GUILD_ID, guild=self.guild)
        identity = dict(uid="12345", name="Player")
        with (
            patch("rionnag.integrations.accounts.verify_account", AsyncMock(return_value=identity)),
            patch.object(self.app, "ticket", AsyncMock(return_value=Mock(id=100))),
            patch.object(self.app, "close_ticket", AsyncMock()),
            patch.object(self.app, "publish", AsyncMock()) as review,
        ):
            await self.app.submit(interaction, 42, "marvel-rivals", self.answers, 1)
        review.assert_not_awaited()
        self.assertIn(self.roles[self.form["manager_role"]], self.member.add_roles.call_args.args)

    async def test_new_member_and_visitor_use_same_submission(self):
        self.guild.owner_id = 99
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        interaction = SimpleNamespace(user=self.member, guild_id=config.GUILD_ID, guild=self.guild)
        identity = dict(uid="12345", name="Player")
        for status in ("new", "visitor"):
            self.store.update(42, status=status, game="marvel-rivals", message_id=None)
            self.member.roles = [self.guild.default_role] + (
                [self.roles[config.VISITOR_ROLE_ID]] if status == "visitor" else []
            )
            with (
                patch("rionnag.integrations.accounts.verify_account", AsyncMock(return_value=identity)),
                patch.object(self.app, "ticket", AsyncMock(return_value=Mock(id=100))) as ticket,
                patch.object(self.app, "publish", AsyncMock()) as review,
            ):
                await self.app.submit(interaction, 42, "marvel-rivals", self.answers, 1)
            ticket.assert_awaited_once_with(self.member)
            review.assert_awaited_once()
            self.assertEqual(self.store.member(42)["status"], "pending")

    async def test_rejoin_restores_manager_not_only_tryout(self):
        self.store.update(
            42,
            status="accepted",
            game="marvel-rivals",
            version=1,
            membership_roles=[self.form["manager_role"]],
        )
        with patch.object(self.app, "close_ticket", new_callable=AsyncMock):
            await Resets(self.app).reconcile_member(self.member)
        self.member.add_roles.assert_awaited_once_with(
            self.roles[self.form["manager_role"]], reason="Restore completed membership on rejoin"
        )

    async def test_review_has_accept_reject_and_applicant_edit(self):
        view = ReviewView(self.app)
        self.assertEqual([button.label for button in view.children], ["Accept", "Reject", "Edit"])
        self.assertTrue(view.is_persistent())

    async def test_only_applicant_can_open_pending_edit(self):
        self.store.update(42, status="pending", game="marvel-rivals", channel_id=100, answers=self.answers)
        interaction = SimpleNamespace(
            user=self.member,
            guild_id=config.GUILD_ID,
            channel_id=100,
            response=SimpleNamespace(send_modal=AsyncMock()),
        )
        await self.app.edit_application(interaction)
        modal = interaction.response.send_modal.call_args.args[0]
        self.assertTrue(modal.editing)
        self.assertEqual(modal.fields["username"].default, "Player")
        interaction.user = SimpleNamespace(id=99)
        with self.assertRaisesRegex(ValueError, "Only the applicant"):
            await self.app.edit_application(interaction)

    async def test_pending_edit_updates_same_message_without_role_changes(self):
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        self.store.update(
            42,
            status="pending",
            game="marvel-rivals",
            channel_id=100,
            message_id=200,
            answers=self.answers,
            version=1,
        )
        interaction = SimpleNamespace(
            user=self.member, guild_id=config.GUILD_ID, guild=self.guild, channel_id=100
        )
        updated = dict(self.answers, preferred_role_1="Support")
        with (
            patch(
                "rionnag.integrations.accounts.verify_account",
                AsyncMock(return_value=dict(uid="123", name="Player")),
            ),
            patch.object(self.app, "ticket", AsyncMock(return_value=Mock(id=100))),
            patch.object(self.app, "publish", AsyncMock()) as publish,
        ):
            await self.app.submit(interaction, 42, "marvel-rivals", updated, 1, editing=True)
        publish.assert_awaited_once()
        row = self.store.member(42)
        self.assertEqual((row["status"], row["message_id"]), ("pending", 200))
        self.assertEqual(row["answers"]["preferred_role_1"], "Support")
        self.member.add_roles.assert_not_awaited()
        self.member.remove_roles.assert_not_awaited()
        self.store.update(42, status="accepted")
        with self.assertRaisesRegex(ValueError, "already been submitted"):
            await self.app.submit(interaction, 42, "marvel-rivals", updated, 1, editing=True)

    async def test_saved_form_command_opens_prefilled_accepted_form_anywhere(self):
        self.store.update(42, status="accepted", game="marvel-rivals", version=1, answers=self.answers)
        interaction = SimpleNamespace(
            user=self.member, guild_id=config.GUILD_ID, response=SimpleNamespace(send_modal=AsyncMock())
        )
        await self.app.edit_saved_form(interaction)
        modal = interaction.response.send_modal.call_args.args[0]
        self.assertEqual(modal.editing, "accepted")
        self.assertEqual(modal.fields["username"].default, "Player")
        self.store.update(42, status="reset", restore_roles=[self.form["team_role"]])
        with self.assertRaises(ValueError):
            await self.app.edit_saved_form(interaction)

    async def test_completed_form_edit_updates_profile_without_ticket_or_roles(self):
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        for status in ("accepted", "rejected", "visitor"):
            self.store.update(
                42,
                status=status,
                game="marvel-rivals",
                version=1,
                answers=self.answers,
                membership_roles=[self.form["manager_role"]],
            )
            interaction = SimpleNamespace(
                user=self.member, guild_id=config.GUILD_ID, guild=self.guild, channel_id=999
            )
            updated = dict(self.answers, preferred_role_1="Support")
            with (
                patch(
                    "rionnag.integrations.accounts.verify_account",
                    AsyncMock(return_value=dict(uid="123", name="Player")),
                ),
                patch.object(self.app, "ticket", AsyncMock()) as ticket,
            ):
                await self.app.submit(interaction, 42, "marvel-rivals", updated, 1, editing=status)
            ticket.assert_not_awaited()
            row = self.store.member(42)
            self.assertEqual(row["status"], status)
            self.assertEqual(row["membership_roles"], [self.form["manager_role"]])
            self.assertEqual(row["answers"]["preferred_role_1"], "Support")
        self.member.add_roles.assert_not_awaited()
        self.member.remove_roles.assert_not_awaited()
        self.assertEqual(self.store.saved_profile(config.GUILD_ID, 42)[2], "Support")

    async def test_rejected_account_is_reusable_and_active_conflict_mentions_member(self):
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        interaction = SimpleNamespace(
            user=self.member, guild_id=config.GUILD_ID, guild=self.guild, channel_id=999
        )
        self.store.update(42, status="accepted", game="marvel-rivals", version=1, answers=self.answers)
        for status, restore in (
            ("rejected", None),
            ("visitor", None),
            ("new", None),
            ("pending", None),
            ("accepted", None),
            ("deciding", None),
            ("reset", []),
        ):
            self.store.update(
                99,
                status=status,
                game="marvel-rivals",
                answers=dict(self.answers, player_uid="123"),
                restore_roles=restore,
            )
            with patch(
                "rionnag.integrations.accounts.verify_account",
                AsyncMock(return_value=dict(uid="123", name="Player")),
            ):
                if status in {"pending", "accepted", "deciding", "reset"}:
                    with self.assertRaisesRegex(ValueError, "<@99>"):
                        await self.app.submit(
                            interaction, 42, "marvel-rivals", self.answers, 1, editing="accepted"
                        )
                else:
                    await self.app.submit(
                        interaction, 42, "marvel-rivals", self.answers, 1, editing="accepted"
                    )

    async def test_accepted_edit_reuses_verified_identity_but_verifies_changed_account(self):
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        saved = dict(self.answers, player_uid="123")
        self.store.update(42, status="accepted", game="marvel-rivals", version=1, answers=saved)
        interaction = SimpleNamespace(user=self.member, guild_id=config.GUILD_ID, guild=self.guild)
        with patch("rionnag.integrations.accounts.verify_account", new_callable=AsyncMock) as verify:
            await self.app.submit(interaction, 42, "marvel-rivals", saved, 1, editing="accepted")
            verify.assert_not_awaited()
            self.store.update(99, status="accepted", game="marvel-rivals", answers=saved)
            with self.assertRaisesRegex(ValueError, "<@99>"):
                await self.app.submit(interaction, 42, "marvel-rivals", saved, 1, editing="accepted")
            verify.assert_not_awaited()
            verify.return_value = dict(uid="456", name="Different")
            changed = dict(saved, player_uid="456", username="Different")
            await self.app.submit(interaction, 42, "marvel-rivals", changed, 1, editing="accepted")
            verify.assert_awaited_once_with("marvel-rivals", "456")

    async def test_new_fields_paginate_and_old_answers_prefill(self):
        self.form["questions"].append(dict(key="extra", label="New question", required=True))
        page = FormPage(self.app, 42, "marvel-rivals", 0, self.answers)
        self.assertEqual(len(page.children), 5)
        self.assertEqual(page.fields["username"].default, "Player")
        second = FormPage(self.app, 42, "marvel-rivals", 1, self.answers)
        self.assertEqual(list(second.fields), ["extra"])

    async def test_returning_member_cannot_skip_updated_game_form(self):
        self.store.update(
            42, status="reset", channel_id=100, game="marvel-rivals", restore_roles=[self.form["team_role"]]
        )
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        interaction = SimpleNamespace(guild=self.guild, user=self.member, channel_id=100)
        with self.assertRaisesRegex(ValueError, "updated game form"):
            await self.app.choose_visitor(interaction)

    async def test_one_ticket_reused(self):
        channel = Mock(id=100)
        channel.edit = AsyncMock()
        self.guild.get_channel.return_value = channel
        self.store.update(42, channel_id=100)
        self.assertIs(await self.app.ticket(self.member), channel)
        self.guild.create_text_channel.assert_not_called()

    async def test_partial_draft_is_not_reset_by_periodic_reconciliation(self):
        self.store.update(99, status="accepted", game="marvel-rivals", answers=self.answers)
        self.store.save_profile(config.GUILD_ID, 99, self.form, self.answers)
        self.store.update(
            42, status="new", game="marvel-rivals", version=0, answers=self.answers, message_id=100
        )

        async def members():
            yield self.member

        self.guild.fetch_members = Mock(side_effect=lambda **kwargs: members())
        self.guild.get_channel.return_value = None
        resets = Resets(self.app)
        with (
            patch.object(resets, "reset_member", AsyncMock()) as reset,
            patch.object(resets, "reconcile_member", AsyncMock()),
        ):
            await resets.reconcile(self.guild)
        reset.assert_not_awaited()
        self.assertEqual(self.store.member(42)["message_id"], 100)
        self.assertNotIn(99, self.store.ids())
        self.assertIsNone(self.store.saved_profile(config.GUILD_ID, 99))


if __name__ == "__main__":
    unittest.main()
