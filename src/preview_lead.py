"""Team preview, in a minute: which lead does not lose on the spot?

THE DECISION THIS SERVES. You see their six. You pick four and a lead pair,
before they reveal anything, and you get about a minute. The full audit is
hours, so it cannot answer this; what it CAN answer -- "is this opening already
lost" -- is one matrix solve at half a second, and that is the question you
actually have at preview.

    "I need a lead that doesn't automatically lose, or at least if I face
     certain bad enemy leads I can switch out to create a winning position."

So this ranks OUR lead pairs by the value we can GUARANTEE on turn 1 against
their best reply, and says which of their leads is the one that hurts, and
whether our answer to it is to attack, to protect, or to pivot.

--------------------------------------------------------------------------
WHY MAXIMIN, AND WHY IT PRUNES.

They choose their lead knowing our six. So a lead of ours is worth its WORST
case over their leads -- not its average, which is the mistake §4.0b is about.
That makes it a maximin, and maximin prunes: once our lead L has met one of
their leads that beats the best complete lead so far, no further work on L can
rescue it. Nothing that follows can raise a minimum.

Measured, that is the difference between 112 s for the full 15x15 and fitting
inside a minute.

--------------------------------------------------------------------------
WHAT IT DOES NOT KNOW. Everything after turn 1. A lead that opens cleanly and
collapses on turn five looks fine here -- that is what the deep search is for.
This tells you which openings are not immediately lost, which is a smaller
claim and the one available in the time you have.

Turn-1 value is in `heuristic_eval` points, where ~180 is a Pokemon. Negative
is normal: they answer with a perfect read.
"""
import itertools
import time

from punish_screen import opening_turn

# One representative enemy lead is used to ORDER our leads before the real
# search, so pruning has a good bound early and an interrupted run still
# returns its better candidates first. Their most common lead is their first
# two, which is how teams.csv writes a fixed-lead team.
ORDERING_SAMPLE = 1


def _bring(six, lead, back=None):
    """The bring-4: lead pair first, then the back pair.

    `back=None` takes the two best remaining, which is only a placeholder --
    the back is a real choice and `rank_brings` searches it. Reported as a
    defect: "Optimise the back two also, it is critical."
    """
    if back is not None:
        return list(lead) + list(back)
    return list(lead) + [x for x in six if x not in lead][:2]


def backs_for(six, lead):
    """Every back pair that can sit behind this lead: C(4,2) = 6 of them."""
    rest = [x for x in six if x not in lead]
    return list(itertools.combinations(rest, 2))


def _action_kind(rec):
    """What our recommended opening actually is, in one word.

    The pivot question, directly: "if I face certain bad enemy leads I can
    switch out to create a winning position". If the answer to their worst lead
    is a switch, that is the plan, and it should be visible without reading a
    log.
    """
    actions = getattr(rec, "our_action", None) or []
    kinds = {getattr(a, "kind", None) for a in actions}
    if "switch" in kinds:
        return "pivot"
    if "protect" in kinds:
        return "protect"
    return "attack"


def rank_leads(our6, their6, world, budget=60.0, our_sets=None, enemy_sets=None,
               their_leads=None, our_leads=None, on_progress=None):
    """Our lead pairs, best guaranteed opening first.

    `budget` is wall-clock seconds. The search is ordered so that stopping
    early still returns the best candidates it has finished, and every returned
    entry says whether it was fully checked -- a lead abandoned by pruning is
    reported as rejected, never as good.

    Returns (ranked, meta). Each entry:
        lead, bring, guaranteed, worst_vs, answer, punish, complete
    """
    merged, moves = world["merged"], world["moves"]
    natures, typechart = world["natures"], world["typechart"]
    ours = list(our_leads or itertools.combinations(our6, 2))
    theirs = list(their_leads or itertools.combinations(their6, 2))
    started = time.time()
    solves = [0]

    def solve(our_lead, their_lead):
        solves[0] += 1
        return opening_turn(_bring(our6, our_lead), _bring(their6, their_lead),
                            merged, moves, natures, typechart,
                            our_sets=our_sets, enemy_sets=enemy_sets)

    # --- order our leads cheaply, so pruning has a bound and an early stop
    #     returns the good ones ------------------------------------------
    probe_lead = theirs[0]
    order = []
    for our_lead in ours:
        if time.time() - started > budget * 0.35:
            order.extend((None, l) for l in ours[len(order):])
            break
        rec = solve(our_lead, probe_lead)
        order.append((rec.worst_case if rec else None, our_lead))
    order.sort(key=lambda x: -(x[0] if x[0] is not None else -1e9))

    # --- maximin with pruning -------------------------------------------
    ranked, best_complete = [], None
    for probe_value, our_lead in order:
        if time.time() - started > budget:
            break
        worst, worst_vs, worst_answer, worst_punish = None, None, None, 0.0
        complete = True
        for their_lead in theirs:
            if time.time() - started > budget:
                complete = False
                break
            rec = solve(our_lead, their_lead)
            if rec is None:
                continue
            if worst is None or rec.worst_case < worst:
                worst = rec.worst_case
                worst_vs = list(their_lead)
                worst_answer = _action_kind(rec)
                worst_punish = rec.exploitability
            # THE PRUNE: a minimum can only fall, so once this lead is behind a
            # lead we have already finished, nothing left can save it.
            if best_complete is not None and worst < best_complete:
                complete = False
                break
        if worst is None:
            continue
        entry = {"lead": list(our_lead), "bring": _bring(our6, our_lead),
                 "guaranteed": worst, "worst_vs": worst_vs,
                 "answer": worst_answer, "punish": worst_punish,
                 "complete": complete}
        ranked.append(entry)
        if complete and (best_complete is None or worst > best_complete):
            best_complete = worst
        if on_progress:
            on_progress(entry, time.time() - started)

    # Fully-checked leads first: a pruned one is only known to be WORSE than
    # some other lead, and reporting it alongside a proven one would invite
    # exactly the mistake the prune exists to make cheap.
    ranked.sort(key=lambda e: (not e["complete"], -e["guaranteed"]))
    return ranked, {"seconds": time.time() - started, "solves": solves[0],
                    "our_leads": len(ours), "their_leads": len(theirs),
                    "budget": budget,
                    "complete": sum(1 for e in ranked if e["complete"])}


