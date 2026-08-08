# Battle model redesign — working design doc

Status: **draft, in progress.** Written 2026-08-08.

This doc exists so the analysis survives a session boundary. It is the design
input for overhauling `solver.py` / `matchup_search.py` from a greedy
expectimax model into something that actually models competitive play.

**Outstanding input:** the principles from vgcguide.com (11 articles listed in
§8) could not be retrieved — that domain is blocked by this environment's
network egress policy. §8 has slots for them and is deliberately incomplete.
Fill it in once the domain is allowlisted.

---

## 1. What the current model actually is

`solver.solve_best_action` is **depth-limited expectimax against a fixed,
deterministic opponent policy**. The docstring is honest about this:

> true doubles turn selection is a simultaneous-move game [...] Solving that
> exactly is a matrix-game / Nash equilibrium problem, which is a much bigger
> undertaking than fits here.

The opponent policy is `greedy_opponent_joint_action`: each of their actives
uses whichever known move does the most normalised damage, takes
Tailwind/Trick Room if it isn't already up, and **never Protects**
(`if a.kind == "protect": return -1`).

The precise failure mode this produces is not "the opponent is weak". It is:

> **We compute a best response to a policy we authored.** Maximising against
> one fixed pure strategy is exactly the definition of an exploitable
> strategy. The number that comes out ("beats 30/90 enemy brings") is a
> measurement of our team against *our own bot*, not against an opponent.

Everything downstream inherits this: `fast_eval` (greedy vs greedy),
`play_out_pair`, `search_robust_composition`, the win counts in every tab.

The recently-added `punish_check` is the right instinct — it asks "is there a
legal response that punishes this?" — but it is bolted on *after* the choice
is made, as a filter and a warning label. The information never enters the
value that picked the action. §3 is the argument that punish-resistance should
*be* the objective rather than a post-hoc audit.

### Secondary structural issues

| Issue | Where | Consequence |
|---|---|---|
| No mixed strategies | solver returns a single joint action | Cannot express "Protect 40% / attack 60%", which is genuinely optimal in many doubles spots |
| Average damage roll, no secondaries | `_play_branch` sets `sim.rng = None` | Survival thresholds vanish; a 15/16 kill and a 1/16 kill score identically |
| Depth 1 in practice | no caller passes `depth>1` | No modelling of setup payoff, Perish countdown, Tailwind expiry |
| Material-centric eval | `heuristic_eval` = HP diff + KO credit + positional | No concept of "this mon is my only answer to their win condition" |
| Single target per move | `candidate_actions` prunes to best target | Spread/target choice is a real mixed-strategy axis in doubles; pruned away |
| Enemy bring handled as worst-case | `search_robust_composition` | Over-prepares for brings a rational opponent would never pick (see §6) |

---

## 2. Measurements (taken 2026-08-08, this repo, this machine)

The optimisation intuition "the engine is slow, speed up the engine" is
**wrong**, and it's worth having the numbers before designing:

```
one full 12-turn game (play_out_worst_case)      0.05 s
  └ deepcopy share of that                       ~25%   (Battle already has a custom __deepcopy__)
  └ run_turn                                     0.42 ms per turn
  └ heuristic_eval                               0.11 ms per call

branching factor, turn 1, real teams:
  our joint actions                              23
  their joint actions                            28
  full matrix                                    644 cells
  cost per matrix cell                           0.27 ms
```

Which yields the single most important number in this document:

```
FULL Nash matrix solve, one turn, brute force    0.17 s
Same via double oracle (~12 cells materialised)  0.003 s
```

Compare against what we pay today: a 12-turn greedy-expectimax game is 0.05 s.
A 12-turn **double-oracle Nash** game is ~0.04 s.

> **A game-theoretically correct solver costs roughly the same as the greedy
> one we have.** The 121 s King run is not slow because the engine is slow —
> it is 1620 games (90 configs × 6 variants × 3 candidates) at 0.075 s each.
> The cost is in *how many configurations we brute-force*, not in per-game
> speed. That is a search-structure problem, and §6 is the fix.

---

## 3. The core reframe: solve the turn as a matrix game

Each turn, both sides commit simultaneously. That is a **two-player zero-sum
matrix game**. Build the payoff matrix

```
A[i][j] = value( our joint action i , their joint action j )
```

where `value` is `run_turn` then either `heuristic_eval` (depth 1) or a
recursive solve. For zero-sum games the following coincide and are computable
by linear programming:

