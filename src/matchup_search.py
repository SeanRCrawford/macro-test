"""
Multi-turn playout + enemy-lead-pair sweep.

Implements the first half of the user's proposed team-search approach:
"run all possible enemy pairs, find which of my pairs has the best record
against them" -- as a real simulated result, not a static heuristic. Each
matchup is played out turn-by-turn using the actual solver (depth=1 greedy
lookahead) for our side against the greedy opponent model, until someone's
pair is fully KO'd or a turn cap is hit.

NOTE on scope: this evaluates PURE 2v2 pair-vs-pair outcomes (no bench
reinforcements coming in on either side mid-fight). That's deliberate --
it answers "does this lead pairing of mine beat that lead pairing of
theirs," which is the building block the user described. Extending to
full 4-brought-6 games (switching in reserves, accounting for switch
damage taken, and needing your bench to beat their bench) is the next
increment once this base case is validated -- flagged, not silently
skipped.
"""
import itertools
import copy

import species_data
from species_data import build_merged_dataset
from stats import compute_stats
from damage import Combatant
from battle import Battle
from solver import (build_moveset, build_wide_movesets, solve_best_action,
                    TOP_K_MOVES)

MAX_TURNS = 8


def make_combatant(name, merged, natures):
    """Delegates to the shared, Mega-aware factory in combatants.py."""
    from combatants import make_combatant as _make
    return _make(name, merged, natures)



def _attach_movesets(battle, movesets, enemy_combatants, merged, moves_db, enemy_sets=None):
    """Attach the move knowledge the turn-level solver needs.

    `movesets` is what we plan with. `wide_movesets` is the OPPONENT's plausible
    move space, which is deliberately wider: assuming they hold only the four
    most-used moves is a self-fulfilling assumption, and it measured at 10 points
    of win rate (solver.build_wide_moveset) and 14-27 points of exploitability
    (tools/measure_robustness.py). Widening it costs ~26% per decision.

    Skipped for any Pokemon whose exact set the caller supplied -- if the moves
    are known there is nothing to widen.
    """
    battle.movesets = movesets
    known = {n for n, spec in (enemy_sets or {}).items() if spec.get("moves")}
    battle.wide_movesets = {
        **movesets,
        **build_wide_movesets([c.name for c in enemy_combatants if c.name not in known],
                              merged, moves_db),
    }


def play_out_worst_case(our_names, enemy_names, merged, moves_db, natures, typechart,
                         max_turns=MAX_TURNS, our_sets=None, enemy_sets=None,
                         return_choice=False, enemy_script=None, roll_index=None, tie_bias=None):
    """Minimax over BOTH sides' Mega Evolution choices.

    Either side may bring two Mega-capable Pokemon, but only one can actually
    transform per battle -- and that is a per-battle decision, not a fixed
    property of the team. So:
      * WE pick whichever of our Megas gives us the best result in THIS matchup
        (you know their team at preview, so you choose accordingly).
      * THEY are assumed to pick whichever of theirs is worst for us.
    A team can therefore Mega Gyarados into one matchup and Mega Charizard Y
    into another, bringing the other in base form.

    Returns (winner, turns, battle), or with return_choice=True also the
    chosen (our_mega, enemy_mega) pair so the recommendation can report it.
    """
    from species_data import mega_variants
    our_variants = mega_variants(list(our_names))
    enemy_variants = mega_variants(list(enemy_names))

    best = None  # best-for-us across our choices
    for ov in our_variants:
        worst = None  # worst-for-us across their responses
        for ev in enemy_variants:
            winner, turns, battle = play_out_pair(
                our_names, enemy_names, merged, moves_db, natures, typechart, max_turns,
                our_mega_transforms=ov, enemy_mega_transforms=ev,
                our_sets=our_sets, enemy_sets=enemy_sets, enemy_script=enemy_script,
                roll_index=roll_index, tie_bias=tie_bias)
            # Rank by how BAD the outcome is for us (higher = worse), so the enemy
            # branch maximises and ours minimises.
            #   we lose            -> worst; the faster we lose, the worse
            #   we win             -> the LONGER it takes us, the worse for us
            #   draw/timeout       -> between the two
            # The `p1` term previously used -turns, which ranked a FASTER win for us
            # as worse -- i.e. the opponent was picking the Mega that helped us most.
            if winner == "p2":
                rank = (2, -turns)
            elif winner == "p1":
                rank = (0, turns)
            else:
                rank = (1, 0)
            if worst is None or rank > worst[0]:
                worst = (rank, (winner, turns, battle), ev)
        if best is None or worst[0] < best[0]:
            best = (worst[0], worst[1], ov, worst[2])

    if return_choice:
        return best[1] + (best[2], best[3])
    return best[1]