def describe(ranked, meta, top=5):
    """The answer, in the order a player needs it."""
    if not ranked:
        return "No opening could be solved in the time given."
    lines = [f"{meta['solves']} opening solves in {meta['seconds']:.0f}s "
             f"({meta['complete']} of {meta['our_leads']} leads fully checked "
             f"against all {meta['their_leads']} of theirs)", ""]
    for e in ranked[:top]:
        # A pruned lead's number is an UPPER BOUND -- we stopped before finding
        # its true worst case -- and printing it bare invites reading 81 as
        # better than a proven 73. The bound is marked as one.
        value = (f"{e['guaranteed']:7.0f}" if e["complete"]
                 else f"  <={e['guaranteed']:4.0f}")
        flag = "" if e["complete"] else "   (abandoned -- provably worse)"
        lines.append(f"{value}  lead {e['lead'][0]} / {e['lead'][1]}{flag}")
        if e["complete"]:
            lines.append(f"         worst vs {e['worst_vs'][0]}/{e['worst_vs'][1]}"
                         f" -- answer: {e['answer']}   (punish {e['punish']:.0f})")
    best = ranked[0]
    if best["complete"]:
        lines += ["", f"BRING {' / '.join(best['bring'])}",
                  f"LEAD  {best['lead'][0]} / {best['lead'][1]}"]
        if best["answer"] == "pivot":
            lines.append("Against their worst lead the plan is to SWITCH, not "
                         "to attack -- the lead buys the pivot.")
    return "\n".join(lines)


# ---------------------------------------------------------------- the line
# Ranking leads answers "what do I send out". It does not answer "and then
# what", which is the half you actually play. These take the chosen lead and
# produce the SEQUENCE -- what to do on each turn, against their equilibrium
# reply -- plus how likely that line is to win, because "wins on the average
# roll" and "probably wins" are different claims (WORKFLOW.md 4.0b).


def _say(actions):
    """A joint action in words: 'Incineroar Fake Out -> Pelipper'."""
    out = []
    for a in actions or []:
        who = getattr(getattr(a, "combatant", None), "name", "?")
        if getattr(a, "kind", None) == "switch":
            target = getattr(a, "targets", None) or []
            name = getattr(target[0], "name", "?") if target else "?"
            out.append(f"{who} -> {name}")
            continue
        move = getattr(getattr(a, "move", None), "name", None) or a.kind
        targets = [getattr(t, "name", "?") for t in (getattr(a, "targets", None) or [])
                   if getattr(t, "name", None) != who]
        out.append(f"{who} {move}" + (f" -> {'/'.join(targets)}" if targets else ""))
    return "; ".join(out)


def line_for(bring, their_bring, world, our_sets=None, enemy_sets=None,
             max_turns=16, depth=1, win_samples=8, pilot=None):
    """The turn-by-turn plan from this position, and how likely it wins.

    The line is played against their EQUILIBRIUM reply each turn -- not against
    a best response to the move we just made, which is a clairvoyant opponent
    that beats every team ever built (see robustness.LineReport.won).

    `win_samples` buys the probability separately, by replaying the position
    over real damage rolls and speed ties. The line tells you what to do; the
    probability tells you how much to trust it. A line can be the best
    available and still be a coin flip, and both facts are worth having.
    """
    from deep_dive import audit_position

    report = audit_position(list(bring), list(their_bring), world["merged"],
                            world["moves"], world["natures"], world["typechart"],
                            our_sets=our_sets, enemy_sets=enemy_sets,
                            max_turns=max_turns, depth=depth)
    turns = []
    for rec in report.turns:
        turns.append({
            "turn": rec.turn,
            "play": _say(rec.our_action),
            "their_reply": _say(rec.equilibrium_reply),
            "punish": rec.exploitability,
            "kos": list(rec.kos or []),
            "events": list(rec.events or []),
        })
    out = {"their_bring": list(their_bring), "outcome": report.outcome,
           "turns": turns, "length": report.length,
           "final_margin": report.final_margin,
           "win_prob": None, "win_interval": None, "win_games": 0}
    if win_samples:
        from win_rate import matchup_win_prob_adaptive
        import matchup_search as _ms
        est = matchup_win_prob_adaptive(
            list(bring), list(their_bring), world, n_max=win_samples,
            turns=max_turns, our_sets=our_sets, enemy_sets=enemy_sets,
            pilot=pilot or _ms.EQUILIBRIUM_PILOT)
        out["win_prob"] = est.p
        out["win_interval"] = est.interval
        out["win_games"] = est.n
    return out


