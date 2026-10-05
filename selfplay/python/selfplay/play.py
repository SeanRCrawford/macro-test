"""Play against the bot in the terminal.

You are side 0 (p1), the bot side 1. Pick teams by name or by a unique part
of it (`python -m selfplay.play MODEL --list` lists them). Each turn you
choose each active Pokemon's action from a numbered list; type `h` for a
hint (the search's mix for your side and its win estimate) or `q` to quit.

    python -m selfplay.play runs/search/model.pt --you "my_teams/pseudo" --bot "Champion"
    python -m selfplay.play runs/search/model.pt --bot-plays policy   # no search: faster, weaker
"""
from __future__ import annotations

import argparse
import random
import sys

import numpy as np
import torch

from selfplay.env import DECISION_PREVIEW, DECISION_SLOTS, SelfPlayEnv
from selfplay.model import preview_orders
from selfplay.search import Search, SearchConfig

SLOT, N_MOVE, N_SWITCH = 47, 40, 6
TARGETS = [0, 1, 2, -1, -2]


# --- describing things -----------------------------------------------------

def active(side_view: dict, slot: int) -> dict | None:
    return next((p for p in side_view["pokemon"] if p.get("slot") == slot), None)


def slot_label(view: dict, slot: int, a: int) -> str:
    """One active Pokemon's action, from the actor's own view."""
    me = active(view["you"], slot)
    if a >= N_MOVE + N_SWITCH:
        return "(nothing)"
    if a >= N_MOVE:
        p = next(p for p in view["you"]["pokemon"] if p.get("position") == a - N_MOVE)
        return f"switch to {p['species']}"
    m, t, mega = a // 10, (a // 2) % 5, a % 2
    name = me["moves"][m]["name"] if me else f"move {m + 1}"
    loc = TARGETS[t]
    tgt = ""
    if loc > 0:
        foe = active(view["foe"], loc - 1)
        tgt = f" -> {foe['species'] if foe else 'empty slot'}"
    elif loc < 0:
        ally = active(view["you"], -loc - 1)
        tgt = f" -> {'itself' if -loc - 1 == slot else ally['species'] if ally else 'empty slot'}"
    return f"{'Mega Evolve + ' if mega else ''}{name}{tgt}"


def joint_label(view: dict, a: int) -> str:
    return " / ".join(slot_label(view, s, x) for s, x in enumerate(divmod(a, SLOT))
                      if x < N_MOVE + N_SWITCH)


def mon_line(p: dict, own: bool) -> str:
    if not p.get("seen"):
        return f"{p['species']} (not seen)"
    hp = f"{p['hp']}/{p['maxhp']}" if own else f"{p['hp_pct']:.0f}%"
    bits = [p["species"], hp]
    if p.get("status"):
        bits.append(p["status"].upper())
    boosts = [f"{n}{b:+d}" for n, b in zip(("Atk", "Def", "SpA", "SpD", "Spe", "Acc", "Eva"),
                                           p.get("boosts", [])) if b]
    if boosts:
        bits.append(" ".join(boosts))
    if p.get("item"):
        bits.append(f"@ {p['item']}")
    vol = [v for v in p.get("volatiles", []) if v not in ("choicelock",)]
    if vol:
        bits.append("[" + ", ".join(vol) + "]")
    return "  ".join(bits)


def show(view: dict) -> None:
    field = []
    if view["weather"]:
        field.append(f"{view['weather']} ({view['weather_turns']})")
    if view["terrain"]:
        field.append(f"{view['terrain']} ({view['terrain_turns']})")
    if view["trick_room"]:
        field.append(f"Trick Room ({view['trick_room']})")
    if view["gravity"]:
        field.append(f"Gravity ({view['gravity']})")
    print(f"\n=== Turn {view['turn']} ===" + (f"   field: {', '.join(field)}" if field else ""))
    for who, own in (("foe", False), ("you", True)):
        sv = view[who]
        cond = ", ".join(f"{k} ({v})" for k, v in sv["conditions"].items())
        print(f"{'Bot' if who == 'foe' else 'You'}:" + (f"   [{cond}]" if cond else ""))
        for slot in (0, 1):
            p = active(sv, slot)
            print(f"  {'L' if slot == 0 else 'R'}: " + (mon_line(p, own) if p else "(empty)"))
        bench = [p for p in sv["pokemon"] if p.get("seen") and p.get("slot") is None
                 and (own and p.get("brought") or not own)]
        if bench:
            print("  bench: " + ";  ".join(mon_line(p, own) for p in bench))