def play_out_pair(our_names, enemy_names, merged, moves_db, natures, typechart,
                   max_turns=MAX_TURNS, our_mega_transforms=None, enemy_mega_transforms=None,
                   our_sets=None, enemy_sets=None, rng_seed=None, tie_bias=None,
                   enemy_script=None, roll_index=None):
    """our_names / enemy_names: lists of Pokemon names, length 2 (pure lead
    duel) or 4 (lead pair + back pair, roster[0:2] leads / roster[2:] bench).
    Multiple Mega-named picks per side are allowed (resolved via make_team;
    by default the first one in list order actually transforms -- pass
    *_mega_transforms=<name> to force a specific one instead, e.g. to
    search over which Mega a team actually evolves).
    Returns (winner 'p1'|'p2'|'draw'|'timeout', turn_count, battle)."""
    from combatants import make_team
    our_combatants = make_team(our_names, merged, natures, mega_transforms=our_mega_transforms,
                                sets=our_sets)
    enemy_combatants = make_team(enemy_names, merged, natures, mega_transforms=enemy_mega_transforms,
                                  sets=enemy_sets)

    movesets = {}
    for c in our_combatants + enemy_combatants:
        spec = (our_sets or {}).get(c.name) or (enemy_sets or {}).get(c.name) or {}
        movesets[c.name] = build_moveset(merged[c.name], moves_db, top_k=TOP_K_MOVES,
                                          only_moves=spec.get("moves"))

    battle = Battle(our_combatants, enemy_combatants, typechart, moves_db, rng_seed=rng_seed,
                     tie_bias=tie_bias)
    _attach_movesets(battle, movesets, enemy_combatants, merged, moves_db, enemy_sets)
    battle.force_roll_index = roll_index
    # Scripted openings (scripted_openings.py) may need the real movesets to pick a
    # damaging move for a generic (non-scripted) alternative line -- see
    # _best_damaging_action, which reads this back.
    battle._movesets = movesets

    from solver import greedy_opponent_joint_action
    for _ in range(max_turns):
        if battle.is_over():
            break
        # Plan deterministically, then RESOLVE on the real battle (which carries the
        # RNG). Previously the planning copy was adopted as the new state, so the
        # chosen line and the reported outcome were the same sample -- damage rolls,
        # flinch chances and speed ties all resolved in our favour by construction.
        best_action, _, sim = solve_best_action(battle, "p1", movesets, depth=1,
                                                 enemy_script=enemy_script)
        if not best_action and sim is None:
            break
        opp_actions = None
        if enemy_script is not None:
            # Scripted teams execute a rehearsed opening a greedy AI would never
            # find (double Protect, Perish Song, spending a turn on Shell Smash).
            try:
                opp_actions = enemy_script(battle, battle.p2, battle.p1, battle.turn_num + 1)
            except Exception:
                opp_actions = None
        if not opp_actions:
            opp_actions = greedy_opponent_joint_action(battle, battle.p2, battle.p1, movesets,
                                                        battle.turn_num + 1)
        try:
            battle.run_turn(list(best_action or []), opp_actions)
        except ValueError:
            break

    if battle.is_over():
        return battle.winner(), battle.turn_num, battle
    return "timeout", battle.turn_num, battle


def full_game_punish_audit(our_names, enemy_names, merged, moves_db, natures, typechart,
                            max_turns=MAX_TURNS, our_sets=None, enemy_sets=None,
                            enemy_script=None, cap=40):
    """Play the whole match, and at EVERY turn check whether our (script-aware,
    see solver.solve_best_action) chosen play could be punished -- KO'd outright
    this turn, or losing a Pokemon on our own best follow-up next turn -- by some
    legal response the opponent didn't actually make (see solver.punish_check).

    This is the real answer to "is my team just walking into it" across a whole
    game, not only the opening: every turn's decision gets its own verdict, so a
    plan that's safe on turn 1 but starts taking unforced risks by turn 4 is
    visible instead of hidden behind the final win/loss.

    Returns {"winner", "turns", "log", "audit": [{"turn", "our_action", "punished",
    "kind", "their_punishing_action"}, ...]}. `our_action`/`their_punishing_action`
    are human-readable strings, not raw Action objects.
    """
    from combatants import make_team
    from solver import punish_check, greedy_opponent_joint_action
    our_combatants = make_team(our_names, merged, natures, sets=our_sets)
    enemy_combatants = make_team(enemy_names, merged, natures, sets=enemy_sets)

    movesets = {}
    for c in our_combatants + enemy_combatants:
        spec = (our_sets or {}).get(c.name) or (enemy_sets or {}).get(c.name) or {}
        movesets[c.name] = build_moveset(merged[c.name], moves_db, top_k=TOP_K_MOVES,
                                          only_moves=spec.get("moves"))

    battle = Battle(our_combatants, enemy_combatants, typechart, moves_db)
    battle._movesets = movesets
    _attach_movesets(battle, movesets, enemy_combatants, merged, moves_db, enemy_sets)

    def _fmt_action(joint):
        parts = []
        for a in joint:
            if a.kind == "switch":
                parts.append(f"{a.combatant.name} switches to {a.targets[0].name}")
            elif a.move and a.move.name == "Protect":
                parts.append(f"{a.combatant.name} Protects")
            else:
                tgt = ", ".join(t.name for t in a.targets)
                parts.append(f"{a.combatant.name} uses {a.move.name if a.move else '?'} on {tgt}")
        return "; ".join(parts)

    def _fmt_tuple_action(desc):
        """Same formatting as _fmt_action, but for punish_check's plain-tuple
        (name, kind, move_name, target_names) response instead of real Actions."""
        parts = []
        for name, kind, move_name, targets in desc:
            if kind == "switch":
                parts.append(f"{name} switches to {targets[0] if targets else '?'}")
            elif move_name == "Protect":
                parts.append(f"{name} Protects")
            else:
                parts.append(f"{name} uses {move_name} on {', '.join(targets)}")
        return "; ".join(parts)

    audit = []
    for _ in range(max_turns):
        if battle.is_over():
            break
        our_action, _, _ = solve_best_action(battle, "p1", movesets, depth=1,
                                              enemy_script=enemy_script)
        if not our_action:
            break
        result = punish_check(battle, "p1", our_action, movesets, cap=cap)
        audit.append({
            "turn": battle.turn_num + 1,
            "our_action": _fmt_action(our_action),
            "punished": result["punished"],
            "kind": result["kind"],
            "their_punishing_action": (_fmt_tuple_action(result["their_action"])
                                        if result["their_action"] else None),
        })
        opp_actions = None
        if enemy_script is not None:
            try:
                opp_actions = enemy_script(battle, battle.p2, battle.p1, battle.turn_num + 1)
            except Exception:
                opp_actions = None
        if not opp_actions:
            opp_actions = greedy_opponent_joint_action(battle, battle.p2, battle.p1, movesets,
                                                         battle.turn_num + 1)
        try:
            battle.run_turn(list(our_action), opp_actions)
        except ValueError:
            break

    return {"winner": battle.winner() or "timeout", "turns": battle.turn_num,
            "log": battle.log.dump(), "audit": audit}


