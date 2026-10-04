import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rionnag.cogs.profiles import Profiles
from rionnag.services.lookup import Lookup
from rionnag.storage import Store


class LookupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "test.sqlite3")
        self.member = SimpleNamespace(id=42, name="eternalwiinter", display_name="eternalwii",
                                      global_name="Winter", roles=[SimpleNamespace(id=7)])
        self.guild = SimpleNamespace(id=10, members=[self.member],
                                     get_member=lambda uid: self.member if uid == 42 else None)
        self.store.save_profile(10, 42, {"name": "Marvel Rivals"}, {
            "username": "Chenoa_", "time_zone": "Eastern",
            "preferred_role_1": "Support", "preferred_role_2": "Tank"})
        self.lookup = Lookup(SimpleNamespace(store=self.store, forms={
            "marvel-rivals": {"team_role": 7, "manager_role": 8}}))

    @patch("rionnag.services.lookup.search_player_accounts", return_value=[])
    async def test_aliases_show_saved_name_without_membership_label(self, search):
        for query in ("eternalwii", "eternalwiinter", "chenoa", "winter"):
            choices = await self.lookup.autocomplete(self.guild, query)
            self.assertEqual(choices[0][1], "member:42")
            self.assertTrue(choices[0][0].startswith("Chenoa_"))
            self.assertEqual(choices[0][0], "Chenoa_ · @eternalwiinter")

    def test_mentions_and_plain_names_resolve_saved_account(self):
        for query in ("<@42>", "<@!42>", "member:42", "eternalwii", "Chenoa_"):
            member, saved, account = self.lookup.resolve(self.guild, query)
            self.assertEqual(member.id, 42)
            self.assertEqual(account, saved[0])
        with self.assertRaises(ValueError):
            self.lookup.resolve(self.guild, "<@999>")
        with self.assertRaises(ValueError):
            self.lookup.resolve(self.guild, " ")

    async def test_provider_search_and_failure(self):
        with patch("rionnag.services.lookup.search_player_accounts",
                   return_value=[{"name": "Other", "uid": "123"}]):
            self.assertEqual(await self.lookup.autocomplete(self.guild, "Other"),
                             [("Other", "account:123")])
        self.assertEqual(self.lookup.resolve(self.guild, "account:123"), (None, None, "123"))
        self.assertEqual(self.lookup.resolve(self.guild, "Other"), (None, None, "Other"))
        with patch("rionnag.services.lookup.search_player_accounts", side_effect=RuntimeError("offline")):
            self.assertEqual(await self.lookup.autocomplete(self.guild, "offline"), [])

    async def test_saved_matches_do_not_suppress_public_search_and_cache(self):
        with patch("rionnag.services.lookup.search_player_accounts", return_value=[
            {"name": "Chenoa_other", "uid": "123"}, {"name": "Chenoa_other", "uid": "123"}
        ]) as search:
            choices = await self.lookup.autocomplete(self.guild, "chenoa")
            self.assertEqual(choices, [("Chenoa_ · @eternalwiinter", "member:42"),
                                       ("Chenoa_other", "account:123")])
            self.assertEqual(await self.lookup.autocomplete(self.guild, "CHENOA"), choices)
            search.assert_called_once_with("chenoa")

    async def test_provider_failure_preserves_saved_suggestions(self):
        with patch("rionnag.services.lookup.search_player_accounts", side_effect=RuntimeError("offline")):
            self.assertEqual(await self.lookup.autocomplete(self.guild, "chenoa"),
                             [("Chenoa_ · @eternalwiinter", "member:42")])

    async def test_concurrent_queries_are_bounded_and_keep_their_own_results(self):
        release = asyncio.Event()

        async def search(function, query):
            await release.wait()
            return [{"name": query, "uid": "123"}]

        with patch("rionnag.services.lookup.asyncio.to_thread", side_effect=search):
            pending = []
            try:
                for query in ("alpha", "beta", "gamma"):
                    pending.append(asyncio.create_task(self.lookup.autocomplete(self.guild, query)))
                    await asyncio.sleep(0)
                self.assertEqual(await self.lookup.autocomplete(self.guild, "delta"), [])
                self.assertEqual(len(self.lookup.searches), 3)
            finally:
                release.set()
                results = await asyncio.gather(*pending)
            self.assertEqual(results, [[(query, "account:123")]
                                       for query in ("alpha", "beta", "gamma")])

    def test_ambiguous_names_require_selection(self):
        second = SimpleNamespace(id=43, name="eternalother", display_name="eternalwii",
                                 global_name=None, roles=[])
        self.guild.members.append(second)
        self.store.save_profile(10, 43, {"name": "Marvel Rivals"}, {
            "username": "Other", "time_zone": "Eastern",
            "preferred_role_1": "Tank", "preferred_role_2": "Support"})
        with self.assertRaisesRegex(ValueError, "Multiple"):
            self.lookup.resolve(self.guild, "eternalwii")

    def test_lookup_requires_autocompleted_string(self):
        self.assertEqual(Profiles.lookup.name, "lookup")
        parameter = Profiles.lookup.parameters[0]
        self.assertTrue(parameter.required)
        self.assertTrue(parameter.autocomplete)
        method = Profiles.lookup.parameters[1]
        self.assertFalse(method.required)
        self.assertIsNone(method.default)
        self.assertEqual([choice.value for choice in method.choices], ["normal", "precise"])
