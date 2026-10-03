import unittest

from rionnag.scrims.scrim_offer_rules import (
    canonical_rank,
    eastern_local_times,
    offer_lifecycle,
    timestamp,
    validate_offer,
)


class StrictOfferTests(unittest.TestCase):
    def test_informal_est_uses_eastern_daylight_time(self):
        offer = {"Start_Time_timestamp": "2026-10-04T01:00:00Z", "End_Time_timestamp": "2026-10-04T03:00:00Z"}
        source = {"content": "LFS GM 8-10pm EST tonight"}
        fixed = eastern_local_times(offer, source)
        self.assertEqual(fixed["Start_Time_timestamp"], "2026-10-04T00:00:00Z")
        self.assertEqual(fixed["End_Time_timestamp"], "2026-10-04T02:00:00Z")
        self.assertEqual(eastern_local_times(fixed, source), fixed)

    def test_winter_and_explicit_fixed_offset_are_preserved(self):
        winter = {"Start_Time_timestamp": "2026-12-04T01:00:00Z"}
        self.assertEqual(eastern_local_times(winter, {"content": "GM 8pm EST"}), winter)
        fixed = {"Start_Time_timestamp": "2026-10-04T01:00:00Z"}
        self.assertEqual(eastern_local_times(fixed, {"content": "GM 8pm EST UTC-5"}), fixed)
        self.assertEqual(eastern_local_times(fixed, {"content": "GM EST <t:1791075600:t>"}), fixed)

    def offer(self, **changes):
        return {
            "rank_minimum": "Diamond",
            "rank_maximum": "Grandmaster",
            "rank_text": "GM to Dia",
            "time_text": "<t:1800000000:F>",
            "Start_Time_timestamp": "2027-01-15T08:00:00+00:00",
            "End_Time_timestamp": None,
            "timezone": None,
            **changes,
        }

    def validate(self, **changes):
        return validate_offer(self.offer(**changes), {"content": "GM to Dia <t:1800000000:F>"})

    def test_shorthand_and_discord_timestamp_are_normalized(self):
        result = self.validate()
        self.assertEqual(result["rank_minimum"], "Diamond")
        self.assertEqual(timestamp(result["Start_Time_timestamp"]), 1800000000)
        self.assertEqual(canonical_rank("gm3"), "Grandmaster III")
        self.assertIsNone(canonical_rank("Masters"))

    def test_missing_or_invalid_required_values_are_rejected(self):
        for changes in (
            {"rank_minimum": None},
            {"rank_maximum": "Masters"},
            {"Start_Time_timestamp": None},
            {"Start_Time_timestamp": "2027-01-15T08:00:00"},
            {"rank_minimum": "unranked"},
            {"Start_Time_timestamp": "2027-01-16T08:00:00+00:00"},
        ):
            self.assertIsNone(self.validate(**changes))

    def test_overwatch_ranks_are_not_rivals_ranks(self):
        source = {"content": "GM to Dia Overwatch ranks, not ranked in Rivals <t:1800000000:F>"}
        self.assertIsNone(validate_offer(self.offer(), source))

    def test_end_must_be_after_start(self):
        self.assertIsNone(self.validate(End_Time_timestamp="2027-01-15T07:00:00+00:00"))
        source = {"content": "GM to Dia <t:1800000000:F> to <t:1800003600:F>"}
        self.assertIsNotNone(
            validate_offer(
                self.offer(End_Time_timestamp="2027-01-15T09:00:00+00:00"),
                source,
            )
        )

    def test_wall_clock_without_timezone_is_rejected(self):
        offer = self.offer()
        self.assertIsNone(validate_offer(offer, {"content": "GM to Dia tomorrow 8pm"}))

    def test_lifecycle_and_midnight_expiry(self):
        offer = {
            "Start_Time_timestamp": "2026-10-03T18:00:00-04:00",
            "End_Time_timestamp": "2026-10-03T20:00:00-04:00",
            "timezone": "America/New_York",
        }
        start, end = timestamp(offer["Start_Time_timestamp"]), timestamp(offer["End_Time_timestamp"])
        self.assertEqual(offer_lifecycle(offer, start - 1)[0], "upcoming")
        self.assertEqual(offer_lifecycle(offer, start)[0], "started")
        self.assertEqual(offer_lifecycle(offer, end)[0], "finished")
        self.assertEqual(offer_lifecycle(offer, end)[1], timestamp("2026-10-04T00:00:00-04:00"))
        offer["End_Time_timestamp"] = None
        self.assertEqual(offer_lifecycle(offer, end)[0], "started")