def sweep_enemy_leads_fixed_back(our_bring4, enemy_roster, merged, moves_db, natures, typechart,
                                  max_turns=MAX_TURNS):
    """our_bring4: our fixed [lead1, lead2, back1, back2].
    enemy_roster: their full 6-mon teams.csv list. For each possible enemy
    lead-pair (all C(6,2) combos), the enemy's back-2 is deterministically
    the next 2 remaining members in teams.csv order (an approximation --
    NOT an exhaustive search of their back choice, flagged as such) so this
    stays computationally tractable while still testing real switching.
    Returns list of (enemy_lead_pair, winner, turns)."""
    results = []
    for enemy_lead in itertools.combinations(enemy_roster, 2):
        remaining = [m for m in enemy_roster if m not in enemy_lead]
        enemy_bring4 = list(enemy_lead) + remaining[:2]
        winner, turns, battle = play_out_pair(our_bring4, enemy_bring4, merged, moves_db,
                                               natures, typechart, max_turns)
        results.append((enemy_lead, winner, turns))
    return results


def sweep_enemy_pairs(our_pair, enemy_roster, merged, moves_db, natures, typechart,
                       max_turns=MAX_TURNS):
    """Try our fixed lead pair against every possible 2-mon lead combo drawn
    from enemy_roster (their full brought roster, e.g. all 6 from teams.csv).
    Returns list of (enemy_pair, winner, turns)."""
    results = []
    for enemy_pair in itertools.combinations(enemy_roster, 2):
        winner, turns, battle = play_out_pair(list(our_pair), list(enemy_pair),
                                               merged, moves_db, natures, typechart, max_turns)
        results.append((enemy_pair, winner, turns))
    return results


def search_best_composition(our_pool6, enemy_bring4, merged, moves_db, natures, typechart,
                             max_turns=MAX_TURNS, our_sets=None, enemy_script=None, enemy_sets=None):
    """Search all (bring-4-of-6) x (lead-2-of-that-4) combos from our_pool6
    against one fixed enemy bring4 (lead = enemy_bring4[:2], back = [2:]).
    Returns results sorted best-first: (our_bring4, winner, turns)."""
    results = []
    seen = set()
    for bring4 in itertools.combinations(our_pool6, 4):
        for lead2 in itertools.combinations(bring4, 2):
            back2 = tuple(m for m in bring4 if m not in lead2)
            our_bring4 = list(lead2) + list(back2)
            key = tuple(our_bring4)
            if key in seen:
                continue
            seen.add(key)
            try:
                winner, turns, battle = play_out_worst_case(our_bring4, enemy_bring4, merged, moves_db,
                                                              natures, typechart, max_turns,
                                                              our_sets=our_sets, enemy_sets=enemy_sets,
                                                              enemy_script=enemy_script)
            except ValueError:
                continue  # illegal combo (2+ Megas in this bring-4) -- skip, don't crash the search
            results.append((our_bring4, winner, turns))
    # Best first: wins sorted by fastest decisive win, then draws, then losses by slowest loss.
    def sort_key(r):
        _, w, t = r
        if w == "p1":
            return (0, t)
        if w in ("draw", "timeout"):
            return (1, -t)
        return (2, -t)
    results.sort(key=sort_key)
    return results


if __name__ == "__main__":
    merged, _, moves, natures, typechart = build_merged_dataset()

    import pandas as pd
    teams_df = pd.read_csv(species_data.DATA_DIR / "teams.csv")
    teams = {}
    for _, row in teams_df.iterrows():
        teams[row["Team"]] = [row[c] for c in ["1", "2", "3", "4", "5", "6"] if pd.notna(row[c])]

    our_pool6 = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon", "Mega Skarmory", "Gholdengo"]

    # Representative "hardest case" enemy lead per team, taken from the earlier fixed-back
    # sweep's losing list (fastest loss = most dangerous). This keeps the search tractable
    # (90 of our combos x 1 enemy config x 5 teams = 450 games) rather than the full
    # 90 x 15 x 5 cross product -- flagged as a scoping choice, not silently assumed.
    toughest_enemy_lead = {
        "Rain": ("Pelipper", "Mega Swampert"),
        "Sun Rain": ("Mega Floette", "Mega Charizard Y"),
        "Big 6": ("Mega Charizard Y", "Mega Floette"),
        "NAIC": ("Mega Charizard Y", "Garchomp"),
        "King": ("Mega Blastoise", "Maushold"),
    }

    for team_name, roster in teams.items():
        lead = toughest_enemy_lead[team_name]
        remaining = [m for m in roster if m not in lead]
        enemy_bring4 = list(lead) + remaining[:2]
        print(f"=== {team_name}: searching our best answer to {lead} "
              f"(their back: {remaining[:2]}) ===")
        results = search_best_composition(our_pool6, enemy_bring4, merged, moves, natures, typechart,
                                           max_turns=12)
        best = results[0]
        wins = sum(1 for _, w, _ in results if w == "p1")
        print(f"  Best answer: lead {best[0][:2]}, back {best[0][2:]} "
              f"-> {best[1]} in {best[2]} turns")
        print(f"  ({wins}/{len(results)} of our bring4/lead2 combos beat this enemy lead)")
        print()


# --------------------------------------------------------------- risk analysis

