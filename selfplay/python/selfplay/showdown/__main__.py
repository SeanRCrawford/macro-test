"""Play the bot on Pokemon Showdown: challenge someone, or accept challenges.

    # Challenge a user to a game (your bot account and its team):
    python -m selfplay.showdown runs/ppo3/model.pt --team my_team.txt --user MyBot --password ... \\
        --challenge mikumiku37

--team is a paste file, or the name of a team the bot trains with: in
data/teams, data/my_teams or the tournament corpus (selfplay/data/corpus),
written as in the team ratings ("Big 6 Chople", "teams/Big 6 Chople" or a
corpus name; quote names with spaces). A set without an EVs line gets the
usage spread the engine trained with, and that is what is uploaded. A team
Showdown would refuse, or with something the engine doesn't implement,
stops here.

After each battle the bot saves its replay on the server and writes the
link to --log-dir/replays.txt: unlisted by default (anyone with the link
can watch it), --replays public to list it, --replays off for none.

    # Wait for challenges from these users (or anyone, with --accept alone):
    python -m selfplay.showdown runs/ppo3/model.pt --team my_team.txt --user MyBot --password ... \\
        --accept YourName mikumiku37 --games 10

The bot plays with the tree search by default (--budget value evaluations
per turn; about 2-5 s on a GPU), or straight from the network with
--policy. It accepts Open Team Sheets (--no-ots to decline) and waits a
little at team preview for the opponent's sheet. Every decision is printed
with the bot's win estimate and its top options; each battle's log is
saved under --log-dir. The password can also come from the
SHOWDOWN_PASSWORD environment variable.

Against a local server (node pokemon-showdown start --no-security), use
--server ws://localhost:8000/showdown/websocket --local: no login needed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import torch

def main():
    from selfplay.evaluate import load
    from selfplay.mcts import TreeConfig
    from selfplay.showdown.bot import Bot
    from selfplay.showdown.client import FORMAT, MAIN_SERVER, Client

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("model")
    p.add_argument("--team", required=True,
                   help="the bot's team: a paste file, or a team's name (data/teams, data/my_teams, the corpus)")
    p.add_argument("--user", required=True, help="the bot's Showdown account")
    p.add_argument("--password", default=None, help="its password (or SHOWDOWN_PASSWORD)")
    p.add_argument("--challenge", nargs="+", default=[], help="users to challenge, in turn")
    p.add_argument("--accept", nargs="*", default=None, help="accept challenges from these users (none listed: anyone)")
    p.add_argument("--games", type=int, default=1,
                   help="matches to play before exiting (a best-of-3 series counts as one)")
    p.add_argument("--format", default=FORMAT)
    p.add_argument("--server", default=MAIN_SERVER)
    p.add_argument("--local", action="store_true", help="a local server without login (--no-security)")
    p.add_argument("--policy", action="store_true", help="play the network's policy, no search")
    p.add_argument("--value-model", default=None,
                   help="the search values positions with this model.pt (e.g. from search_train)")
    p.add_argument("--budget", type=int, default=2000, help="search: value evaluations per turn")
    p.add_argument("--max-seconds", type=float, default=20.0,
                   help="search: the longest a decision may take (less when the battle timer is short)")
    p.add_argument("--sample", action="store_true",
                   help="sample the policy / the search's mix (default: its most likely action)")
    p.add_argument("--no-ots", action="store_true", help="decline Open Team Sheets")
    p.add_argument("--ots-wait", type=float, default=20.0, help="seconds to wait for the opponent's sheet")
    p.add_argument("--timer", action="store_true", help="turn the battle timer on")
    p.add_argument("--log-dir", default="runs/showdown")
    p.add_argument("--replays", choices=["unlisted", "public", "off"], default="unlisted",
                   help="save each battle's replay on the server (links in LOG_DIR/replays.txt)")
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    # A Windows console (or output sent to a file) may not take every
    # character in a name or a chat line: show those as "?", don't stop.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    if not a.challenge and a.accept is None:
        p.error("give --challenge USER or --accept [USER ...]")

    from selfplay.showdown.team import check_team, complete_paste, find_team
    path = find_team(a.team)
    team, notes = complete_paste(path.read_text(encoding="utf-8-sig"))
    print(f"team: {path}")
    for note in notes:
        print("  " + note)
    errors, warnings = check_team(team)
    for w in warnings:
        print("  warning: " + w)
    if errors:
        raise SystemExit("this team can't be played:\n  " + "\n  ".join(errors))
    torch.set_grad_enabled(False)
    model = load(a.model, a.device)
    value_model = load(a.value_model, a.device) if a.value_model else None
    tree = None if a.policy else TreeConfig(budget=a.budget)
    seeds = iter(range(a.seed, a.seed + 1_000_000))

    def make_bot():
        cfg = None if tree is None else TreeConfig(**vars(tree))
        return Bot(model, team, cfg, sample=a.sample, seed=next(seeds), value_model=value_model)

    client = Client(make_bot, team, a.user, a.password or os.environ.get("SHOWDOWN_PASSWORD"),
                    server=a.server, fmt=a.format, challenge=a.challenge, accept=a.accept,
                    games=a.games, ots=not a.no_ots, timer=a.timer, log_dir=a.log_dir, local=a.local,
                    ots_wait=a.ots_wait, replays=a.replays, max_seconds=a.max_seconds)
    asyncio.run(client.run())
    r, s = client.record, client.series_record
    print(f"done: games {r['win']} won, {r['loss']} lost, {r['tie']} tied"
          + (f"; series {s['win']}-{s['loss']}-{s['tie']}" if sum(s.values()) else ""))


if __name__ == "__main__":
    main()
