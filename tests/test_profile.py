import re
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

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

    def test_form_fields_and_discord_timestamps(self):
        embed = form_profile_embed(self.member, self.form, self.answers,
                                   datetime(2026, 10, 3, 18, tzinfo=UTC))
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("Future Game", embed.title)
        self.assertEqual(fields["In-game username"], "Player")
        self.assertEqual(fields["Favorite character"], "Hero")
        self.assertEqual(fields["Current time"],
                         f"<t:{int(datetime(2026, 10, 3, 18, tzinfo=UTC).timestamp())}:t>")
        stamps = re.findall(r"<t:(\d+):t>", fields["Days and times free"])
        dates = [datetime.fromtimestamp(int(stamp), ZoneInfo("America/Los_Angeles"))
                 for stamp in stamps]
        self.assertEqual([(date.weekday(), date.hour) for date in dates], [(6, 9), (0, 0)])
        self.assertNotIn("private metadata", str(embed.to_dict()))

    def test_availability_observes_winter_dst(self):
        embed = form_profile_embed(self.member, self.form, self.answers,
                                   datetime(2026, 12, 3, 18, tzinfo=UTC))
        stamps = re.findall(r"<t:(\d+):t>", embed.fields[-1].value)
        start = datetime.fromtimestamp(int(stamps[0]), UTC)
        self.assertEqual(start.hour, 17)

    async def test_profile_defaults_to_caller_and_accepts_other_member(self):
        self.assertEqual(Profiles.profile.parameters[0].name, "member")
        self.assertFalse(Profiles.profile.parameters[0].required)
        store = Mock()
        store.member.return_value = {"game": "future", "version": 2,
                                     "status": "accepted", "answers": self.answers}
        cog = Profiles(SimpleNamespace(store=store, forms={"future": self.form}))
        interaction = SimpleNamespace(user=self.member, response=SimpleNamespace(send_message=AsyncMock()))
        await Profiles.profile.callback(cog, interaction)
        store.member.assert_called_once_with(42)
        self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])
        other = SimpleNamespace(id=99, display_name="Other member")
        await Profiles.profile.callback(cog, interaction, other)
        store.member.assert_called_with(99)
        self.assertIn("Other member", interaction.response.send_message.call_args.kwargs["embed"].title)
        for status, version in (("reset", 2), ("new", 2), ("accepted", 1)):
            store.member.return_value.update(status=status, version=version)
            with self.assertRaisesRegex(ValueError, "finish"):
                await Profiles.profile.callback(cog, interaction)