def evaluate_risk(our_names, enemy_names, merged, moves_db, natures, typechart,
                   max_turns=MAX_TURNS, our_sets=None, n_random=24, seed=0, tie_bias="p2"):
    """How robust is a line, rather than just 'does the average roll win'?

    The default engine uses the average damage roll, which reports a result as
    if it were certain. Damage actually varies over a 0.85x-1.00x 16-step
    range, and speed ties are coin flips, so a line that wins on the average
    roll may still lose a real game. This runs:
        * the AVERAGE roll (the headline result)
        * a MIN-roll run (we roll low, effectively worst case for our damage)
        * a MAX-roll run
        * `n_random` randomised-roll runs
    and reports the win rate plus any speed ties encountered.

    Returns a dict; `win_rate` is the fraction of randomised runs won.
    """
    base = play_out_worst_case(our_names, enemy_names, merged, moves_db, natures, typechart,
                                max_turns, our_sets=our_sets)
    avg_winner, avg_turns, avg_battle = base

    # IMPORTANT: speed ties are forced to the OPPONENT here (tie_bias="p2").
    # Leaving them random does not measure risk -- the solver plans against the
    # same RNG stream it is being scored on, so it simply selects the branch
    # where the shuffle happened to favour us and reports ~100%. Forcing ties
    # against us gives an honest worst case; use evaluate_tie_branches() to see
    # both sides of the flip explicitly.
    outcomes = []
    for i in range(n_random):
        w, t, b = play_out_pair(our_names, enemy_names, merged, moves_db, natures, typechart,
                                 max_turns, our_sets=our_sets, rng_seed=seed + i,
                                 tie_bias=tie_bias)
        outcomes.append(w)

    wins = sum(1 for w in outcomes if w == "p1")
    losses = sum(1 for w in outcomes if w == "p2")
    return {
        "avg_result": avg_winner, "avg_turns": avg_turns,
        "win_rate": wins / len(outcomes) if outcomes else 0.0,
        "wins": wins, "losses": losses, "other": len(outcomes) - wins - losses,
        "speed_ties": avg_battle.speed_ties,
        "battle": avg_battle,
    }


def evaluate_tie_branches(our_names, enemy_names, merged, moves_db, natures, typechart,
                           max_turns=MAX_TURNS, our_sets=None, n_random=30, seed=0):
    """Run a matchup with speed ties forced BOTH ways.

    A line that only wins when you win the coin flip is not a plan. This
    reports the outcome with ties given to us and to them, plus a randomised
    win rate, so you can see whether the matchup actually depends on the flip.
    """
    out = {}
    for who, label in (("p1", "we_win_ties"), ("p2", "they_win_ties")):
        best = None
        from species_data import mega_variants
        for ev in mega_variants(list(enemy_names)):
            for ov in mega_variants(list(our_names)):
                w, t, b = play_out_pair(our_names, enemy_names, merged, moves_db, natures,
                                         typechart, max_turns, our_mega_transforms=ov,
                                         enemy_mega_transforms=ev, our_sets=our_sets,
                                         tie_bias=who)
                rank = (2, -t) if w == "p2" else ((0, t) if w == "p1" else (1, 0))
                if best is None or rank > best[0]:
                    best = (rank, (w, t, b))
        out[label] = {"winner": best[1][0], "turns": best[1][1], "battle": best[1][2],
                      "ties": best[1][2].speed_ties}
    rng = evaluate_risk(our_names, enemy_names, merged, moves_db, natures, typechart,
                         max_turns, our_sets=our_sets, n_random=n_random, seed=seed)
    out["random_win_rate"] = rng["win_rate"]
    out["random_detail"] = f"{rng['wins']}W-{rng['losses']}L-{rng['other']}o"
    return out


ROLL_PATH_LABELS = {None: "avg", 0: "min (0/16, 0.85x)", 5: "5/16 (0.90x)", 15: "max (15/16, 1.00x)"}


def robust_win_paths(our_names, enemy_names, merged, moves_db, natures, typechart,
                      max_turns=MAX_TURNS, our_sets=None, enemy_sets=None,
                      roll_indices=(0, 5, 15, None)):
    """Cross {lose every speed tie, win every speed tie} x a set of named
    discrete damage-roll paths (worst-case 0/16, the 5/16 roll, best-case
    15/16, and the plain average), and report the outcome of each cell.

    This is the tool a losing (or fragile-looking) matchup needs: rather than
    a single win/loss or a win RATE, it shows exactly which combination of
    "bad luck" assumptions the result depends on -- e.g. "we win as long as
    we don't lose the speed tie AND roll at least 5/16" is a very different
    situation from "we lose the instant either goes against us."

    `robust_win` is True only if we still win in the single worst cell (lose
    every tie, roll minimum damage) -- the bar the user asked for: a win has
    to hold up even against the worst rolls and losing every tie, not merely
    win on average.

    Returns {"paths": {(tie_label, roll_label): (winner, turns)}, "robust_win": bool,
             "worst_cell": (tie_label, roll_label)}.
    """
    tie_options = [("we_win_ties", "p1"), ("they_win_ties", "p2")]
    paths = {}
    for tie_label, tie_bias in tie_options:
        for roll_index in roll_indices:
            roll_label = ROLL_PATH_LABELS.get(roll_index, str(roll_index))
            w, t, _ = play_out_worst_case(our_names, enemy_names, merged, moves_db, natures,
                                           typechart, max_turns, our_sets=our_sets,
                                           enemy_sets=enemy_sets, roll_index=roll_index,
                                           tie_bias=tie_bias)
            paths[(tie_label, roll_label)] = (w, t)

    worst_cell = ("they_win_ties", ROLL_PATH_LABELS.get(0, "0"))
    robust_win = paths.get(worst_cell, ("timeout", 0))[0] == "p1"
    return {"paths": paths, "robust_win": robust_win, "worst_cell": worst_cell}