- **Nash equilibrium** — neither side gains by deviating
- **Maximin / security level** — the value we can guarantee regardless of what
  they do
- **Minimax** — the value they can hold us to

This buys, directly and without extra machinery:

**a) "Safe plays that can't be punished" becomes the objective, not a filter.**
The maximin row *is* the play whose worst case is best. `punish_check` is
currently computing `min_j A[i][j]` for one chosen `i`. Solving the matrix
computes it for all `i` and picks the best — the punish idea, promoted from
audit to objective.

**b) Mixed strategies fall out.** The LP returns a distribution. "Protect 40%
/ Sucker Punch 60%" is representable and *correct* where a pure strategy is
exploitable.

**c) Exploitability becomes measurable.** For any candidate policy π,
`exploitability(π) = value(best response to π) − value(π)`. This is the
principled replacement for "beats N/90 brings": it answers *how much a good
opponent gains by knowing our strategy*, which is what actually decides games.

**d) It subsumes the scripted-opponent work.** A script is a degenerate
mixed strategy with all mass on one action. The current `enemy_script` +
top-K deviation-check machinery becomes: solve the matrix, but with a **prior**
that concentrates their probability mass on the scripted action. Deviation
safety is then automatic — the equilibrium already accounts for the off-script
columns, weighted by how likely they are. This removes the `CHECK_TOP_K = 6`
heuristic and its arbitrary cap entirely.

### Practical algorithm: Double Oracle

644 cells at 0.27 ms is affordable but wasteful, and it grows badly with
target-choice branching. **Double Oracle** (McMahan, Gordon & Blum 2003) solves
a large matrix game by materialising only a small subgame:

```
1. Start with a tiny restricted action set for each side (e.g. greedy pick + Protect)
2. Solve the restricted matrix game (LP) → mixed strategies (σ_us, σ_them)
3. Compute each side's BEST RESPONSE to the other's mixed strategy
4. If either best response is outside the restricted set, add it; goto 2
5. Converged ⇒ the restricted equilibrium IS an equilibrium of the full game
```

Equilibrium support in these games is small (typically 2–5 actions), so this
converges in a handful of iterations — hence the ~12-cell / 3 ms estimate.
The proof of correctness means **no accuracy is given up** relative to the full
matrix; it is pure pruning.

### Cheaper alternative if LP is unwanted

**Regret matching** / CFR-style iteration reaches an approximate equilibrium
with no LP dependency — a few hundred iterations of pure arithmetic over a
cached payoff matrix. Worth considering to avoid adding `scipy.optimize.linprog`
as a dependency, though scipy is likely already present.

---

## 4. Evaluation function overhaul

The matrix game is only as good as the leaf value. `heuristic_eval` is
material + a positional proxy; it has no representation of the two ideas the
brief calls out as crucial.

### 4a. The threat matrix ("who threatens who")

Precompute once per matchup, for every (ours × theirs) pair:

```
T[a][b] = { ohko: bool, 2hko: bool, outspeeds: bool,
            dmg_pct: float, survives_their_best: bool }
```

This is ~16–36 pairs, computed once, and it is the substrate for everything
below. It also gives **free action pruning** (moves that can't threaten
anything relevant get dropped before the matrix is built) and makes the
"who beats what" question directly inspectable in the UI.

### 4b. Answer preservation as bipartite matching

"Preserve a specific Pokémon as the answer to their worst threat" has an exact
formalisation. Build a bipartite graph: our surviving mons on one side, their
surviving mons on the other, edge weight = how well ours handles theirs (from
`T`). Then:

```
coverage(state) = maximum-weight bipartite matching
```

If their Kingambit's only answer is our Gallade, the matching has one high-value
edge through Gallade. Trading Gallade off collapses the matching value even
though *material is even* — which is precisely the intuition current material
eval cannot express. `scipy.optimize.linear_sum_assignment` solves this in
microseconds at this size.

This one term captures: preserving answers, why a "even" trade can be losing,
and why removing their check to *our* win condition is worth more than its HP.

### 4c. Roll awareness ("1 HP is infinitely more than 0 HP")

Average-roll determinism is the wrong abstraction for exactly the situations
that decide games. But 16 rolls per damage event is a combinatorial explosion.