def changes(before: dict, after: dict) -> list[str]:
    """What visibly changed between two views (there is no battle log yet)."""
    out = []
    for who, label in (("foe", "Bot's"), ("you", "Your")):
        for a, b in zip(before[who]["pokemon"], after[who]["pokemon"]):
            if not b.get("seen"):
                continue
            name = f"{label} {b['species']}"
            if not a.get("seen"):
                out.append(f"{name} appeared")
            if a.get("species") != b["species"] and a.get("seen"):
                out.append(f"{label} {a['species']} became {b['species']}")
            pa, pb = a.get("hp_pct", 100), b.get("hp_pct", 100)
            if b.get("status") == "fnt" and a.get("status") != "fnt":
                out.append(f"{name} fainted")
            elif pa != pb:
                out.append(f"{name}: {pa:.0f}% -> {pb:.0f}%")
            if b.get("status") not in ("", "fnt", None) and a.get("status") != b.get("status"):
                out.append(f"{name} is now {b['status'].upper()}")
            if a.get("item") and not b.get("item"):
                out.append(f"{name} lost its {a['item']}")
    for k, name in (("weather", "weather"), ("terrain", "terrain")):
        if before[k] != after[k]:
            out.append(f"{name}: {after[k] or 'none'}")
    if bool(before["trick_room"]) != bool(after["trick_room"]):
        out.append("Trick Room " + ("set" if after["trick_room"] else "ended"))
    return out


# --- choosing --------------------------------------------------------------

def ask(prompt: str, n: int, extra: str = "") -> str:
    while True:
        s = input(prompt).strip().lower()
        if s in ("q", "quit"):
            sys.exit(0)
        if s in ("h", "hint") and "h" in extra:
            return "h"
        if s.isdigit() and 1 <= int(s) <= n:
            return s
        print(f"  enter 1-{n}" + (", h for a hint" if "h" in extra else "") + " or q to quit")


def choose_preview(view: dict) -> int:
    print("\nTeam preview. The bot's team (open team sheet):")
    for p in view["foe"]["pokemon"]:
        print(f"   {p['species']} @ {p['item'] or '-'}  ({p['ability']}): "
              + ", ".join(m["name"] for m in p["moves"]))
    print("Your team:")
    for i, p in enumerate(view["you"]["pokemon"]):
        print(f"  {i + 1}. {p['species']} @ {p['item'] or '-'}  ({p['ability']}): "
              + ", ".join(m["name"] for m in p["moves"]))
    orders = [tuple(o) for o in preview_orders().tolist()]
    while True:
        s = input("Bring 4, leads first (e.g. 1 2 5 6), or q: ").strip().lower()
        if s in ("q", "quit"):
            sys.exit(0)
        try:
            pick = tuple(int(x) - 1 for x in s.replace(",", " ").split())
        except ValueError:
            continue
        if pick in orders:
            return orders.index(pick)
        print("  four different numbers from 1 to 6")


def choose_slots(view: dict, mask: np.ndarray, hint) -> int:
    legal = np.flatnonzero(mask)
    pairs = [divmod(int(a), SLOT) for a in legal]
    firsts = sorted({a for a, _ in pairs})
    chosen = []
    for slot in (0, 1):
        opts = sorted({b for a, b in pairs if a == chosen[0]}) if slot == 1 else firsts
        if opts == [N_MOVE + N_SWITCH]:          # this slot has nothing to do
            chosen.append(opts[0])
            continue
        who = active(view["you"], slot)
        print(f"{who['species'] if who else 'Slot ' + 'LR'[slot]}:")
        for i, a in enumerate(opts):
            print(f"  {i + 1}. {slot_label(view, slot, a)}")
        while True:
            s = ask("> ", len(opts), "h")
            if s == "h":
                hint()
                continue
            chosen.append(opts[int(s) - 1])
            break
    return chosen[0] * SLOT + chosen[1]