def play_out_stochastic(our_names, enemy_names, merged, moves_db, natures, typechart,
                         max_turns=MAX_TURNS, our_sets=None, our_mega=None, enemy_mega=None,
                         rng_seed=0, tie_bias=None):
    """A single battle with REAL randomness: 16-step damage rolls, secondary
    effects (Rock Slide flinch, etc.) and, unless pinned, random speed ties.

    The key detail is that the solver plans on a DETERMINISTIC clone (average
    roll, no RNG) and its chosen actions are then executed against the
    stochastic battle. Earlier the solver searched the same RNG stream it was
    scored on, so it silently picked whichever branch the dice happened to
    favour and reported near-100% win rates. Planning must not see the dice.
    """
    import copy as _copy
    from combatants import make_team
    from solver import build_moveset, solve_best_action, greedy_opponent_joint_action, TOP_K_MOVES
    from engine import Action

    oc = make_team(our_names, merged, natures, mega_transforms=our_mega, sets=our_sets)
    ec = make_team(enemy_names, merged, natures, mega_transforms=enemy_mega)
    movesets = {}
    for c in oc + ec:
        spec = (our_sets or {}).get(c.name) or {}
        movesets[c.name] = build_moveset(merged[c.name], moves_db, top_k=TOP_K_MOVES,
                                          only_moves=spec.get("moves"))

    battle = Battle(oc, ec, typechart, moves_db, rng_seed=rng_seed, tie_bias=tie_bias)

    for _ in range(max_turns):
        if battle.is_over():
            break

        # 1. Plan on a deterministic view of the current state.
        planner = _copy.deepcopy(battle)
        planner.rng = None
        planner.tie_bias = None
        chosen, _score, _sim = solve_best_action(planner, "p1", movesets, depth=1)
        if chosen is None:
            break

        # 2. Map the planned actions back onto the real (stochastic) battle.
        fwd = {}
        for a, b in zip(planner.p1.roster, battle.p1.roster):
            fwd[id(a)] = b
        for a, b in zip(planner.p2.roster, battle.p2.roster):
            fwd[id(a)] = b
        p1_actions = [Action(fwd.get(id(a.combatant), a.combatant), a.side, a.kind, a.move,
                              [fwd.get(id(t), t) for t in a.targets]) for a in chosen]

        # 3. Opponent chooses on the real state (its model is greedy, not RNG-aware).
        p2_actions = greedy_opponent_joint_action(battle, battle.p2, battle.p1, movesets,
                                                   battle.turn_num + 1)
        try:
            battle.run_turn(p1_actions, p2_actions)
        except ValueError:
            break

    return (battle.winner() or "timeout"), battle.turn_num, battle


def win_probability(our_names, enemy_names, merged, moves_db, natures, typechart,
                     max_turns=MAX_TURNS, our_sets=None, n=200, seed=0, tie_bias=None):
    """Monte-Carlo win probability over damage rolls, secondary effects and
    (unless pinned) speed ties. The opponent picks whichever Mega is worst for us.

    Returns {p_win, p_loss, p_other, n}.
    """
    from species_data import mega_variants
    worst = None
    for ev in mega_variants(list(enemy_names)):
        wins = losses = other = 0
        for i in range(n):
            w, _t, _b = play_out_stochastic(our_names, enemy_names, merged, moves_db, natures,
                                             typechart, max_turns, our_sets=our_sets,
                                             enemy_mega=ev, rng_seed=seed + i, tie_bias=tie_bias)
            if w == "p1":
                wins += 1
            elif w == "p2":
                losses += 1
            else:
                other += 1
        res = {"p_win": wins / n, "p_loss": losses / n, "p_other": other / n, "n": n,
               "enemy_mega": ev}
        if worst is None or res["p_win"] < worst["p_win"]:
            worst = res
    return worst


# ------------------------------------------------- exhaustive enemy brings

def enemy_configs(roster, fixed_lead=None):
    """Every bring-4 the opponent could field: all C(6,2) lead pairs x C(4,2)
    back pairs = 90 configurations for a 6-Pokemon team.

    Everything before this assumed their back-2 was "whichever two are listed
    next in teams.csv", which is one arbitrary sample out of six. That let a
    recommendation look safe while losing to a back pair that was never tried
    -- and it also produced nonsense brings like leading Mega Charizard Y with
    Mega Floette in the back, where only one can ever transform.
    """
    out = []
    leads = ([tuple(fixed_lead)] if fixed_lead
             else list(itertools.combinations(roster, 2)))
    for lead in leads:
        rest = [x for x in roster if x not in lead]
        for back in itertools.combinations(rest, 2):
            out.append((list(lead), list(back)))
    return out


def aggregate_battle_stats(our_names, enemy_roster, merged, moves_db, natures, typechart,
                            max_turns=MAX_TURNS, our_sets=None, enemy_sets=None,
                            fixed_lead=None, script_team=None):
    """Sum per-Pokemon usage stats (move-usage counts, damage dealt as %-of-target-
    max-HP, KOs secured -- see Battle.stats) across EVERY enemy bring-4 configuration
    for one fixed bring-4 of ours, i.e. "how did this team actually perform, in
    aggregate, across all the games it could be made to play against this opponent."

    Re-plays each config (the same games search_robust_composition's verify stage
    already played, since that function doesn't retain battle objects) -- one full
    pass over the opponent's configs (90, or 6 for a fixed lead), not the whole
    search, so this is meant to be called once for a team's FINAL chosen bring-4,
    not per candidate.

    Returns {(side, name): {"moves": {move_name: count}, "damage_pct": float,
    "kos": int}}.
    """
    from scripted_openings import script_for
    enemy_script = script_for(script_team) if script_team else None
    configs = enemy_configs(enemy_roster, fixed_lead=fixed_lead)
    total = {}
    for lead, back in configs:
        eb4 = list(lead) + list(back)
        if script_team:
            _, _, battle, _ = play_scripted_worst_case(
                our_names, eb4, merged, moves_db, natures, typechart, script_team,
                max_turns, our_sets=our_sets, enemy_sets=enemy_sets)
        else:
            _, _, battle = play_out_worst_case(
                our_names, eb4, merged, moves_db, natures, typechart, max_turns,
                our_sets=our_sets, enemy_sets=enemy_sets, enemy_script=enemy_script)
        for key, st in battle.stats.items():
            acc = total.setdefault(key, {"moves": {}, "damage_pct": 0.0, "kos": 0})
            for mv, cnt in st["moves"].items():
                acc["moves"][mv] = acc["moves"].get(mv, 0) + cnt
            acc["damage_pct"] += st["damage_pct"]
            acc["kos"] += st["kos"]
    return total


