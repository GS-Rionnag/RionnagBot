import unittest

from rionnag.scrims.scrim_feed_view import offer_embed


class PreviewTests(unittest.TestCase):
    def test_compact_fields_and_short_discord_times(self):
        offer = {
            "rank_minimum": "Diamond",
            "rank_maximum": "Grandmaster",
            "Start_Time_timestamp": "2027-01-15T08:00:00Z",
            "End_Time_timestamp": "2027-01-15T09:00:00Z",
            "authorID": "4",
            "messageURL": "https://discord.com/channels/1/2/3",
            "messageContent": "scrim post",
        }
        embed = offer_embed(offer, 0, 1)
        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields["Start time"], "<t:1800000000:t>")
        self.assertEqual(fields["End time"], "<t:1800003600:t>")
        self.assertEqual(fields["Posted by"], "<@4>")
        self.assertEqual(fields["Rank range"], "Diamond to Grandmaster")
        self.assertEqual(embed.url, offer["messageURL"])
        self.assertNotIn("Platforms", fields)

    def test_no_end_time_and_long_content(self):
        embed = offer_embed({"messageContent": "x" * 10000}, 0, 1)
        self.assertLessEqual(len(embed), 6000)
        self.assertNotIn("End time", {field.name for field in embed.fields})
        self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))