def lines_for_lead(entry, their6, world, their_leads=None, budget=45.0,
                   our_sets=None, enemy_sets=None, max_turns=16,
                   win_samples=8, on_progress=None):
    """One line per enemy lead, hardest first.

    Hardest first because that is the one you need to have thought about: the
    lead ranking already told you which of theirs hurts most, so it is played
    before the budget can run out on the easy ones.
    """
    theirs = list(their_leads or itertools.combinations(their6, 2))
    worst = tuple(entry.get("worst_vs") or ())
    theirs.sort(key=lambda t: 0 if tuple(t) == worst else 1)
    started, out = time.time(), []
    for their_lead in theirs:
        if time.time() - started > budget:
            break
        line = line_for(entry["bring"], _bring(their6, their_lead), world,
                        our_sets=our_sets, enemy_sets=enemy_sets,
                        max_turns=max_turns, win_samples=win_samples)
        line["their_lead"] = list(their_lead)
        out.append(line)
        if on_progress:
            on_progress(line, time.time() - started)
    return out, {"seconds": time.time() - started, "played": len(out),
                 "of": len(theirs)}


def describe_line(line, max_turns=8):
    """One line, as something you could read while the clock runs."""
    head = f"vs {line['their_lead'][0]} / {line['their_lead'][1]}"
    if line.get("win_prob") is not None:
        lo, hi = line["win_interval"]
        head += (f"   {line['win_prob']:.0%} win [{lo:.0%}-{hi:.0%}]"
                 f" over {line['win_games']} games")
    head += f"   ({line['outcome']} in {line['length']} turns)"
    rows = [head]
    for t in line["turns"][:max_turns]:
        rows.append(f"  T{t['turn']}: {t['play']}")
        if t["their_reply"]:
            rows.append(f"        they: {t['their_reply']}")
        if t["kos"]:
            rows.append(f"        KO: {', '.join(t['kos'])}")
    return "\n".join(rows)


def rank_brings(entry, our6, their6, world, budget=45.0, our_sets=None,
                enemy_sets=None, their_leads=None, max_turns=16,
                win_samples=8, on_progress=None):
    """Now choose the BACK TWO, by playing them out rather than assuming them.

    THE GAP THIS CLOSES. `rank_leads` solves turn 1, where the back pair is off
    the field and barely matters -- so it used a placeholder. But the back is
    most of the game: it decides what you pivot INTO, what covers the lead's
    weakness, and whether the endgame is 2v2 or 2v1. Optimising the lead and
    then assuming the back is not the same as optimising the bring.

    Six back pairs per lead, scored by the WORST win probability across their
    leads -- maximin again, because they still choose. Hardest of their leads
    first, so an exhausted budget costs the easy information rather than the
    important information.
    """
    lead = tuple(entry["lead"])
    theirs = list(their_leads or itertools.combinations(their6, 2))
    worst_first = tuple(entry.get("worst_vs") or ())
    theirs.sort(key=lambda t: 0 if tuple(t) == worst_first else 1)

    started, out = time.time(), []
    for back in backs_for(our6, lead):
        if time.time() - started > budget:
            break
        bring = _bring(our6, lead, back)
        worst_p, worst_vs, played = None, None, 0
        for their_lead in theirs:
            if time.time() - started > budget:
                break
            from win_rate import matchup_win_prob_adaptive
            est = matchup_win_prob_adaptive(
                bring, _bring(their6, their_lead), world, n_max=win_samples,
                turns=max_turns, our_sets=our_sets, enemy_sets=enemy_sets)
            played += 1
            if worst_p is None or est.p < worst_p:
                worst_p, worst_vs = est.p, list(their_lead)
            # Same prune as the lead search: a minimum only falls.
            if out and worst_p < max(o["worst_win"] for o in out):
                break
        if worst_p is None:
            continue
        entry_out = {"back": list(back), "bring": bring,
                     "worst_win": worst_p, "worst_vs": worst_vs,
                     "checked": played, "of": len(theirs),
                     # A back checked against 6 of their 15 leads has an UPPER
                     # BOUND of worst_win, not a proven one -- the same trap as
                     # the lead pruning, and it showed up the same way: a
                     # 6-of-15 "100%" sorted above a 15-of-15 75%.
                     "complete": played >= len(theirs)}
        out.append(entry_out)
        if on_progress:
            on_progress(entry_out, time.time() - started)
    out.sort(key=lambda e: (not e["complete"], -e["worst_win"]))
    return out, {"seconds": time.time() - started, "backs": len(out),
                 "of": len(backs_for(our6, lead))}