def search_robust_composition(our_pool6, enemy_roster, merged, moves_db, natures, typechart,
                               max_turns=MAX_TURNS, our_sets=None, verify_top=3, progress=None,
                               fixed_lead=None, enemy_script=None, script_team=None, enemy_sets=None,
                               preview_tau=None, preview_alpha=None,
                               rate_robustness=False, robustness_leads=3,
                               robustness_turns=5, prescreen_top=None):
    """Find the bring-4/lead-2 of ours with the best WORST CASE against every
    enemy configuration, rather than against one arbitrary back pair.

    Two stages, because 90 x 90 = 8,100 matchups per opponent is far too many
    for the full solver:
      1. fast screener over every (our config) x (their config) pair
      2. full solver verification of the `verify_top` survivors, against the
         enemy configs the screener found hardest for them

    Returns a list of dicts sorted best-first.
    """
    from fast_eval import fast_pair_score
    configs = enemy_configs(enemy_roster, fixed_lead=fixed_lead)

    our_configs = []
    seen = set()
    for bring4 in itertools.combinations(our_pool6, 4):
        for lead2 in itertools.combinations(bring4, 2):
            back2 = tuple(x for x in bring4 if x not in lead2)
            key = tuple(list(lead2) + list(back2))
            if key in seen:
                continue
            seen.add(key)
            our_configs.append(list(key))

    from preview import DEFAULT_ALPHA, DEFAULT_TAU, downside_score, ranked_brings
    preview_tau = DEFAULT_TAU if preview_tau is None else preview_tau
    preview_alpha = DEFAULT_ALPHA if preview_alpha is None else preview_alpha

    # Optional cheap filter: cut candidates on static threat-matrix coverage
    # before any battle is simulated (design doc 2s). Off by default -- a
    # prescreen silently discards candidates, so it must be opted into, and
    # tools/measure_prescreen.py is the measurement that says how narrow it can
    # safely be set.
    if prescreen_top:
        from prescreen import keep_top
        our_configs = keep_top(our_configs, enemy_roster, merged, moves_db,
                               natures, typechart, prescreen_top,
                               our_sets=our_sets, enemy_sets=enemy_sets)

    movesets_cache = {}
    scored = []
    for i, oc in enumerate(our_configs):
        worst, worst_cfg, wins = None, None, 0
        margins = []
        for lead, back in configs:
            r = fast_pair_score(tuple(oc[:2]), tuple(lead), merged, moves_db, natures,
                                 typechart, movesets_cache)
            margin = r["margin"]
            margins.append(margin)
            if margin > 0:
                wins += 1
            if worst is None or margin < worst:
                worst, worst_cfg = margin, (lead, back)
        # Design doc section 6a/6b: score against the brings they would
        # PLAUSIBLY pick, not uniformly over all 90 (which lets a bring no
        # rational opponent makes drag the number down) and not by a win count
        # (which says nothing about how badly the losses go).
        #
        # Reported alongside worst_margin rather than replacing it: the existing
        # ranking is unchanged by this commit, so the new number can be compared
        # against the old one on real searches before anything depends on it.
        scored.append({"our_bring4": oc, "worst_margin": worst, "worst_enemy": worst_cfg,
                        "screen_wins": wins, "screen_total": len(configs),
                        "downside": downside_score(margins, tau=preview_tau,
                                                   alpha=preview_alpha),
                        "screen_margins": margins})
        if progress and (i + 1) % max(1, len(our_configs) // 10) == 0:
            progress(i + 1, len(our_configs))

    scored.sort(key=lambda d: -d["worst_margin"])

    # Verify the survivors properly against EVERY enemy config.
    verified = []
    for cand in scored[:verify_top]:
        losses, worst_seen = [], None
        script_losses, conv_losses = [], []  # only populated when script_team is set
        for lead, back in configs:
            eb4 = list(lead) + list(back)
            if script_team:
                w, t, _, _v, all_res = play_scripted_worst_case(
                    cand["our_bring4"], eb4, merged, moves_db, natures, typechart, script_team,
                    max_turns, our_sets=our_sets, enemy_sets=enemy_sets, return_all=True)
                # Split the SAME already-played games (no extra simulation) into
                # "beats their script (+ generic near-script deviations)" vs "beats
                # plain conventional play" -- idx=None is specifically the plain
                # unscripted greedy 2v2, everything else is script-related. See
                # play_scripted_worst_case's return_all docstring.
                for idx, (rw, rt, _) in all_res.items():
                    if rw == "p1":
                        continue
                    if idx is None:
                        conv_losses.append((tuple(lead), tuple(back), rw, rt))
                    else:
                        script_losses.append((tuple(lead), tuple(back), rw, rt))
            else:
                w, t, _ = play_out_worst_case(cand["our_bring4"], eb4, merged, moves_db, natures,
                                               typechart, max_turns, our_sets=our_sets,
                                               enemy_sets=enemy_sets, enemy_script=enemy_script)
            if w != "p1":
                losses.append((tuple(lead), tuple(back), w, t))
            rank = (0 if w == "p1" else (1 if w != "p2" else 2), t)
            if worst_seen is None or rank > worst_seen[0]:
                worst_seen = (rank, (lead, back, w, t))
        # Which of their openings are actually worth worrying about (section
        # 6a). A ranking rather than the crisp equilibrium-support SET the
        # earlier draft promised -- more honest, since the underlying numbers
        # are estimates, and more useful to show.
        #
        # Grouped by LEAD, because the screener (fast_pair_score) only looks at
        # the lead pair: all six configs sharing a lead score identically, so a
        # per-config list would repeat every entry six times and imply a
        # precision the screen does not have. Their back two is resolved during
        # the battle, not at preview, so lead plausibility is the honest unit.
        margins = cand.get("screen_margins") or []
        by_lead = {}
        for (lead, _back), margin in zip(configs, margins):
            by_lead.setdefault(tuple(lead), margin)
        lead_keys = list(by_lead)
        plausible = ranked_brings([by_lead[k] for k in lead_keys],
                                  [" / ".join(k) for k in lead_keys],
                                  tau=preview_tau)
        rec = {**cand, "solver_losses": losses,
               "solver_wins": len(configs) - len(losses),
               "solver_total": len(configs),
               "worst_case": worst_seen[1] if worst_seen else None,
               "plausible_brings": plausible[:10]}
        if script_team:
            # Per-config "did it lose to ANY script/deviation variant" and "did it
            # lose to plain conventional" -- deduplicate by config since a config can
            # appear in script_losses more than once (one entry per losing variant).
            script_loss_cfgs = {(l, b) for l, b, _, _ in script_losses}
            conv_loss_cfgs = {(l, b) for l, b, _, _ in conv_losses}
            rec["script_wins"] = len(configs) - len(script_loss_cfgs)
            rec["script_total"] = len(configs)
            rec["script_losses"] = script_losses
            rec["conventional_wins"] = len(configs) - len(conv_loss_cfgs)
            rec["conventional_total"] = len(configs)
            rec["conventional_losses"] = conv_losses
        verified.append(rec)
    verified.sort(key=lambda d: (-d["solver_wins"], -d["worst_margin"]))

    # Final stage: re-rank by how PUNISHABLE each survivor is, not by how many
    # games it wins against our own bot (design doc 2q). Opt-in because it needs
    # a full payoff matrix per turn -- the cheap screen above still does the
    # shortlisting, this only refines what survived it.
    if rate_robustness and verified:
        verified = _rate_and_rerank(verified, enemy_roster, merged, moves_db,
                                     natures, typechart, configs, our_sets,
                                     enemy_sets, preview_tau, robustness_leads,
                                     robustness_turns)
    return verified


def _rate_and_rerank(verified, enemy_roster, merged, moves_db, natures, typechart,
                      configs, our_sets, enemy_sets, preview_tau,
                      leads_per_candidate, turns):
    """Attach an exploitability rating to each survivor and sort by it.

    Piloted with the least-exploitable solver configuration available: rating a
    team while playing it badly measures the pilot, not the team.
    """
    import solver as _solver
    from combatants import make_team
    from preview import ranked_brings
    from team_rating import rate_bring

    def choose(battle):
        with _solver.solver_mode(nash=True, depth=1):
            _solver.seed_nash_sampling(20260808)
            action, _score, _sim = _solver.solve_best_action(
                battle, "p1", battle.movesets)
        return action

    for rec in verified:
        our4 = rec["our_bring4"]
        margins = rec.get("screen_margins") or []
        by_lead = {}
        for (lead, _back), margin in zip(configs, margins):
            by_lead.setdefault(tuple(lead), margin)
        lead_keys = list(by_lead)
        if not lead_keys:
            continue
        ranked = ranked_brings([by_lead[k] for k in lead_keys],
                               [" / ".join(k) for k in lead_keys], tau=preview_tau)
        chosen = [(lead_keys[row["index"]], row["probability"])
                  for row in ranked[:leads_per_candidate]]

        def build(our_names, enemy_lead):
            rest = [x for x in enemy_roster if x not in enemy_lead]
            enemy4 = list(enemy_lead) + rest[:2]
            oc = make_team(list(our_names), merged, natures, sets=our_sets)
            ec = make_team(enemy4, merged, natures, sets=enemy_sets)
            ms = {c.name: build_moveset(merged[c.name], moves_db, top_k=TOP_K_MOVES)
                  for c in oc + ec}
            battle = Battle(oc, ec, typechart, moves_db)
            _attach_movesets(battle, ms, ec, merged, moves_db, enemy_sets)
            return battle, ms

        rating = rate_bring(our4, chosen, build, choose, max_turns=turns)
        rec["exploitability"] = rating.weighted_exploitability
        rec["severe_turns"] = rating.severe_turns
        rec["rated_turns"] = rating.total_turns
        worst = rating.worst_lead
        if worst:
            lead, _p, report = worst
            rec["hardest_lead"] = list(lead)
            wt = report.worst_turn
            if wt:
                from robustness import describe_action
                rec["worst_turn"] = {
                    "turn": wt.turn,
                    "exploitability": wt.exploitability,
                    "our_play": describe_action(wt.our_action),
                    "punished_by": describe_action(wt.punisher) if wt.punisher else None,
                }

    rated = [r for r in verified if "exploitability" in r]
    unrated = [r for r in verified if "exploitability" not in r]
    rated.sort(key=lambda d: d["exploitability"])
    return rated + unrated


def play_scripted_worst_case(our_names, enemy_names, merged, moves_db, natures, typechart,
                              team_name, max_turns=MAX_TURNS, our_sets=None, enemy_sets=None,
                              return_all=False):
    """Play a scripted opponent, trying EVERY opening variant and returning the
    worst outcome for us.

    A forced-lead team has a tiny decision space -- mainly which of our two slots
    to Fake Out -- so it should be searched exhaustively rather than assuming it
    always targets slot 1. A line that only survives one targeting choice is not
    a plan.

    Returns (winner, turns, battle, variant_index) for the worst variant. With
    return_all=True, ALSO returns {variant_idx: (winner, turns, battle)} for
    every variant tried (idx=None is the plain unscripted greedy 2v2, everything
    else is either the real script or a generic near-script deviation -- see
    scripted_openings.all_scripts) -- lets a caller split "beats the script" from
    "beats plain conventional play" without re-simulating anything, since these
    are the same games that were already going to be played to find the worst.
    """
    from scripted_openings import all_scripts
    # The unscripted greedy opponent is ALWAYS one of the variants. A scripted team
    # is not obliged to run its script -- it can simply attack with both slots, or
    # Protect the setup threat and attack with the other. A composition that only
    # beats the rehearsed line but loses the plain 2v2 is not a real answer, so the
    # conventional game is evaluated alongside every scripted opening.
    variants = all_scripts(team_name) + [(None, None)]
    if not variants:
        w, t, b = play_out_worst_case(our_names, enemy_names, merged, moves_db, natures,
                                       typechart, max_turns, our_sets=our_sets, enemy_sets=enemy_sets)
        return (w, t, b, None, {None: (w, t, b)}) if return_all else (w, t, b, None)

    worst = None
    all_results = {}
    for idx, script in variants:
        w, t, b = play_out_worst_case(our_names, enemy_names, merged, moves_db, natures,
                                       typechart, max_turns, our_sets=our_sets, enemy_sets=enemy_sets,
                                       enemy_script=script)   # script=None -> greedy 2v2
        all_results[idx] = (w, t, b)
        rank = (2, -t) if w == "p2" else ((0, t) if w == "p1" else (1, 0))
        if worst is None or rank > worst[0]:
            worst = (rank, (w, t, b, idx))
    return (*worst[1], all_results) if return_all else worst[1]


def find_committed_plan(our_names, enemy_names, merged, moves_db, natures, typechart,
                         team_name, max_turns=MAX_TURNS, our_sets=None, commit_turns=1,
                         enemy_sets=None):
    """Find a SINGLE turn-1 action that wins against EVERY opponent variant.

    Why this exists: the solver chooses our move after computing what the
    opponent does that turn, so evaluating each variant separately lets us use a
    different answer for each. That is not a plan -- at team preview you commit
    to a lead and, on turn 1, to a move, without knowing whether they will run
    their script, Fake Out the other slot, or simply attack with both.

    This enumerates our candidate turn-1 joint actions, FIXES each one, and plays
    it out against every opponent variant (all script openings plus the plain
    greedy 2v2). Only an action that wins them all is a real answer. Later turns
    may use the solver, since by then their line is observed.

    Returns (best_action_description, results_per_variant, wins_all: bool).
    """
    from scripted_openings import all_scripts
    from solver import (build_moveset, our_candidate_joint_actions,
                         greedy_opponent_joint_action, TOP_K_MOVES)
    from combatants import make_team

    variants = all_scripts(team_name) + [(None, None)]

    # Build one battle just to enumerate our legal turn-1 joint actions.
    probe = _fresh_battle(our_names, enemy_names, merged, moves_db, natures, typechart,
                           our_sets, enemy_sets)
    movesets = _movesets_for(probe, merged, moves_db, our_sets, enemy_sets)
    options = our_candidate_joint_actions(probe, probe.p1, probe.p2, movesets, 1)

    best = None
    for opt in options:
        desc = tuple((a.combatant.name, a.kind,
                       a.move.name if a.move else (a.targets[0].name if a.targets else "-"),
                       tuple(t.name for t in a.targets) if a.kind != "switch" else ())
                      for a in opt)
        per_variant, all_win = {}, True
        for idx, script in variants:
            b = _fresh_battle(our_names, enemy_names, merged, moves_db, natures, typechart,
                               our_sets, enemy_sets)
            ms = _movesets_for(b, merged, moves_db, our_sets, enemy_sets)
            b._movesets = ms  # scripted_openings.py's generic alternatives need real moves
            # Re-express our fixed choice against THIS battle's objects.
            fixed = _rebind_actions(desc, b)
            if fixed is None:
                all_win = False
                break
            opp = None
            if script is not None:
                try:
                    opp = script(b, b.p2, b.p1, 1)
                except Exception:
                    opp = None
            if not opp:
                opp = greedy_opponent_joint_action(b, b.p2, b.p1, ms, 1)
            try:
                b.run_turn(fixed, opp)
            except ValueError:
                all_win = False
                break
            # Turn 1 is committed; from turn 2 the solver may adapt.
            w, t, b2 = _finish_with_solver(b, ms, max_turns)
            per_variant[idx] = (w, t)
            if w != "p1":
                all_win = False
        if all_win and per_variant:
            return desc, per_variant, True
        if best is None or sum(1 for v in per_variant.values() if v[0] == "p1") > \
                sum(1 for v in best[1].values() if v[0] == "p1"):
            best = (desc, per_variant, False)
    return best if best else (None, {}, False)


def _fresh_battle(our_names, enemy_names, merged, moves_db, natures, typechart, our_sets,
                   enemy_sets=None):
    from combatants import make_team
    oc = make_team(our_names, merged, natures, sets=our_sets)
    ec = make_team(enemy_names, merged, natures, sets=enemy_sets)
    return Battle(oc, ec, typechart, moves_db)


def _movesets_for(battle, merged, moves_db, our_sets, enemy_sets=None):
    from solver import build_moveset, TOP_K_MOVES
    ms = {}
    for c in battle.p1.roster + battle.p2.roster:
        spec = (our_sets or {}).get(c.name) or (enemy_sets or {}).get(c.name) or {}
        ms[c.name] = build_moveset(merged[c.name], moves_db, top_k=TOP_K_MOVES,
                                    only_moves=spec.get("moves"))
    return ms


def _rebind_actions(desc, battle):
    """Recreate a saved turn-1 joint action against a fresh battle."""
    from engine import Action
    out = []
    for name, kind, move_or_target, tgt_names in desc:
        actor = next((c for c in battle.p1.active if c is not None and c.name == name), None)
        if actor is None:
            return None
        if kind == "switch":
            inc = next((c for c in battle.p1.bench if c.name == move_or_target), None)
            if inc is None:
                return None
            out.append(Action(actor, "p1", "switch", None, [inc]))
        else:
            key = move_or_target.lower().replace(" ", "").replace("-", "").replace("'", "")
            if key not in battle.moves_db:
                return None
            mv = battle.make_move(key)
            tgts = [c for c in battle.p1.active + battle.p2.active
                    if c is not None and c.name in tgt_names] or [actor]
            out.append(Action(actor, "p1", kind, mv, tgts))
    return out


def _finish_with_solver(battle, movesets, max_turns):
    from solver import solve_best_action, greedy_opponent_joint_action
    for _ in range(max_turns - battle.turn_num):
        if battle.is_over():
            break
        act, _, sim = solve_best_action(battle, "p1", movesets, depth=1)
        if sim is None:
            break
        battle = sim
    return (battle.winner() or "timeout"), battle.turn_num, battle
