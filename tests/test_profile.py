import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from rionnag.cogs.profiles import Profiles
from rionnag.ui.profile import form_profile_embed


class ProfileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.form = {"name": "Future Game", "version": 2, "questions": [
            {"key": "username", "label": "In-game username"},
            {"key": "time_zone", "label": "Time zone"},
            {"key": "favorite", "label": "Favorite character"},
            {"key": "availability", "label": "Availability"}]}
        self.answers = {"username": "Player", "time_zone": "Pacific Time (PT)",
                        "favorite": "Hero", "player_uid": "private metadata",
                        "availability_days": {"Sunday": {"start": 9, "end": 24}}}
        self.member = SimpleNamespace(id=42, display_name="Member")

    def test_form_fields_and_member_local_clock(self):
        embed = form_profile_embed(self.member, self.form, self.answers,
                                   datetime(2026, 10, 3, 18, tzinfo=UTC))
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("Future Game", embed.title)
        self.assertEqual(fields["In-game username"], "Player")
        self.assertEqual(fields["Favorite character"], "Hero")
        self.assertEqual(fields["Your local time"], "Saturday, 11:00 AM PDT")
        self.assertEqual(fields["Days and times free (your local time)"],
                         "Sunday: 9:00 AM – 12:00 AM (next day)")
        self.assertNotIn("private metadata", str(embed.to_dict()))

    def test_local_clock_observes_winter_dst(self):
        embed = form_profile_embed(self.member, self.form, self.answers,
                                   datetime(2026, 12, 3, 18, tzinfo=UTC))
        self.assertEqual(embed.fields[-2].value, "Thursday, 10:00 AM PST")

    async def test_profile_has_no_arguments_and_uses_only_caller(self):
        self.assertEqual(Profiles.profile.parameters, [])
        store = Mock()
        store.member.return_value = {"game": "future", "version": 2,
                                     "status": "accepted", "answers": self.answers}
        cog = Profiles(SimpleNamespace(store=store, forms={"future": self.form}))
        interaction = SimpleNamespace(user=self.member, response=SimpleNamespace(send_message=AsyncMock()))
        await Profiles.profile.callback(cog, interaction)
        store.member.assert_called_once_with(42)
        self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])
        for status, version in (("reset", 2), ("new", 2), ("accepted", 1)):
            store.member.return_value.update(status=status, version=version)
            with self.assertRaisesRegex(ValueError, "Finish"):
                await Profiles.profile.callback(cog, interaction)