# --- the game --------------------------------------------------------------

def main():
    from selfplay.evaluate import load
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("model")
    p.add_argument("--you", default=None, help="your team (default: random)")
    p.add_argument("--bot", default=None, help="the bot's team (default: random)")
    p.add_argument("--list", action="store_true", help="list the teams and exit")
    p.add_argument("--bot-plays", choices=("search", "double-oracle", "policy"), default="search")
    p.add_argument("--k", type=int, default=8, help="search: candidates per side")
    p.add_argument("--device", default="cpu")
    p.add_argument("--seed", type=int, default=None)
    a = p.parse_args()

    seed = a.seed if a.seed is not None else random.randrange(1 << 30)
    env = SelfPlayEnv(1, seed=seed, threads=1)
    names = env.team_names()
    if a.list:
        print("\n".join(names))
        return
    rng = random.Random(seed)
    you = env.team_index(a.you) if a.you else rng.randrange(len(names))
    bot = env.team_index(a.bot) if a.bot else rng.randrange(len(names))
    env.set_matchups([(you, bot)])
    print(f"You: {names[you]}\nBot: {names[bot]}  (plays: {a.bot_plays})")

    model = load(a.model, a.device)
    cfg = SearchConfig(k=a.k, double_oracle=a.bot_plays == "double-oracle", sample=True)
    search = Search(model, cfg, seed)
    from selfplay.train import act, to_tensors

    def hint():
        r = search.run(env, [0])[0]
        v = env.view(0, 0)
        print(f"  search: your win chance about {100 * (r['value'] + 1) / 2:.0f}%. Its mix for you:")
        for cand, w in sorted(zip(r["candidates"][0], r["row"]), key=lambda t: -t[1]):
            if w >= 0.01:
                print(f"    {100 * w:4.0f}%  {joint_label(v, cand)}")

    record = [0, 0, 0]
    while True:
        obs = env.observe()
        before = env.view(0, 0)
        if obs.decisions[0, 0] == DECISION_SLOTS:
            show(before)
        actions = np.full((1, 2), -1, np.int64)
        # The bot's move first (so a hint can't leak it).
        if obs.decisions[0, 1] == DECISION_SLOTS and a.bot_plays != "policy":
            actions[0, 1] = search.act(env, [0], side=1)[0]
        elif obs.decisions[0, 1] != 0:
            act_, _, _ = act(model, to_tensors(obs, np.array([1])), greedy=False)
            actions[0, 1] = int(act_[0])
        if obs.decisions[0, 0] == DECISION_PREVIEW:
            actions[0, 0] = choose_preview(before)
        elif obs.decisions[0, 0] == DECISION_SLOTS:
            actions[0, 0] = choose_slots(before, obs.masks[0, 0], hint)
        bot_view = env.view(0, 1)
        r = env.step(actions)
        if obs.decisions[0, 1] == DECISION_SLOTS:
            print(f"Bot chose: {joint_label(bot_view, int(actions[0, 1]))}")
        if r.done[0]:
            res = r.reward[0, 0]
            record[0 if res > 0 else 1 if res < 0 else 2] += 1
            print(f"\n*** {'You win' if res > 0 else 'The bot wins' if res < 0 else 'Draw'} "
                  f"after {r.turns[0]} turns.  Record: {record[0]}-{record[1]}-{record[2]} ***")
            if input("Play again with the same teams? [y/N] ").strip().lower() != "y":
                return
            continue
        after = env.view(0, 0)
        for line in changes(before, after):
            print("  " + line)

if __name__ == "__main__":
    torch.set_grad_enabled(False)
    main()
