import unittest
from datetime import datetime

from rionnag.services.scrim_availability import same_scrim_day
from rionnag.services.scrim_opportunities import (
    covers_interval,
    match_offer,
    offer_interval,
)
from rionnag.ui.scrim_opportunities import matched_offer_embed


def instant(text):
    return int(datetime.fromisoformat(text).timestamp())


def answers(zone="Eastern Time (ET)", day="Monday", start=20, end=22):
    return {"time_zone": zone, "availability_days": {day: {"start": start, "end": end}},
            "username": "Example", "preferred_role_1": "Tank", "preferred_role_2": "DPS"}


def offer(start="2027-06-08T00:00:00Z", end=None):
    return {"Start_Time_timestamp": start, "End_Time_timestamp": end,
            "rank_minimum": "Diamond", "rank_maximum": "Grandmaster",
            "messageURL": "https://discord.com/channels/1/2/3"}


class MatchingTests(unittest.TestCase):
    def test_official_day_uses_server_calendar_date(self):
        official = instant("2027-06-08T01:00:00+00:00")
        self.assertTrue(same_scrim_day(instant("2027-06-08T03:30:00+00:00"), official))
        self.assertFalse(same_scrim_day(instant("2027-06-08T04:00:00+00:00"), official))

    def test_minimum_hour_and_full_advertised_duration(self):
        start, end = offer_interval(offer())
        self.assertEqual(end - start, 3600)
        self.assertTrue(covers_interval(answers(end=21), start, end))
        longer = offer(end="2027-06-08T02:00:00Z")
        self.assertEqual(match_offer(longer, [{"member_id": 1, "answers": answers(end=21)}], start-1), [])
        self.assertIsNone(offer_interval(offer(end="2027-06-08T00:30:00Z")))
        self.assertIsNone(offer_interval(offer(end="invalid")))

    def test_four_real_players_not_partial_or_rotating_coverage(self):
        players = [{"member_id": n, "answers": answers(end=21)} for n in range(1, 5)]
        start, _ = offer_interval(offer())
        self.assertEqual(match_offer(offer(), players, start-1), [1, 2, 3, 4])
        players[-1]["answers"] = answers(start=21)
        self.assertEqual(match_offer(offer(), players, start-1), [1, 2, 3])
        self.assertEqual(match_offer(offer(), players + [players[0]], start-1), [1, 2, 3])
        self.assertEqual(match_offer(offer(), players, start), [1, 2, 3])
        self.assertEqual(match_offer(offer(), players, start+3600), [])

    def test_cross_zone_weekday_and_overnight(self):
        start, end = offer_interval(offer("2027-06-08T06:00:00Z"))
        self.assertTrue(covers_interval(answers("Pacific Time (PT)", start=23, end=1), start, end))
        self.assertFalse(covers_interval(answers("Eastern Time (ET)", start=23, end=1), start, end))
        start, end = offer_interval(offer())
        self.assertTrue(covers_interval(answers("Central Time (CT)", start=19, end=20), start, end))
        self.assertFalse(covers_interval(answers("Central Time (CT)", start=20, end=21), start, end))

    def test_contiguous_days_cover_midnight_but_gap_does_not(self):
        a = answers(start=23, end=24)
        a["availability_days"]["Tuesday"] = {"start": 0, "end": 2}
        start = instant("2027-06-08T03:30:00+00:00")
        self.assertTrue(covers_interval(a, start, start+3600))
        a["availability_days"]["Tuesday"]["start"] = 1
        self.assertFalse(covers_interval(a, start, start+3600))

    def test_separate_blocks_have_breaks_and_next_day_spill(self):
        a = answers(start=9, end=12)
        a["availability_days"]["Monday"] = [
            {"start": 9, "end": 12}, {"start": 15, "end": 25}
        ]
        a["availability_days"]["Tuesday"] = [{"start": 2, "end": 17}]
        def interval(hour):
            start = instant(f"2027-06-07T{hour:02d}:00:00-04:00")
            return covers_interval(a, start, start + 3600)
        self.assertTrue(interval(9))
        self.assertFalse(interval(13))
        self.assertTrue(interval(23))
        self.assertTrue(covers_interval(
            a, instant("2027-06-08T00:00:00-04:00"),
            instant("2027-06-08T01:00:00-04:00")
        ))
        self.assertFalse(covers_interval(
            a, instant("2027-06-08T01:00:00-04:00"),
            instant("2027-06-08T02:00:00-04:00")
        ))
        self.assertTrue(covers_interval(
            a, instant("2027-06-08T02:00:00-04:00"),
            instant("2027-06-08T03:00:00-04:00")
        ))

    def test_opportunity_matches_second_block_but_not_break(self):
        players = []
        for mid in range(1, 7):
            saved = answers()
            saved["availability_days"]["Monday"] = [
                {"start": 18, "end": 19}, {"start": 20, "end": 22},
            ]
            players.append({"member_id": mid, "answers": saved})
        now = instant("2027-06-07T17:00:00-04:00")
        second_block = offer("2027-06-08T00:00:00Z", "2027-06-08T02:00:00Z")
        across_break = offer("2027-06-07T22:00:00Z", "2027-06-08T00:00:00Z")
        self.assertEqual(match_offer(second_block, players, now), list(range(1, 7)))
        self.assertEqual(match_offer(across_break, players, now), [])

    def test_dst_uses_scrim_date_and_conservative_transition_boundaries(self):
        summer = instant("2027-06-08T00:00:00+00:00")
        winter = instant("2027-01-05T01:00:00+00:00")
        self.assertTrue(covers_interval(answers(), summer, summer+3600))
        self.assertTrue(covers_interval(answers(), winter, winter+3600))
        self.assertFalse(covers_interval(answers(), winter-3600, winter))
        spring = instant("2027-03-14T07:00:00+00:00")
        self.assertFalse(covers_interval(answers(day="Sunday", start=2, end=4), spring, spring+3600))
        fall_first = instant("2027-11-07T05:00:00+00:00")
        self.assertFalse(covers_interval(answers(day="Sunday", start=1, end=3), fall_first, fall_first+3600))
        self.assertTrue(covers_interval(
            answers(day="Sunday", start=1, end=3), fall_first+3600, fall_first+7200
        ))

    def test_missing_or_invalid_schedule_is_not_assumed_available(self):
        start, end = offer_interval(offer())
        for a in ({}, {"time_zone": "Unknown", "availability_days": {"Monday": {"start": 20, "end": 22}}},
                  {"time_zone": "Eastern Time (ET)", "availability_days": "everyday"},
                  answers(start=True), answers(end=49)):
            self.assertFalse(covers_interval(a, start, end))

    def test_embed_shows_date_source_and_assumed_hour_without_private_answers(self):
        embed = matched_offer_embed(offer(), [1, 2, 3, 4], "3:0")
        self.assertIn("End time not advertised", embed.description)
        self.assertNotIn("checked 1 hour", embed.description)
        self.assertIn(":F>", embed.description)
        self.assertIn("Available players (4)", {field.name for field in embed.fields})
        self.assertNotIn("Based on saved availability", embed.description)
        self.assertEqual(embed.url, offer()["messageURL"])
        self.assertEqual(embed.footer.text, "Scrim finder • 3:0")
