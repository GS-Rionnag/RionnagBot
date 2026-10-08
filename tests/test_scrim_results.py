import copy
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import test_scrim_queue

from rionnag import config
from rionnag.scrims.scrim_results import ScrimResultLogs, result_embeds, session_embed
from rionnag.scrims.scrim_rivals import basic_match


class ResultTests(unittest.IsolatedAsyncioTestCase):
    def test_renamed_canonical_log_channel_uses_id(self):
        self.guild.id = config.GUILD_ID
        self.channel.name = "Renamed logs"
        self.guild.get_channel = MagicMock(return_value=self.channel)
        self.assertEqual(self.logs.log_channel({"guild_id": config.GUILD_ID}),
                         (self.guild, self.channel))
        self.guild.get_channel.assert_called_once_with(config.SCRIM_LOG_CHANNEL_ID)

    tearDown = test_scrim_queue.QueueTests.tearDown
    click = test_scrim_queue.QueueTests.click
    begin = test_scrim_queue.QueueTests.begin
    interaction = test_scrim_queue.QueueTests.interaction

    def setUp(self):
        test_scrim_queue.QueueTests.setUp(self)
        self.sent = []
        self.guild.filesize_limit = 10_000_000
        self.controller.bot.user = SimpleNamespace(id=1000)

        async def history(**kwargs):
            for message in reversed(self.sent):
                yield message

        async def send(**kwargs):
            message = SimpleNamespace(
                id=5000 + len(self.sent),
                author=self.controller.bot.user,
                embeds=kwargs["embeds"],
                created_at=datetime.now(UTC),
            )

            async def edit(**changes):
                message.embeds = changes["embeds"]
                message.attachments = changes.get("attachments", [])

            message.edit = AsyncMock(side_effect=edit)
            message.delete = AsyncMock()
            self.sent.append(message)
            return message

        async def fetch(mid):
            return next(m for m in self.sent if m.id == mid)

        self.channel = SimpleNamespace(
            id=110,
            name="scrim-logs",
            history=history,
            send=AsyncMock(side_effect=send),
            fetch_message=AsyncMock(side_effect=fetch),
        )
        self.guild.text_channels = [self.channel]
        self.logs = ScrimResultLogs(self.controller)

    async def verified_match(self):
        data = await self.begin()
        mid = data["match_id"]
        await self.controller.game_ended(self.guild, data)
        roster = self.store.match(mid)["roster"]
        payload = {
            "match_uid": "verified",
            "replay_id": "123456789",
            "duration_seconds": 615,
            "timestamp": self.store.match(mid)["ended_at"],
            "winner_camp": 1,
            "map_id": 1288,
            "game_mode_id": 3,
            "game_play_mode_id": 300,
            "draft": [{"banned_hero": 1016}],
            "unknown_future_data": {"secret_stat": 789},
            "teams": [
                {
                    "camp": 1,
                    "is_win": True,
                    "round_score": 2,
                    "players": [
                        {
                            "player_uid": str(p["member_id"]),
                            "name": p["username"],
                            "kills": 23,
                            "deaths": 4,
                            "assists": 8,
                            "damage": 20001,
                            "heroes": [{"hero_id": 1016, "new_hero_stat": 999}],
                        }
                        for p in roster
                    ],
                },
                {
                    "camp": 0,
                    "is_win": False,
                    "round_score": 1,
                    "players": [
                        {
                            "player_uid": "99999",
                            "name": "Enemy",
                            "kills": 10,
                            "deaths": 5,
                            "assists": 3,
                            "unknown_enemy_stat": {"nested_rounds": [11, 22, 33]},
                        }
                    ],
                },
            ],
            "_scrim_source_data": {"responses": [{"data": {"unmodified": {"future": [1, 2, 3]}}}]},
        }
        self.store.snapshot(mid, "details", payload, "verified")
        return self.store.match(mid)

    async def test_archive_and_post_preserve_all_enemy_unknown_fields_and_replay(self):
        match = await self.verified_match()
        await self.logs.tick()
        delivery = self.store.result_delivery(match["id"])
        self.assertTrue(delivery["done"])
        archive = json.loads(Path(delivery["archive_path"]).read_text(encoding="utf-8"))
        self.assertEqual(archive, basic_match(match["data"]))
        self.assertEqual(archive["replay_id"], "123456789")
        self.assertIn("unknown_future_data", archive)
        self.assertNotIn("_scrim_source_data", archive)
        display = "\n".join(str(e.to_dict()) for message in self.sent for e in message.embeds)
        self.assertIn("Enemy", display)
        for hidden in ("123456789", "nested_rounds", "new_hero_stat", "secret_stat"):
            self.assertNotIn(hidden, display)
        self.assertEqual(len(self.sent[0].embeds), 3)
        self.assertEqual(self.sent[0].embeds[0].color.value, 0x57F287)
        self.assertEqual(self.sent[0].embeds[1].color.value, 0x57F287)
        self.assertEqual(self.sent[0].embeds[2].color.value, 0xE74C3C)
        self.assertTrue(all("file" not in call.kwargs for call in self.channel.send.call_args_list))
        self.assertEqual(self.store.pending_results(), [])
        await ScrimResultLogs(self.controller).tick()
        self.assertEqual(len(self.sent), len(delivery["message_ids"]))
        for message in self.sent:
            self.assertLessEqual(sum(len(e) for e in message.embeds), 6000)
            self.assertLessEqual(len(message.embeds), 10)
            for embed in message.embeds:
                self.assertLessEqual(len(embed.fields), 25)
                self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))

    async def test_loss_color_is_from_real_roster_and_missing_outcome_is_neutral(self):
        match = await self.verified_match()
        session = self.store.get(match["session_id"])
        match["data"]["teams"][0]["is_win"] = False
        self.assertEqual(result_embeds(match, session)[0].color.value, 0xE74C3C)
        del match["data"]["teams"][0]["is_win"]
        match["data"]["winner_camp"] = None
        self.assertEqual(result_embeds(match, session)[0].color.value, 0xF1C40F)

    async def test_discord_failure_keeps_archive_and_retry_recovers_sent_pages(self):
        match = await self.verified_match()
        send = self.channel.send.side_effect

        async def interrupted(**kwargs):
            # Discord receives the message, but the connection drops before acknowledgment.
            await send(**kwargs)
            raise OSError("connection interrupted")

        self.channel.send.side_effect = interrupted
        with self.assertLogs("rionnag.scrims.scrim_results", level="ERROR"):
            await self.logs.tick()
        state = self.store.result_delivery(match["id"])
        self.assertTrue(Path(state["archive_path"]).exists())
        self.assertFalse(state.get("done", False))
        self.assertGreater(state["next_at"], 0)
        self.channel.send.side_effect = send
        state["next_at"] = 0
        self.store.save_result_delivery(match["id"], state)
        await ScrimResultLogs(self.controller).tick()
        state = self.store.result_delivery(match["id"])
        self.assertTrue(state["done"])
        self.assertEqual(len(state["message_ids"]), len(self.sent))
        self.assertEqual(len(set(state["message_ids"])), len(self.sent))

    async def test_large_extra_stats_stay_local_and_do_not_mutate_payload(self):
        match = await self.verified_match()
        original = "abcdefghijklmnopqrstuvwxyz" * 300
        match["data"]["teams"][1]["players"][0]["giant_stat"] = original
        before = json.dumps(match, sort_keys=True)
        embeds = result_embeds(match, self.store.get(match["session_id"]))
        self.assertEqual(json.dumps(match, sort_keys=True), before)
        self.assertTrue(all(len(f.value) <= 1024 for e in embeds for f in e.fields))
        displayed = "".join(f.value for e in embeds for f in e.fields)
        self.assertNotIn(original, displayed)

    async def test_upgrade_edits_existing_results_and_removes_files_and_extra_pages(self):
        match = await self.verified_match()
        await self.channel.send(embeds=result_embeds(match, self.store.get(match["session_id"])))
        await self.channel.send(embeds=result_embeds(match, self.store.get(match["session_id"])))
        self.store.save_result_delivery(
            match["id"],
            {
                "done": True,
                "message_ids": [m.id for m in self.sent],
                "channel_id": self.channel.id,
                "format_version": 6,
            },
        )
        await self.logs.tick()
        self.assertTrue(self.store.result_delivery(match["id"])["done"])
        self.assertEqual(self.channel.send.await_count, 2)
        self.sent[0].edit.assert_awaited_once()
        self.assertEqual(self.sent[0].edit.call_args.kwargs["attachments"], [])
        self.sent[1].delete.assert_awaited_once()
        self.assertEqual(self.store.result_delivery(match["id"])["message_ids"], [self.sent[0].id])

    async def test_player_fields_group_roles_and_show_requested_stats_bans_map_and_mode(self):
        match = await self.verified_match()
        heroes = (1016, 1024, 1011, 1031, 1059, 1018)
        for player, hero in zip(match["data"]["teams"][0]["players"], heroes, strict=True):
            player.update(top_hero_id=hero, accuracy=55.5, final_hits=12, blocked=2500, healing=5000)
            player["heroes"] = [{"hero_id": hero}]
        match["data"]["draft"] = [
            {"hero_id": 1016, "is_pick": False},
            {"hero_id": 1011, "is_pick": True},
        ]
        embeds = result_embeds(match, self.store.get(match["session_id"]))
        self.assertEqual(
            [e.title for e in embeds],
            [
                "Game 1 · WIN",
                "Team Rionnag · WIN",
                "Opponent Team · LOSS",
            ],
        )
        self.assertEqual([f.name for f in embeds[0].fields], ["Duration", "Map", "Game mode", "Score"])
        self.assertIsNone(embeds[0].description)
        self.assertIsNone(embeds[0].footer.text)
        self.assertEqual(embeds[0].fields[0].value, "10:15")
        self.assertEqual(embeds[0].fields[1].value, "Hell's Heaven")
        self.assertEqual(embeds[0].fields[2].value, "Domination")
        self.assertEqual(embeds[0].fields[3].value, "2 : 1")
        self.assertIsNone(embeds[0].thumbnail.url)
        self.assertEqual(
            embeds[0].image.url, "https://rivalstracker.com/images/Map/img_map_hydracharterisbase.png"
        )
        table = "\n".join(f.value for f in embeds[1].fields[:-1])
        for key in ("12F", "20k ⚔️", "2.5k 🛡️", "5k 💚", "55.5% 🎯", "23/4/8"):
            self.assertIn(key, table)
        # The shuffled input becomes two tanks, two DPS, then two supports.
        hero_order = ("Hulk", "Doctor Strange", "Hela", "Elsa Bloodstone", "Loki", "Luna Snow")
        positions = [table.index(name) for name in hero_order]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(embeds[1].fields[-1].value, "Loki")
        self.assertEqual(len(embeds[1].fields), 7)
        self.assertTrue(all(not f.inline for f in embeds[1].fields))

    async def test_provider_labels_unknown_map_and_percent_accuracy_remain_honest(self):
        match = await self.verified_match()
        match["data"].update(
            map_id=999999,
            map_name="New Map",
            map_mode_name="New Objective",
            map_thumbnail="https://example.com/map.png",
        )
        player = match["data"]["teams"][0]["players"][0]
        player.update(is_mvp=True, accuracy=0.1, accuracy_percent=10.0)
        embeds = result_embeds(match, self.store.get(match["session_id"]))
        self.assertEqual(embeds[0].fields[1].value, "New Map")
        self.assertEqual(embeds[0].fields[2].value, "New Objective")
        self.assertEqual(embeds[0].image.url, "https://example.com/map.png")
        self.assertTrue(embeds[1].fields[0].value.startswith("**★ <@"))
        self.assertIn("10.0% 🎯", embeds[1].fields[0].value)
        del match["data"]["map_name"]
        del match["data"]["map_mode_name"]
        del match["data"]["map_thumbnail"]
        embeds = result_embeds(match, self.store.get(match["session_id"]))
        self.assertEqual(embeds[0].fields[1].value, "Map 999999")
        self.assertEqual(embeds[0].fields[2].value, "Unknown")
        self.assertIsNone(embeds[0].image.url)

    def test_sdk_reference_metadata_is_removed_without_losing_match_values(self):
        payload = {
            "map": {
                "id": 1288,
                "name": "Hell's Heaven",
                "is_known": True,
                "source": "catalog",
                "location": "Hydra Charteris Base",
            },
            "unknown_stat": {"source": 7, "value": 23},
        }
        clean = basic_match(payload)
        self.assertEqual(
            clean["map"], {"id": 1288, "name": "Hell's Heaven", "location": "Hydra Charteris Base"}
        )
        self.assertEqual(clean["unknown_stat"], payload["unknown_stat"])

    async def test_custom_stat_emojis_are_used_for_both_teams_with_unusable_fallback(self):
        match = await self.verified_match()
        icons = []
        for index, stat in enumerate(("damage", "blocked", "healing", "accuracy"), 1):
            emoji = MagicMock()
            emoji.name = f"mr_{stat}"
            emoji.__str__.return_value = f"<:mr_{stat}:{index}>"
            emoji.is_usable.return_value = True
            icons.append(emoji)
        self.guild.emojis = icons
        await self.logs.tick()
        embeds = self.sent[0].embeds
        for embed in embeds[1:]:
            for value in (str(icon) for icon in icons):
                self.assertIn(value, embed.fields[0].value)
            for old in ("⚔️", "🛡️", "💚", "🎯"):
                self.assertNotIn(old, embed.fields[0].value)
        icons[0].is_usable.return_value = False
        fallback = result_embeds(match, self.store.get(match["session_id"]), self.guild)
        self.assertIn("⚔️", fallback[1].fields[0].value)
        self.assertNotIn(str(icons[0]), fallback[1].fields[0].value)

    async def test_all_heroes_keep_api_order_and_only_our_real_roster_gets_mentions(self):
        match = await self.verified_match()
        player = match["data"]["teams"][0]["players"][0]
        player.update(top_hero_id=1011, top_hero_name="Hulk", is_mvp=True)
        player["heroes"] = [
            {"hero_id": 1016, "play_time": 5},
            {"hero_id": 1011, "play_time": 500},
            {"hero_id": 1016, "play_time": 10},
        ]
        person = match["roster"][0]
        person["uid"] = player["player_uid"]
        player["name"] = "Renamed in game"
        enemy = match["data"]["teams"][1]["players"][0]
        # Matching an ID outside our team must never mention a roster member.
        enemy.update(player_uid=player["player_uid"], top_hero_name="The Hood", is_svp=True)
        enemy["heroes"] = [{"hero_id": 1027}, {"hero_id": 1035}]
        before = json.dumps(match, sort_keys=True)
        embeds = result_embeds(match, self.store.get(match["session_id"]))
        our_field = next(f for f in embeds[1].fields if "Loki, Hulk, Loki" in f.value)
        self.assertEqual(our_field.name, "\u200b")
        self.assertTrue(our_field.value.startswith(f"**★ <@{person['member_id']}>**\n"))
        self.assertNotIn("Renamed in game", our_field.value)
        self.assertTrue(embeds[2].fields[0].value.startswith("Groot, Venom |"))
        self.assertEqual(embeds[2].fields[0].name, "★ `Enemy`")
        self.assertNotIn("<@", embeds[2].fields[0].value)
        self.assertNotIn("[", our_field.value)
        self.assertNotIn("]", our_field.value)
        self.assertEqual(json.dumps(match, sort_keys=True), before)
        person["simulated"] = True
        embeds = result_embeds(match, self.store.get(match["session_id"]))
        value = next(f.value for f in embeds[1].fields if "Loki, Hulk, Loki" in f.value)
        self.assertNotIn("<@", value)

    async def test_session_awards_combine_mvp_svp_and_include_all_ties_and_past_starters(self):
        first = await self.verified_match()
        session = self.store.get(first["session_id"])
        players = first["data"]["teams"][0]["players"]
        first_person, second_person = first["roster"][:2]
        players[0]["is_mvp"] = True
        players[1]["is_svp"] = True
        # Neither duplicate provider rows, opponents nor simulated fillers win session MVP.
        players.append(copy.deepcopy(players[0]))
        players[2]["is_mvp"] = players[2]["is_svp"] = True
        first["roster"][2]["simulated"] = True
        first["data"]["teams"][1]["players"][0].update(is_mvp=True, is_svp=True)
        second = copy.deepcopy(first)
        second.update(id=first["id"] + 1, number=2, external_id="verified-two")
        second["data"]["teams"][0]["is_win"] = False
        second["data"]["teams"][0]["players"] = players[:6]
        second["data"]["teams"][0]["players"][0] = copy.deepcopy(players[0])
        second["data"]["teams"][0]["players"][1] = copy.deepcopy(players[1])
        second["data"]["teams"][0]["players"][0].update(is_mvp=False, is_svp=True)
        second["data"]["teams"][0]["players"][1].update(is_mvp=True, is_svp=False)
        # The session's current roster does not control awards from recorded game rosters.
        session["roster"].pop(first_person["member_id"])
        aborted = copy.deepcopy(second)
        aborted["status"] = "aborted"
        embed = session_embed(session, [first, second, aborted])
        self.assertEqual(embed.title, "Session ended · 1–1")
        self.assertEqual(embed.fields[0].value, "1–1")
        self.assertEqual(embed.fields[1].name, "MVP(s)")
        self.assertEqual(
            embed.fields[1].value, f"<@{first_person['member_id']}>, <@{second_person['member_id']}>"
        )
        players[0]["is_svp"] = True
        self.assertEqual(
            session_embed(session, [first, second]).fields[1].value, f"<@{first_person['member_id']}>"
        )
        second["data"]["teams"][0]["players"][1]["is_mvp"] = False
        self.assertEqual(
            session_embed(session, [first, second]).fields[1].value, f"<@{first_person['member_id']}>"
        )

    async def test_ended_session_posts_after_games_and_survives_restart_without_duplicates(self):
        match = await self.verified_match()
        session = self.store.get(match["session_id"])
        self.assertEqual(self.store.pending_session_results(), [])
        self.store.end(session, None)
        await self.logs.tick()
        self.assertEqual(len(self.sent[0].embeds), 3)
        await self.logs.tick()
        state = self.store.session_delivery(session["id"])
        self.assertTrue(state["done"])
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sent[-1].embeds[0].title, "Session ended · 1–0")
        self.assertEqual(self.sent[-1].embeds[0].fields[1].value, "None recorded")
        self.assertEqual(self.channel.send.call_args.kwargs["allowed_mentions"].to_dict()["parse"], [])
        await ScrimResultLogs(self.controller).tick()
        self.assertEqual(len(self.sent), 2)

    async def test_session_with_no_games_does_not_post_separator(self):
        session = await self.begin(start=False)
        self.store.end(session, None)
        await self.logs.tick()
        self.assertEqual(self.sent, [])
        self.assertTrue(self.store.session_delivery(session["id"])["done"])

    async def test_late_result_replaces_unverified_summary_after_game_log(self):
        session = await self.begin(start=False)
        empty = session_embed(session, [])
        self.assertEqual(empty.title, "Session ended · 0–0")
        self.assertEqual(empty.fields[1].value, "None recorded")
        self.store.start(session, None)
        mid = session["match_id"]
        self.store.finish(session, None)
        self.store.end(session, None)
        await self.logs.tick()
        divider = self.sent[0]
        self.assertEqual(divider.embeds[0].fields[-1].name, "Unverified games")
        person = self.store.match(mid)["roster"][0]
        payload = {
            "teams": [
                {
                    "is_win": False,
                    "players": [
                        {"name": p["username"], "is_svp": p["member_id"] == person["member_id"]}
                        for p in self.store.match(mid)["roster"]
                    ],
                }
            ]
        }
        self.store.snapshot(mid, "details", payload, "late-result")
        self.assertFalse(self.store.session_delivery(session["id"])["done"])
        await self.logs.tick()
        await self.logs.tick()
        self.assertEqual(len(self.sent), 3)
        divider.delete.assert_awaited_once()
        updated = self.sent[-1]
        self.assertEqual(self.store.session_delivery(session["id"])["message_id"], updated.id)
        self.assertGreater(updated.id, self.sent[-2].id)
        self.assertEqual(updated.embeds[0].title, "Session ended · 0–1")
        self.assertEqual(updated.embeds[0].fields[1].value, f"<@{person['member_id']}>")
        self.assertNotIn("Unverified games", [f.name for f in updated.embeds[0].fields])
        await self.logs.tick()
        self.assertEqual(len(self.sent), 3)

    async def test_aborted_session_does_not_post_summary(self):
        session = await self.begin(start=False)
        self.store.start(session, None)
        self.store.finish(session, None, aborted=True)
        self.store.end(session, None)
        await self.logs.tick()
        self.assertTrue(self.store.session_delivery(session["id"])["done"])
        self.assertEqual(self.sent, [])

    async def test_session_delivery_recovers_lost_send_acknowledgment(self):
        match = await self.verified_match()
        await self.logs.tick()
        session = self.store.get(match["session_id"])
        self.store.end(session, None)
        send = self.channel.send.side_effect

        async def interrupted(**kwargs):
            await send(**kwargs)
            raise OSError("connection interrupted")

        self.channel.send.side_effect = interrupted
        with self.assertLogs("rionnag.scrims.scrim_results", level="ERROR"):
            await self.logs.tick()
        state = self.store.session_delivery(session["id"])
        self.assertFalse(state.get("done", False))
        state["next_at"] = 0
        self.store.save_session_delivery(session["id"], state)
        self.channel.send.side_effect = send
        await ScrimResultLogs(self.controller).tick()
        self.assertTrue(self.store.session_delivery(session["id"])["done"])
        self.assertEqual(len(self.sent), 2)
