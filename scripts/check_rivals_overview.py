"""Read-only public-provider smoke check; prints availability, never account details."""

import argparse

from rionnag.integrations.rivals import fetch_player_overview, profile_overview


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("account", help="Public account name or numeric UID")
    args = parser.parse_args()
    player = fetch_player_overview(args.account)
    fields, _ = profile_overview(player)
    print("Season win rate available:", player["win_rate"] is not None)
    print("Hero history available:", bool(player["match_hero_rates"]))
    print("Hero leaderboard positions available:", "`#" in fields["competitive_heroes"])
    print("Class history available:", bool(player["match_class_rates"]))


if __name__ == "__main__":
    main()