The prior art here is **Damage Roll Grouping** (used by *FoulPlay*, the winning
entry in the PokéAgent Challenge): group the 16 rolls into equivalence classes
by *outcome* — typically {kills, doesn't kill}, sometimes 3 buckets — and weight
branches by class probability. Cost goes from ×16 to ×2, and it preserves the
only distinction that matters: **did it die**.

Concretely: replace the `min/max/avg` roll mode with an outcome-bucketed
distribution, and make the leaf value an expectation over buckets. This makes
"survives on 15/16 rolls" and "survives on 1/16" different numbers, which
Focus Sash, Sturdy, Multiscale, and every bulk-EV decision depend on.

---

## 5. Hidden information

Two distinct hidden-information problems, currently conflated:

1. **Their back 2** (which 4 of 6 they brought) — resolved progressively
2. **Their sets** (item / EV spread / exact 4 moves) — often never resolved

`heuristic_eval` already has a partial guard (`seen` filters unrevealed mons
out of the HP differential, unrevealed mons get a flat KO-value placeholder),
which is a good instinct and should be generalised into an explicit **belief
state**: a distribution over their remaining roster, updated by what they've
revealed, with the search taking an expectation over it.

The literature answer is **Information Set MCTS** (ISMCTS), evaluated for
Pokémon specifically by Ihara et al. (2018), which avoids the *strategy fusion*
error that plain determinisation makes — determinisation lets the searcher
implicitly "choose differently depending on hidden info it shouldn't know",
which is a real risk in the current 90-config sweep.

**Recommended scope:** do not build full ISMCTS initially. Do build the belief
state and take expectations over it, which captures most of the value; treat
ISMCTS as a later upgrade if the analysis warrants.

---

## 6. Team preview is *also* a matrix game

This may be the highest-value/lowest-effort change in the document.

`search_robust_composition` currently evaluates our bring against **all 90** of
their bring-4 configurations and reports how many we beat — i.e. worst-case /
maximin against a uniformly-considered opponent. But *they* don't know our
bring either. Team preview is a simultaneous game:

```
B[our bring][their bring] = result of that 4v4
```

Solving `B` for its Nash equilibrium instead of taking worst-case gives:

- **A mixed bring strategy** (correct: good players do vary brings)
- **Which of their 90 brings are actually credible** — the equilibrium support.
  Most of the 90 are strictly dominated and no rational opponent picks them.
  Today we spend equal compute on all 90 and let a strawman bring drag a team's
  score down.
- **Double oracle applies here too**, and this is where it pays for itself: we
  currently pay 90 × 6 variants × 3 candidates = 1620 games. Double oracle
  would materialise the credible support only — plausibly 10–20× less work,
  which is where the 121 s King run gets its real speedup.

This directly serves the brief's "solve the easier 6/6 problem": a guaranteed
lead is just conditioning `B` on a row.

---

## 7. Proposed tiered architecture

Keep the existing screen-then-verify shape; add tiers rather than replacing.

| Tier | Engine | Cost | Used for |
|---|---|---|---|
| 0 | `fast_eval` greedy-vs-greedy | ~1 ms | Pair matrix, beam search over thousands of teams |
| 1 | Current expectimax vs greedy | 0.05 s/game | Broad sweeps where a rough verdict suffices |
| 2 | **Double-oracle Nash per turn** (new) | ~0.04 s/game | Real verification, Vs Team, Battle Viewer, punish analysis |
| 3 | **Preview-level double oracle** (new) | seconds | Bring selection, team rating, the headline number |

Tier 2 replacing Tier 1 as the default is *cost-neutral* per the §2 numbers.

New user-facing outputs this unlocks, all of which the current model cannot
express:

- **Exploitability score** per team ("a perfect opponent gains X against you")
- **Mixed-strategy recommendations** ("T1: Fake Out Gengar 70% / Protect 30%")
- **Credible-bring list** from equilibrium support, instead of 90 rows
- **Answer map** from the threat matrix — who on your team answers what
- **Roll-sensitivity** — which wins are 15/16 and which are 8/16

---

## 8. VGC principles → formal mapping

**INCOMPLETE — vgcguide.com blocked, see header.** Articles to fold in:

*Before the battle:* approaching-best-of-1-vs-best-of-3 · analyzing-your-opponents-teams · team-preview · what-is-a-game-plan
*General principles:* what-is-pressure · predictions · protect-in-battle · switching
*Specific concepts:* battling-against-trick-room · 1-hp-is-infinitely-more-than-0-hp · how-to-analyze-a-battle

Mapping established so far (partly from search summaries, to be verified
against the real articles):

| VGC concept | Formal counterpart | Where it lands |
|---|---|---|
| "A safe play succeeds regardless of what your opponent goes for" | Maximin / security level of the turn matrix | §3 — becomes the objective |
| Punishing a committed opponent | Best response; `min_j A[i][j]` | §3a |
| Predictions, mind games | Mixed strategy over the equilibrium support | §3b |
| Preserving an answer to their threat | Max-weight bipartite matching term | §4b |
| Who threatens who | Threat matrix `T` | §4a |
| "1 HP is infinitely more than 0 HP" | Outcome-bucketed damage rolls | §4c |
| Attacking first / "best defence is a strong offence" | Already in eval via priority-KO bonus; strengthen via speed in `T` | §4a |
| Team preview and game plan | Preview-level matrix game + equilibrium support | §6 |
| Pressure | *TBD — likely: opponent's maximin value falling, i.e. all their options lose material* | — |
| Best-of-1 vs best-of-3 | *TBD — likely: risk tolerance / variance preference in strategy selection* | — |
| Protect's scouting value | *TBD — information gain; needs the belief state of §5* | — |

The "Pressure" row is the one I most expect to change the eval design, since a
formal reading ("every action available to them loses value") is computable
directly from the matrix we're already building — it is roughly the negative of
their security level.

---

## 9. Prior art

Retrieved via search only — **arxiv.org, smogon.com and ieeexplore are also
egress-blocked**, so these are leads at summary-level confidence, not papers I
have read. Verify before relying on specifics.

- **Ihara et al. (2018)**, *Implementation and Evaluation of Information Set
  Monte Carlo Tree Search for Pokémon* (IEEE) — compares Cheating MCTS,
  Determinized MCTS and ISMCTS on Pokémon; motivates ISMCTS via strategy fusion.
- **FoulPlay** — winner, PokéAgent Challenge. Root-parallelised MCTS, custom
  Rust engine, **Damage Roll Grouping** (§4c). Reported finding: specialised
  search/RL still clearly beats generalist LLMs here.
- **Metamon** — *Human-Level Competitive Pokémon via Scalable Offline RL with
  Transformers* (arXiv 2504.04395).
- **Smogon forum thread 3785316** — "VGC doubles as a poker problem": CFR+/ISMCTS
  over the public game tree with a Bayesian belief model. Closest existing
  discussion to this exact project; worth reading in full once unblocked.
- **McMahan, Gordon & Blum (2003)** — Double Oracle.
- **Zinkevich et al. (2007)** — Counterfactual Regret Minimization.
- **Lanctot et al.** — simultaneous-move MCTS / DUCT variants.

---

## 10. Proposed phasing

Each phase is independently shippable and independently verifiable.

**Phase A — threat matrix + eval (no search change).** Build `T`, add the
bipartite-matching coverage term to `heuristic_eval`. Verifiable immediately:
existing matchups should re-rank in explainable ways, and the answer map is a
UI feature on its own. Lowest risk, and it improves every tier at once.

**Phase B — matrix game at the turn level.** Implement payoff-matrix
construction + LP (or regret matching) + double oracle behind a flag; A/B it
against the current solver on known matchups. Retire `CHECK_TOP_K`. Deliver
mixed-strategy output and exploitability in Battle Viewer.

**Phase C — roll bucketing.** Replace `min/max/avg` with outcome buckets;
surface roll-sensitivity.

**Phase D — preview-level matrix game.** Nash over the bring matrix, credible-
bring support, double oracle for the sweep. This is where the runtime win lands.

**Phase E — belief state / ISMCTS.** Only if A–D justify it.

Suggested order of value-per-effort: **A → D → B → C → E.** D before B because
the preview game is where both the accuracy error (strawman brings) and the
runtime cost (1620 games) currently live.

---

## 11. Open questions for the next session

1. Does the vgcguide material change the eval terms in §4 — particularly
   "pressure" and the best-of-1 vs best-of-3 risk posture?
2. Is `scipy` acceptable as a dependency (for `linprog` and
   `linear_sum_assignment`), or should regret matching + a hand-rolled
   assignment be used instead?
3. Should Tier 2 *replace* Tier 1, or stay parallel? §2 says cost-neutral, which
   argues for replacement, but that invalidates cached/reported numbers.
4. How much does equilibrium play differ from current output in practice? Worth
   measuring exploitability of the *current* solver as a baseline before
   building anything — it quantifies the size of the prize.
