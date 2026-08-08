# Battle model redesign — working design doc

Status: **draft, in progress.** Written 2026-08-08.

This doc exists so the analysis survives a session boundary. It is the design
input for overhauling `solver.py` / `matchup_search.py` from a greedy
expectimax model into something that actually models competitive play.

**Update 2026-08-08:** vgcguide.com is now reachable. All 11 articles have been
retrieved and read in full, and §8 has been rewritten against the real text
rather than search summaries. The mapping changed materially — three of the
placeholder rows were wrong, not merely incomplete — and §10's phase ordering
has been revised as a result. See §8b and §10.

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
| Depth 1 in practice | no caller passes `depth>1` | No modelling of setup payoff, Perish countdown, Tailwind expiry. **Promoted after §8** — two specific, common plays are invisible at depth 1: burning a field-effect clock (§8c.5) and forcing a Protect (§8c.6) |
| Material-centric eval | `heuristic_eval` = HP diff + KO credit + positional | No concept of "this mon is my only answer to their win condition"; also **HP is scored linearly** (`solver.py:327`), which §4d/§8b.1 argues is the wrong shape |
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

Which *appeared* to yield the single most important number in this document:

```
FULL Nash matrix solve, one turn, brute force    0.17 s
Same via double oracle (~12 cells materialised)  0.003 s
```

> ## ⚠ SUPERSEDED — these two numbers are wrong. See §2c.
>
> The double-oracle figure was an estimate, not a measurement, and it is out by
> **~10× on cells and ~27× on time**. Measured with a real implementation
> (`src/matrix_game.py`, `tools/measure_depth2_cost.py`): **118 cells and
> 0.08 s**, not 12 cells and 0.003 s. The error came from assuming double
> oracle only materialises its final restricted subgame; it does not, because
> each best-response step must scan a full action set against the other side's
> mixed strategy (§2c).
>
> The claim that followed — *"a game-theoretically correct solver costs roughly
> the same as the greedy one we have"* — **is therefore false.** A 12-turn
> double-oracle game is ~0.96 s against ~0.05 s for the greedy one: about 20×,
> not parity. Everything in this document that leaned on cost-neutrality (§7's
> tier table, open question 3) has been corrected.

What survives, and is still worth stating: the 121 s King run is **not** slow
because the engine is slow — it is 1620 games (90 configs × 6 variants × 3
candidates) at 0.075 s each. The cost is in *how many configurations we
brute-force*, not in per-game speed. That is a search-structure problem, and §6
is the fix. That argument never depended on the double-oracle figure.

---

## 2b. Baseline: how exploitable is the current solver? (measured)

Open question #4 from the first draft — "measure the current solver's
exploitability before building anything, it quantifies the size of the prize."
Answered. Harness: `tools/measure_exploitability.py`.

At 24 genuine decision points across 4 opponent teams, the real payoff matrix
`A[i][j]` was built and four values compared:

| Quantity | Meaning |
|---|---|
| `assumed` | `A[i*][j_greedy]` — what the current solver thinks it gets |
| `actual` | `min_j A[i*][j]` — what it gets against a best-responding opponent |
| `maximin` | `max_i min_j A[i][j]` — what the safest *pure* play guarantees |
| `nash` | equilibrium value, allowing mixing (regret matching) |

**Re-measured after fixing the evaluation.** §2c.4 found the original matrix
was not antisymmetric, so it was not really zero-sum and "the opponent
minimises our heuristic" was a wrong model of their objective.
`heuristic_eval` has since been made antisymmetric **at source** (Phase A1,
§2d) — verified at 0.0 asymmetry across 8905 states — and the baseline
re-measured on it.

| | original (one-sided eval) | **corrected** | change |
|---|---|---|---|
| decision points | 24 | 24 | |
| mean self-delusion (assumed − actual) | 232.3 | **174.4** | **−57.9 (−25%)** |
| mean regret vs maximin | 48.4 | **40.9** | −7.5 |
| turns where maximin ≠ greedy pick | 14/24 (58%) | **13/24 (54%)** | −1 |
| turns where Nash is MIXED | 14/24 (58%) | **20/24 (83%)** | **+6 (+25pp)** |

```
units: heuristic_eval points, KO_WEIGHT = 180, so ~180 points ≈ one Pokemon
```

**The case for the project survives, and its two halves move in opposite
directions.** The exploitability claim is **25% smaller** than originally
reported — a quarter of the headline number was an artifact of the broken
evaluation, not a real finding. It is still ~0.97 Pokémon per turn, which is
large. The mixed-strategy claim gets substantially **stronger**: from 58% to
**83%** of turns, because the old asymmetry was masking genuine equilibrium
mixing.

That matters for phase ordering. The strongest measured argument for Phase B is
now *"the current architecture cannot represent the right answer on 83% of
turns"* rather than *"it overestimates itself by 1.3 Pokémon"* — a
representational argument, not an accuracy one, and one that no amount of
evaluation tuning can address.

Read plainly, on the corrected numbers:

- **The current solver overestimates its own position by ~0.97 Pokémon per
  turn.** That is the cost of scoring against a policy we authored. Every win
  count in every tab inherits this bias.
- **On 54% of turns it plays a different move than the maximin play**, and
  gives up ~41 points (~0.23 of a Pokémon) per turn in the worst case by doing so.
- **On 83% of turns no pure strategy is optimal at all** — the equilibrium has
  support ≥ 2. The current architecture *cannot represent* the right answer on
  the large majority of turns, regardless of how good its evaluation gets.
- **Mixing is worth 50–86 points** on the turns where it matters (`nash` minus
  pure `maximin`; e.g. Rain T3 130.2 vs 43.9). Pure maximin is a floor, not the
  target — which is the argument for solving for Nash rather than just taking
  the safest row.

Two honesty caveats on these numbers:

1. ~~`A` is built from `heuristic_eval` and treated as zero-sum...~~
   **Addressed.** The asymmetry was measured (§2c.4, 277.7 points mean), traced
   to the `seen` filter at `solver.py:290-298`, and fixed: `solver.leaf_value`
   now scores `(eval(s,"p1") − eval(s,"p2")) / 2`, antisymmetric by
   construction and covered by `tests/test_leaf_value.py`. The symmetric column
   above is computed on that. This caveat is retired.
2. One measured point is already-lost terminal state (`-10000`), which
   contributes 0 to both means and therefore makes both figures slightly
   conservative.

**Conclusion: the prize is large and the overhaul is justified.** The dominant
error is not evaluation quality — it is that we optimise against a fixed
self-authored policy and cannot express mixed strategies.

> Caveat 1 above turned out to be a much bigger deal than "a proxy, not
> identical". It is now measured: §2c.

---

## 2c. Step 0: measurements taken before building (2026-08-08)

Everything above this line was estimated. This section is measured, with a real
double-oracle implementation (`src/matrix_game.py`, tested against textbook
games in `tests/test_matrix_game.py`) and three harnesses in `tools/`. Three of
the design's load-bearing assumptions did not survive.

### 2c.1 Double oracle prunes far less than assumed

`tools/measure_depth2_cost.py`, 8 decision points, mean matrix 20.5 × 24.0:

| Solver | sims/decision | sec/decision | vs `do1` |
|---|---|---|---|
| `brute1` — full matrix, depth 1 | 496 | 0.34 | 4.2× |
| `do1` — double oracle, depth 1 | **118** | **0.08** | 1.0× |
| `do2` — double oracle, depth 2, equilibrium-valued | 6778 | 3.88 | **47.9×** |
| `do2_sel` — depth 2 over the top-4 depth-1 rows | 2108 | 1.22 | 15.0× |
| `do2_greedy` — depth 2 with a greedy inner playout | 1996 | 0.77 | 9.6× |

Double oracle **is exact** — `|do1 − brute1|` is 0.05 points mean, 0.08 max,
and it converged at 8/8 points, so §3's "no accuracy is given up" claim holds
(also verified against 25 random matrices in the test suite). But it
materialises **24% of the full matrix, not ~2%**. The reason is structural and
worth writing down, because it caps how much pruning is ever available:

> Double oracle's best-response step must evaluate **every** action of one
> player against the *support* of the other's mixed strategy. That is
> `|actions| × |support|` payoff evaluations per iteration, no matter how small
> the final restricted subgame is. The restricted game really is small
> (support 2–5, as §2b found); getting to it is not.

A corollary worth having in hand for Phase B: **on small matrices double oracle
is not worth using at all.** The inner 8×8 games below evaluated ~57 of 64
cells — the best-response scan costs nearly the whole matrix, and brute force
is simpler and about as fast. Use DO for the large outer game, brute force
inside.

### 2c.2 Depth 2 costs ~48×, not the estimated ~10×

`do2` is 3.88 s per decision — a 12-turn game would be ~47 s. That is fine for
a single interactive decision in Battle Viewer (~4 s) and **impossible inside
any sweep**. This is already the optimistic figure: the inner game was capped
at 8 actions per side (`INNER_CAP = 8`); uncapped it is worse.

Selective deepening does cut it (15× rather than 48×), **but it changes the
answer**: `|do2 − do2_sel|` is 58.8 points mean, 208.8 max — up to ~1.2
Pokémon. So `k = 4` is not a safe approximation, and open question 7's
sub-question ("what is the smallest `k` that preserves the ranking?") is now
known to have an answer larger than 4.

### 2c.3 The greedy-inner-playout trap is real, and costs ~1 Pokémon

`|do2 − do2_greedy|` is **175.8 points mean, 255.9 max**. With
`KO_WEIGHT = 180`, evaluating the turn-*t+1* subgame with a greedy playout
instead of an equilibrium solve costs almost exactly one Pokémon per decision —
and note how close that is to §2b's 232.3 self-delusion figure, which is the
same error measured one level up. §10's insistence that the recursion be
equilibrium-valued at both levels was correct, and the cheap-looking shortcut
(9.6× instead of 47.9×) would have silently reintroduced the exact bias the
redesign exists to remove.

### 2c.4 `heuristic_eval` is far less antisymmetric than assumed

`tools/measure_antisymmetry.py`, 9434 states across 23 decision points:

```
mean   |eval(p1) + eval(p2)|      277.7      (0 if perfectly antisymmetric)
median                            301.8
max                               627.8
mean   |eval(p1)|  (for scale)    233.0
perfectly antisymmetric states    1178/9434  (12%)

maximin pick CHANGES if symmetrised  15/23  (65%)
```

**The asymmetry is larger than the signal** — 277.7 against a mean |eval| of
233.0, and larger than the 232.3 self-delusion that motivated the whole
redesign. The cause is exact and fixable: the `seen` filter is applied only to
the opponent (`solver.py:290-298`). `my_hp` sums the full roster; `opp_hp` sums
only revealed mons. So

```
eval(s,"p1") + eval(s,"p2")  =  100 × (hidden HP on BOTH sides)  + KO-term analogue
```

which is ≥ 0, shrinks as the battle reveals, and — critically — **is not a
constant offset**, so it does not cancel out of an argmax. Different cells
reveal different amounts (a KO forces a reveal), which is why the maximin pick
moves on 65% of turns.

Consequence for §2b: its matrix was built from `eval(·, "p1")` and solved as
zero-sum. With an asymmetry this size, "the opponent minimises our heuristic"
is a materially wrong model of their objective. **The direction of §2b's
conclusion is unaffected** — the greedy solver is still measurably exploitable
and cannot represent mixed strategies — but its specific magnitudes (232.3,
48.4) should be treated as provisional until re-measured on a symmetrised eval.

This promotes open question 5 from "worth doing as part of Phase A" to **a
prerequisite for trusting any matrix-game number at all**, including the
baseline that justified the project. The cheap fix — score
`(eval(s,"p1") − eval(s,"p2")) / 2` — is antisymmetric by construction and adds
one `heuristic_eval` call per cell (0.11 ms against a 0.42 ms `run_turn`, so
roughly +20% per cell, not +100%).

---

## 2d. Phase A1 (done): the evaluation is now genuinely zero-sum

`heuristic_eval` is antisymmetric **at source** — measured at exactly 0.0 across
8905 states, 8905/8905 perfect, and symmetrising now changes the maximin pick on
**0/24** turns (it changed 65% before). Two fixes, and the first attempt at each
was wrong in an instructive way.

**Fix 1 — HP.** The `seen` filter was applied only to the opponent: `my_hp`
summed the full roster, `opp_hp` only revealed mons. Removed entirely for this
term, because it was guarding a disclosure that cannot occur: **a Pokémon that
has never been on the field is necessarily at full HP**, and the number of live
Pokémon each side has left is public. Counting hidden mons at their (always 1.0)
HP fraction leaks nothing.

**Fix 2 — KO-threat value.** This term reads Attack/Sp.Atk, so hidden opponents
were placeholdered at a flat 1.0 while our own bench used real values. Now both
rosters use the real value always. That relaxes the old no-leak guard, and it is
sound *here* because the evaluation is always conditioned on a **hypothesised**
enemy bring — `search_robust_composition` sweeps over brings rather than peeking
at one — so within a hypothesis the species are known, and `_ko_threat_value` is
a coarse clamp on attacking stats that discloses nothing about moves, items or
EV spreads, which are what actually stay hidden.

### The trap: averaging is not a fix

The obvious implementation — score `(eval(s,"p1") − eval(s,"p2")) / 2`, as this
document previously recommended — **is antisymmetric and still wrong.** Over a
one-sided heuristic it converts a *side* asymmetry into a **reveal incentive**:

```
(eval_p1 − eval_p2)/2 = ½[(p1_all + p1_seen) − (p2_all + p2_seen)] × 100
```

so a hidden mon is weighted 0.5× and a revealed one 1.0×. Switching a healthy
Pokémon in therefore manufactured exactly **+50 points** (0.5 × 100) out of an
information update rather than any real progress, and the solver started
preferring switches almost everywhere. The same trap appeared a second time, one
term over: placeholdering the KO-threat value *by reveal state* made flipping
`revealed` worth up to **+63 points** (0.35 × `KO_WEIGHT`).

Both were caught immediately by `tools/golden_baseline.py` and are now pinned by
regression tests in `tests/test_leaf_value.py`. The general lesson is worth
carrying into Phases B–D: **a value function must not change when information
changes unless the underlying position changed.** Antisymmetry is necessary and
not sufficient.

`solver.leaf_value()` is now the single scoring entry point, so the pending
points → P(win) change (open question 6) is one edit rather than a hunt.

---

## 2e. Phase A2/A3 (done): threat matrix shipped, coverage term parked

**The threat matrix is built, tested and useful** (`src/threat.py`,
12 tests). It computes both directed edges for every pair, costs **0.7 ms** for
a 4×4 (32 edges) — the same order as one `run_turn`, as §4a predicted — and its
output is inspectable via `tools/show_threat_matrix.py`. Spot-checking one
matchup: Farigiraf reads 0% into Grimmsnarl (Psychic into Dark), Gallade 111%
into Archaludon, Hydreigon OHKOs and outspeeds both Metagross and Pelipper.

§8c.3's joint dimension paid off immediately: in that single matchup it found
**7 focus-fire pairs** — two of ours that together KO one of theirs where
neither OHKOs alone — which a purely pairwise matrix structurally cannot
represent.

### The coverage evaluation term is measured, and parked

`COVERAGE_WEIGHT = 0.0`. The term works and is covered by tests; it simply does
not earn its cost yet.

| | |
|---|---|
| cost | `heuristic_eval` 0.028 ms → **0.702 ms (25×)**; one matrix **cell** 0.201 ms → 0.876 ms (**4.35×**) |
| benefit | head-to-head vs the same solver with the term off: **3 W / 5 L at weight 0.25, and again at 0.10** |

n = 8 is far too small to claim the term is *worse* — 3–5 is well inside noise.
But there is no evidence it is better. Parked rather than deleted.

**Update after caching (§2f):** the cost objection has largely been answered —
a matrix cell is now **1.83×**, not 4.35×.

**Correction (§2g): the win-rate figures above were measured on a biased
harness and should be disregarded.** The 38% / 38% / 41% readings, and the
inference that three samples "all sit below 50%", were artifacts — that harness
scored **44% on its own null case**. Re-measured on the fixed harness over
**160 games**, the coverage term scores **49%, 95% CI [42%, 57%]**: neutral, not
mildly harmful.

`COVERAGE_WEIGHT` still stays 0.0, but for the correct reason — **no measurable
benefit, at a 1.83× cost per cell** — rather than because it looked actively
bad. The `coverage_differential` hypothesis recorded above and in
`tests/test_coverage_term.py` remains the thing a fourth attempt should fix.

Three things surfaced that are worth keeping regardless of the verdict.

**1. The measurement gap this exposed.** Neither existing harness can validate
an *evaluation* change. `measure_exploitability` scores how exploitable the
greedy solver is — that number moved from 174.4 to 585.8 when coverage was
switched on, but it moves for two indistinguishable reasons (the evaluation got
better, or merely noisier across columns). `golden_baseline` detects change by
design and says nothing about direction. So `tools/measure_headtohead.py` was
added: two configurations play the same positions, sides swapped on alternate
matchups. **Every future evaluation change should go through it**, and its
sample size wants to be much larger than 8.

**2. Matching must penalise unanswered threats.** A matching pairs at most
`min(threats, answerers)`, so when a Pokémon is lost the matching can simply
*drop the threat it handled worst* and report **higher** coverage than before —
losing a piece looking like an improvement. Fixed with an explicit
`NO_ANSWER_PENALTY` worse than any real answer.

**3. Coverage must be a mean, not a sum.** The sum scales with the number of
threats, so subtracting one side's from the other's confounds "our answers got
worse" with "there are fewer Pokémon left to answer". Losing a Pokémon is
already priced by the KO and HP terms; this term measures answer *structure*.

Normalised and penalised, **our** coverage behaves exactly as §4b claims:
losing Hydreigon (113.1 → 31.3) or Gallade (→ 34.6) collapses it, while losing
Farigiraf (→ 74.7) barely moves it. The honest caveat is that
`coverage_differential` — the antisymmetric wrapper the zero-sum leaf value
requires — is a **weaker signal** than its own first half, because the second
half moves when our roster size changes. Recorded in
`tests/test_coverage_term.py` rather than smoothed over.

---

## 2f. Threat-matrix caching (done)

The threat matrix was rebuilt from scratch at every leaf, which is what made it
a 4.35× tax on the hottest path in the system. It is now cached, and the cache
turns out to be close to ideal:

```
realistic workload: a full 23x23 payoff matrix = 529 DISTINCT leaf states
  coverage off : 0.258 ms/cell
  coverage on  : 0.473 ms/cell   (1.83x, was 4.35x)   cache hit rate 98%
```

Only **280 distinct damage computations** were needed across those 529 leaf
states. The reason the hit rate is so high is worth stating, because it also
says when the cache will *stop* working: damage depends on stats, stages,
items, status and field, and those mostly do **not** differ between sibling
leaves of one turn — only HP does, and HP enters damage through exactly one
narrow path (Multiscale). A search that branches heavily on stat drops or item
consumption would see a materially lower hit rate.

### Two correctness traps, one of them a live bug

**A stale cache does not crash — it returns plausible wrong numbers**, so the
key has to cover everything `damage_roll` genuinely reads. Two entries are easy
to miss, and `tests/test_threat_cache.py` pins both by mutating the field and
asserting the answer moves:

- **`defender.item`**, because Knock Off reads it.
- **The full-HP flag.** `Multiscale` halves damage only at full HP
  (`damage.py:281`), so **damage is not independent of current HP** — the most
  natural assumption to make when designing this cache, and wrong.

Separately, building the key surfaced a **pre-existing bug in the threat
matrix**: it called `damage_roll` with `weather` only, while the engine
(`battle.py` `_resolve_move`) also passes `auras` and `screens`. So every
threat edge silently **over-estimated damage into a screened side** and
mis-handled Fairy/Dark Aura. Now fixed, with screens applied per category
(Aurora Veil both, Reflect physical, Light Screen special) to match the engine.

This is the same lesson as §2d in a different guise: the failure mode of an
optimisation here is not a crash but a plausible wrong number, so each
dependency needs a test that deliberately breaks it.

---

## 2g. Phase A4, and a measurement instrument that was lying

### The harness was biased, and it invalidated earlier conclusions

`measure_headtohead.py` originally swapped sides on **alternate matchups**. That
sounds equivalent to swapping properly and is not: the two halves are then
*different matchups*, so any per-matchup skew survives the swap instead of
cancelling.

This was caught by finally running the control that should have come first —
configure the candidate **identically** to the baseline, which must by
definition score 50%. It scored **44%**. That is comfortably enough bias to
swamp the effects being measured, and it means every head-to-head number
reported before this point was unsound, including the coverage verdict in §2e.

Fixed by **paired play**: every matchup is played twice, once with the
candidate on each side, and both results counted. When the configurations agree
the two games cancel exactly, so the null is 50% *by construction* — verified,
39 W / 39 L / 2 D over 80 games. The tool's docstring now says to run the null
before trusting any result from it.

The lesson generalises past this harness, and is the third instance of the same
shape in Phase A (after §2d's reveal incentive and §2f's stale-cache traps):
**the failure mode of measurement code is a plausible wrong number, not a
crash.** A null case is the cheapest possible defence and it was skipped.

### A4 result: reshaping the HP term does nothing measurable

§8b.1 established that "1 HP is infinitely more than 0 HP" is about **HP
non-linearity**, not damage rolls. Implementing it turned out to need a smaller
change than expected, because the evaluation already encodes most of the claim.
Per **alive** Pokémon:

```
KO term   _ko_threat_value in [0.35, 1.35] x KO_WEIGHT  ->   63..243 points
HP term   current_hp_frac                  x 100        ->     0..100 points
```

Dying already costs far more than being chipped to 1 HP. So the open question
was never "is there a step" but **"is the balance between the two right"** —
one number, `FUNCTIONAL_FLOOR`, the share of a Pokémon's HP-term value it keeps
merely by being alive. `0.0` is the previous linear behaviour, `1.0` ignores HP
entirely.

Measured on the fixed harness:

| `FUNCTIONAL_FLOOR` | games | win rate | 95% CI |
|---|---|---|---|
| 0.25 | 80 | 55% | [44%, 66%] |
| 0.25 | **240** | **51%** | **[44%, 57%]** |
| 0.50 | 80 | 48% | [37%, 58%] |
| 0.75 | 80 | 43% | [32%, 54%] |

The promising 55% at n=80 **did not survive** tripling the sample — it fell to
51%, which is nothing. `FUNCTIONAL_FLOOR` stays `0.0`, with the knob and its
tests kept so the question can be reopened cheaply.

That n=80 → n=240 collapse is worth remembering on its own: at 80 games the
95% interval is roughly ±11 points, so this harness simply cannot see effects
smaller than about ten points, and reading a point estimate without its interval
would have shipped a non-improvement.

### What Phase A's negative results actually say

Two evaluation terms with strong backing in the VGC literature — answer
preservation and functional HP value — were implemented, tested, and measured
at **49%** and **51%**. Neither is worth its cost.

That is a real result rather than a failure, and it points somewhere specific.
§2b (re-measured, §2d) found the current architecture cannot represent the
right answer on **83% of turns**, because the equilibrium is mixed and the
solver can only return a pure strategy. Phase A has now shown that *evaluation*
changes of the kind the literature suggests do not move win rate detectably. The
two findings agree: **the binding constraint is representational, not
evaluative**, which is precisely the argument for Phase B and against further
evaluation tuning.

The threat matrix (§2e) is kept regardless — it is infrastructure for action
pruning, double-oracle seeding and the answer map, none of which depend on the
coverage term.

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

### What this is *not*: a note on CFR tractability

Anyone arriving from poker solvers will raise the standard objection, and it
was raised verbatim in the Smogon thread (§9): *vanilla CFR+ requires solving
the entire game tree, and growing-tree CFR requires a value estimator that is
tricky and expensive to train.* Both halves are true, and neither applies here,
because **we are not running CFR over the game tree.**

The object being solved is a **single turn's payoff matrix**, whose cells are
one `run_turn` scored by a hand-written leaf evaluation (§4). There is no
full-tree traversal, and no learned value function to train — the leaf value is
`heuristic_eval`, which already exists. "Regret matching" above refers only to
the *inner solver for one matrix*, used as an alternative to an LP; it is not
CFR over an extensive-form game, and the resemblance in name is the only thing
the two share.

This is what makes the §2 cost numbers credible, and it is the reason this
design is tractable in a way full-game CFR would not be. The corresponding
weakness is the honest one: our equilibrium is only as good as the leaf value,
which is exactly why Phase A comes first.

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

### 4c. Roll awareness (survival thresholds)

> Retitled. This section used to be headed *"1 HP is infinitely more than 0
> HP"*, on the assumption that the vgcguide article of that name was about
> damage rolls. It is not — see §8b.1. The roll-bucketing argument below stands
> on its own engine-fidelity merits; the article's actual content is §4d.

Average-roll determinism is the wrong abstraction for exactly the situations
that decide games. But 16 rolls per damage event is a combinatorial explosion.

The technique is **outcome bucketing**: group the 16 rolls into equivalence
classes by *outcome* — typically {kills, doesn't kill}, sometimes 3 buckets —
and weight branches by class probability. Cost goes from ×16 to ×2, and it
preserves the only distinction that matters: **did it die**.

> **Correction (§9):** the previous draft attributed this to *FoulPlay* as
> prior art. That attribution is **wrong, and backwards.** I read the source:
> `poke-engine`, the engine FoulPlay searches with, defines
> `pub enum DamageRolls { Average, Min, Max }` and applies a flat multiplier
> (`×0.925`, `×0.85`, `×1.0`) — i.e. **exactly the min/max/avg scheme this
> section proposes to replace**, with the instruction generator calling
> `DamageRolls::Max`. There is no bucketing anywhere in it. So this idea has
> no external prior art behind it in the sources we have; it stands or falls
> on the engine-fidelity argument below, which is checkable against our own
> `damage.py`.

Concretely: replace the `min/max/avg` roll mode with an outcome-bucketed
distribution, and make the leaf value an expectation over buckets. This makes
"survives on 15/16 rolls" and "survives on 1/16" different numbers, which
Focus Sash, Sturdy, Multiscale, and every bulk-EV decision depend on.

### 4d. Functional value ("1 HP is infinitely more than 0 HP")

*Added from the real article — see §8b.1. Scoped into Phase A.*

`heuristic_eval` scores HP linearly (`score += (my_hp - opp_hp) * 100`,
`solver.py:327`). Wolfe's article is written directly against that model: "All
of your Pokemon will function in **exactly the same way** no matter how much
health they have left." A mon's contribution is mostly a step function — alive
or not — plus a bulk term, not a proportional one.

Three components, all computable from `T` and existing state:

1. **Re-entry opportunity.** A preserved mon is only worth preserving if it can
   come back in safely. The article enumerates the routes: after one of ours
   is KOed, off a self-switch move (`self_switch` is already parsed at
   `solver.py:57`), into an immunity or heavy resist, or behind a partner's KO.
   All are checkable against `T`.
2. **Later impact.** Does it move first (speed, or priority — Fake Out,
   Follow Me, Grassy Glide), or does it bring an on-switch-in ability
   (Intimidate, weather, terrain — `on_switch_in` already exists in
   `engine.py`)? If neither, a preserved mon is worth little beyond (3).
3. **Sacrifice value — a floor, not zero.** Even a mon with no offensive future
   is worth something: it re-enables an ally's switch-in ability, buys a free
   end-of-turn switch, lets a teammate reset stat drops, or burns a turn of the
   opponent's Trick Room/Tailwind clock (which links this term to §8c.5).

The article is also explicit that preservation has a *cost* — "it can be risky
to switch, and if the turn doesn't go in the way you anticipate you may end up
in a worse position than if you'd simply sacrificed your Pokemon" — which is
`min_j A[switch][j]` and needs no separate machinery once §3 exists.

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

The previous draft proposed solving `B` for its **Nash equilibrium** and taking
the equilibrium *support* as the set of credible brings, with double oracle to
materialise only that support. **That is now revised** — see below. The part
that survives unchanged is the framing: preview is simultaneous, not worst-case,
and today's uniform-over-90 is wrong.

### 6a. Revised: bounded rationality, not equilibrium

Equilibrium support is the wrong model of a real opponent's bring, for a reason
that is easy to state and hard to get around: **real players bring somewhat
unexpected leads, even though they won't bring their worst.** Nash support is a
hard cutoff — a bring is either in the support or it has probability zero — and
that cutoff is exactly what a real opponent violates. It is also fragile in a
way that compounds with §10's argument: support membership is decided by cell
values we have measured to be biased, so a hard cutoff turns a noisy number
into a categorical claim.

The better model is a **soft plausibility weight over all 90**, not a
partition into credible and impossible:

```
P(their bring j) ∝ exp( value_to_them(j) / τ )
```

A logit / quantal-response weighting, with one tunable temperature `τ`. The
useful property is that `τ` spans everything of interest, and both current
proposals are its endpoints:

| `τ` | Behaviour | Corresponds to |
|---|---|---|
| `τ → ∞` | uniform over all 90 | **today's `search_robust_composition`** |
| `τ` mid | good brings likelier, bad brings rare but possible | the model we actually want |
| `τ → 0` | all mass on their best response | Nash support / the previous draft |

This is strictly more honest than either endpoint, and it is *far* cheaper to
build than an equilibrium solve: it is a weighting over the 90 we already
enumerate, with no LP, no double oracle, and no fixed-point iteration. `τ` can
be fitted later against real games; until then it is one number to tune, and
its effect is inspectable.

### 6b. The objective: don't lose to a bad lead

"How many of the 90 do we beat" is a count. Nash gives an expected value.
Neither matches the actual goal, which is **not to lose on a bad lead
match-up** — an asymmetric, downside-focused objective. Formally that is a
tail measure over the plausibility-weighted distribution rather than a mean or
a worst case:

```
score(our bring) = CVaR_α over j ~ P(j) of  B[our bring][j]
```

i.e. the average outcome across the worst `α` fraction of *plausible* brings.
Two knobs, both meaningful: `τ` says how sharp the opponent is, `α` says how
much we care about the tail. Setting `α = 1` recovers the mean; `α → 0` with
`τ → ∞` recovers today's worst-case-over-90. The recommendation is a middle
setting on both, and the deliverable is a metric that says "against the brings
they would plausibly pick, your bad match-ups look like *this*" — which is the
question actually being asked.

This also disposes of the strawman-bring problem without pruning anything: an
absurd bring gets a small weight rather than being deleted, so it can still
show up in the tail if it is *catastrophic* for us, which is the one case where
we do want to hear about it. Hard support-pruning would silently drop exactly
that case.

### 6c. What this costs, and what it gives up

The runtime win shrinks, and that should be stated plainly. Double oracle
promised 10–20× by never materialising most of the 90; a weighting over all 90
materialises all of them and saves nothing by itself. Recovering runtime now
means ordinary approximation rather than an exactness guarantee: evaluate all
90 cheaply (Tier 0/1), then re-evaluate only the top and bottom slices at
Tier 2 — the tail is what the objective reads, and the middle barely moves
`CVaR`. That is a sampling argument, not a proof, and unlike double oracle it
gives up the "no accuracy lost" guarantee. Given §10's finding that the cells
are biased anyway, an exactness guarantee over biased cells was worth less than
it appeared.

What is genuinely lost: the **credible-bring list** as a crisp equilibrium
support (§7 listed it as a user-facing output). It becomes a *ranked
plausibility list* instead — arguably more useful to show, and certainly more
honest, but it is a ranking, not a set.

This still serves the brief's "solve the easier 6/6 problem": a guaranteed lead
is just conditioning `B` on a row.

---

## 7. Proposed tiered architecture

Keep the existing screen-then-verify shape; add tiers rather than replacing.

| Tier | Engine | Cost | Used for |
|---|---|---|---|
| 0 | `fast_eval` greedy-vs-greedy | ~1 ms | Pair matrix, beam search over thousands of teams |
| 1 | Current expectimax vs greedy | 0.05 s/game | Broad sweeps where a rough verdict suffices |
| 2 | **Double-oracle Nash per turn** (new) | **measured (§2c): 0.08 s/decision ≈ 0.96 s/game at depth 1; 3.88 s/decision ≈ 47 s/game at depth 2** | Depth 1: real verification, Vs Team. Depth 2: Battle Viewer and punish analysis only — too slow for sweeps |
| 3 | **Plausibility-weighted preview** (new, revised — §6) | seconds | Bring selection, team rating, the headline number |

~~Tier 2 replacing Tier 1 as the default is *cost-neutral* per the §2 numbers.~~
**Definitively false, now measured (§2c).** Cost-neutrality was wrong even at
depth 1: double oracle is 0.08 s/decision (~0.96 s/game) against ~0.05 s/game
for the current greedy solver — about **20×**, not parity. At the depth 2 that
Phase B requires it is ~47 s/game, roughly **1000×**. Tier 2 cannot replace
Tier 1 as a blanket default; the tiers must coexist, with depth chosen per
call site. Open question 3 is settled by this.

New user-facing outputs this unlocks, all of which the current model cannot
express:

- **Exploitability score** per team ("a perfect opponent gains X against you")
- **Mixed-strategy recommendations** ("T1: Fake Out Gengar 70% / Protect 30%")
- **Ranked plausibility list** of their brings (§6a), instead of 90 flat rows —
  a ranking rather than the crisp equilibrium-support set the previous draft
  promised
- **Downside score** — "against the brings they'd plausibly pick, your worst
  match-ups look like this" (§6b), replacing "beats N/90"
- **Answer map** from the threat matrix — who on your team answers what
- **Roll-sensitivity** — which wins are 15/16 and which are 8/16

---

## 8. VGC principles → formal mapping

**COMPLETE.** All 11 articles retrieved and read 2026-08-08.

*Before the battle:* approaching-best-of-1-vs-best-of-3 · analyzing-your-opponents-teams · team-preview · what-is-a-game-plan
*General principles:* what-is-pressure · predictions · protect-in-battle · switching
*Specific concepts:* battling-against-trick-room · 1-hp-is-infinitely-more-than-0-hp · how-to-analyze-a-battle

Worth noting up front: the corpus has three authors with visibly different
angles, and the disagreements are informative. **Aaron Traylor** (pressure,
half of team-preview) writes in something very close to game-theoretic
language and is the source of most of the confirmations below. **Aaron Zheng**
(predictions, protect, switching, game-plan, bo1-vs-bo3, analyzing-teams,
half of team-preview) is procedural and game-state-conditional — he is the
source of most of the *corrections*, because his advice repeatedly departs
from equilibrium play on purpose. **Wolfe Glick** (trick-room, 1-hp) writes
about resources and tempo, and supplies two ideas the doc had no slot for at
all.

### 8a. Confirmed mappings

These survive contact with the real text, in several cases more strongly than
the placeholder claimed.

| VGC concept | Formal counterpart | Where | Evidence |
|---|---|---|---|
| Safe plays | Maximin / security level of the turn matrix | §3 — the objective | Stated verbatim, twice, by two authors: "Do you have a 'safe' play that succeeds regardless of what your opponent goes for?" (*game-plan*) and "Did I make the best possible play, **regardless of what my opponent could go for**?" (*how-to-analyze*) |
| Punishing a committed opponent | Best response; `min_j A[i][j]` | §3a | "Was my play this turn unnecessarily risky? **Even if it worked out**, was there a way my opponent could have punished me that they missed?" (*how-to-analyze*) |
| Who threatens who | Threat matrix `T` | §4a | This is exactly what *pressure* is — see 8b.2 |
| Preserving an answer | Max-weight bipartite matching | §4b | "What Pokémon can you absolutely not afford to lose? **If you lose a Pokémon, what Pokémon on their team are freed from its pressure?**" (*team-preview*, Traylor). Independently in *bo1*: "Have answers against all 6 of your opponent's Pokémon" |
| Speed as a component of threat | `outspeeds` in `T` | §4a | "Which Pokémon is moving first this turn? … Speed plays an important role in pressure" (*pressure*) |
| Team preview is a game | Preview-level matrix game | §6 | Traylor states the fixed point in prose: "I imagine my opponent asking themselves **the same questions that I'm asking myself, but from their point of view**" |
| Most brings are implausible | Low plausibility weight, §6a (*not* equilibrium support — see §6a for why the hard cutoff was dropped) | §6 | "Can you rule out any Pokémon on each side? … If you can eliminate some of their potential options, you can get a better idea of what Pokémon they will actually bring." Note the article immediately hedges: "**Be careful, though: opponents don't always act how you think they will**" — a soft weighting, not a partition |
| Bring strategy must mix | Preview equilibrium is mixed, not pure | §6 | "Many teams have **more than one mode and will force you to choose to prepare for one of them**" (*team-preview*). You cannot cover all modes; therefore no pure bring is optimal — this is the direct argument against today's worst-case-over-90 |

The §4b confirmation is the strongest result here. Traylor's formulation —
losing a piece *frees* an enemy piece from its pressure — is precisely a
matching edge being deleted, and it is the exact intuition §4b was designed
around, arrived at independently.

### 8b. Corrections — placeholder rows that were wrong

Three of the guessed rows were not merely vague; they pointed at the wrong
formalism. One more was half-right.

**1. "1 HP is infinitely more than 0 HP" is not about damage rolls.**

The placeholder mapped it to §4c outcome-bucketed rolls. Wolfe's article is
not about rolls at all — it never mentions a damage roll. Its thesis is:

> All of your Pokemon will function in **exactly the same way** no matter how
> much health they have left — a Pokemon at low HP won't do less damage than
> if it was fully healthy.

That is a claim that **value is not linear in HP**, and it lands squarely on
`heuristic_eval`, which currently has `score += (my_hp - opp_hp) * 100`
(`solver.py:327`) — perfectly linear, i.e. the exact model the article is
written against. The article's actual content decomposes into three
computable conditions on whether a damaged mon is worth preserving:

- *"Will you be able to bring it in later?"* — a **re-entry opportunity**
  term. The listed routes are enumerable from state: after a KO, off a
  self-switch move (`self_switch` is already parsed in `solver.py:57`), into
  an immunity, or behind a partner's KO.
- *"Will the Pokemon be able to have an impact later?"* — moving first
  (speed, or priority), or an on-switch-in ability (Intimidate, weather,
  terrain). Note `on_switch_in` already exists in `engine.py`.
- **Sacrifice value** — a mon at 1 HP retains positive value purely as a
  sacrifice: it re-enables an ally's switch-in ability, buys a free end-of-turn
  switch, resets stat drops, or burns a turn of the opponent's Trick
  Room/Tailwind clock.

So this article belongs in **Phase A**, as a shape change to the HP term plus
a floor for functional value, not in Phase C. Phase C (roll bucketing) is
still worth doing, but its justification is now entirely engine-fidelity —
Sash/Sturdy/Multiscale, survival thresholds, the FoulPlay prior art — and it
has **no support in the VGC principles corpus**. That materially lowers its
priority; see §10.

**2. "Pressure" is the threat matrix, not the opponent's security level.**

The placeholder guessed "opponent's maximin value falling, i.e. all their
options lose material", and predicted this row would drive eval design.
Traylor's definition is narrower, pairwise, and directional:

> The threat of proactive action is how I define **pressure**; i.e., Pikachu
> pressures Gyarados.

That is `T[a][b]` — an edge, not a scalar. Pressure does **not** introduce a
new eval term; it validates §4a and gives `T` a *second* job the doc had not
assigned it. Traylor is explicit that pressure's main use is predicting the
opponent:

> pressure is your guide to your opponent's thought process: what are they
> threatened by? What proactive moves are their safest path to victory? By
> doing so, you'll figure out **which moves they are most likely to make**.

That is an opponent action prior, derived from `T`. Concretely, it is the
seeding rule for double oracle (§3): the initial restricted column set should
be the actions justified by pressure edges, and the prior over their columns
in §3d should be weighted by them. `T` therefore has to be built before the
matrix machinery can be seeded well — a dependency that matters for §10.

The security-level idea in the placeholder is real, but it belongs to a
different article — see 8c.4.

**3. Protect is not primarily about scouting.**

The placeholder guessed "information gain; needs the belief state of §5". The
article is ~2000 words and information gain is not among them. The real
content is three things, all cheaper than a belief state:

- **The 1/3 consecutive-Protect probability is a first-class strategic
  object.** "The odds of consecutive Protects are only ⅓. If you fail to get
  the Protect off, then your Pokémon will be **completely useless** for the
  turn." Good news: `battle.py:421-436` already models this as a hard fail on
  `protected_last_turn`, which is *stricter* than the real 1/3 rule. That
  strictness is defensible for a solver (the code comment says as much) but it
  makes Protect strictly worse than reality on the second turn, and the
  article treats the gamble as sometimes correct — "in the right situation,
  going for consecutive Protects can be game-defining". Once the matrix game
  exists, the honest model is a stochastic action with a 1/3 branch, which the
  matrix can price properly rather than banning.
- **Protect's drawbacks are exactly the opponent's column.** The article's
  list of what you give the opponent — set up, switch out for free, set speed
  control, "double target your other Pokémon" — is precisely `min_j A[protect][j]`.
  This is a clean confirmation that Protect needs no special-case logic: solve
  the matrix and its cost appears. Today's `greedy_opponent_joint_action`
  hard-codes `protect: return -1`, so the model cannot even represent the
  cost, let alone the benefit.
- **Usage-conditioned Protect priors.** "only 5% of Incineroar run Protect,
  while 97% of Zacian run Protect." This is a per-species prior on their
  action distribution, and it plugs directly into §3d's prior mechanism. It
  needs a usage table, not a belief state.

**4. "Predictions" is only half mixed-strategy play.**

The placeholder mapped predictions → "mixed strategy over the equilibrium
support". Zheng splits the skill in two, and only the first half is that:

> Your ability to anticipate **every single possible combination** of moves
> that your opponent can go for [vs] your ability to anticipate **a specific
> play** that your opponent is going to go for & reacting accordingly.

The first is building the full column set of `A` — enumeration, which the
matrix formulation gives for free. The second is *deliberately committing
off-equilibrium*, and Zheng conditions it on game state:

| Game state | When to commit to a read |
|---|---|
| Ahead | Right outright wins; **wrong does not lose the lead** |
| Neutral | Right swings the game significantly |
| Behind | "You don't feel like you have a 'safe' play that covers for all your opponent's options"; "you don't think you can win if your opponent finds the correct play, so you look for opportunities where they may **make a mistake**" |

This is not Nash and is not an approximation of it. It is a **variance
preference that depends on the current win probability** — the standard
result that when your equilibrium value is below the win threshold, you should
prefer higher-variance lines even at lower expectation. Two consequences:

- The leaf objective should ultimately be **P(win)**, not expected
  `heuristic_eval` points. Maximising expected material is only equivalent to
  maximising P(win) when the value function is linear in the material, which
  is exactly what the "1 HP" article (8b.1) says it is not.
- The "behind" row is explicitly *exploitative*, not equilibrium: play for
  opponent error. This is the same mechanism as §3d's prior — a non-equilibrium
  opponent model — used deliberately, and it means the solver should be able
  to run in an exploitative mode as well as an equilibrium one. §3 currently
  presents equilibrium as the unqualified goal; it is the right *default*, not
  the right answer in every game state.

### 8c. Concepts with no counterpart in the current design

These are in the corpus and had no row at all. Several are cheap.

**1. Switching (whole article, unmapped).** The doc never had a switching row.
`candidate_actions` does generate voluntary switches (`solver.py:177`), and
`heuristic_eval`'s positional terms exist precisely so a pivot can look good at
depth 1 (see the comment at `solver.py:329-332`) — a good instinct that the
article confirms in detail. The four motives it gives are all formalisable:
survive an attack (already implicit), improve next turn's positioning
(positional score), **make use of a decaying field condition before it
expires** (see 8c.5), and save a key mon (§4b matching). The three risks
likewise: the opponent deviates, the switch-in takes too much damage, and — the
one with no current representation — "**switching doesn't do damage**", i.e. a
tempo cost that compounds: "if you spend too much time switching, you might
take too much damage to be able to make use of the positioning you get."

**2. Proactive vs reactive actions → an action-generation rule.** Traylor's
taxonomy is sharper than it first looks: *"Reactive actions are **always** in
response to the threat of a specific proactive action."* Reactive actions
(Protect, switch, heal) are only well-defined relative to a threat in `T`. That
is a principled pruner: generate reactive actions **only** where `T` shows a
live threat justifying them. This is a better rule than the current
`candidate_actions` pruning-to-best-target, and it composes with the free
pruning §4a already promises.

**3. Pressure is joint, but `T` is pairwise.** "**Can both enemy Pokémon work
together to secure a knockout?**" A per-pair `T[a][b]` cannot express focus
fire — two attackers that individually 2HKO but jointly OHKO. §4a as written
would miss the single most common threat pattern in doubles. `T` needs either
a joint entry `T2[(a1,a2)][b]` for the "do our two actives together KO `b`"
question (16–36 pairs becomes a still-tiny 4×4-ish extra table) or a
combined-damage query. This is a real correction to §4a's design, not just an
addition.

**4. "Making your opponent make difficult decisions" — the security-level
idea, from the Trick Room article.** Wolfe states it as a general principle:

> Pokémon is in many ways a game about **making your opponent make difficult
> decisions**.

This is the formalism the placeholder wrongly attached to "pressure": the
value of a position includes how *low* the opponent's security level is, and
how *flat* their options are. It is directly computable from the matrix we are
already building — `max_j min_i A[i][j]` from their side, plus something like
the entropy or spread of their best-response distribution. Worth surfacing as
an output ("their best play is worth X, their second-best X−ε → they are
guessing") because it is nearly free once §3 exists.

**5. Field-effect clocks, and declining a KO.** Wolfe's Indeedee example is a
direct counterexample to KO-positive material eval. Against a Trick Room lead
of setter + Follow Me support, taking the free KO on the support is *wrong*:

> But what happens if you DON'T knock out that Indeedee turn 1? … Compare this
> choice to what happens if you knock out Indeedee turn 1 — your opponent
> doesn't have to make a difficult choice and instead can exert pressure
> immediately. … If your opponent has led two defensive Pokémon, **leaving them
> on the field to waste turns is extremely valuable**.

Two things the model cannot currently express: (a) an enemy Pokémon can be a
*liability to its owner*, so `_ko_threat_value`'s floor of 0.35 ("removing ANY
Pokemon is still real progress") is wrong in this case and should be able to go
negative; (b) a decaying field effect is a **resource with a clock**, and value
accrues to whoever spends the opponent's clock. Trick Room is 5 turns
("effectively four because the setting turn counts") — so this is invisible at
depth 1 by construction. Along with 8c.6, it is the strongest argument in the
corpus that depth-1 evaluation is not merely coarse but structurally blind.

**6. Degrading the opponent's option set — "forcing" Protect.** The Protect
article's sharpest idea:

> it can actually be useful to **attack into something you think is
> Protecting**: if they don't Protect, you can just get a large amount of
> damage off/KO them, and if they do Protect, you can **pressure that slot a
> lot more the subsequent turn**.

An action's value includes *removing options from the opponent next turn*. In
this engine that is literal: a successful Protect sets `protected_last_turn`,
which `battle.py:429` uses to fail the next one. So the value is already
mechanically present in the state — it is simply unreachable at depth 1,
because the payoff arrives on turn *t+1*. This is a concrete, nameable play
that a depth-1 Nash solver still gets wrong, and it is the clearest case for
depth ≥ 2 in the document.

**7. Battle review → three computable UI metrics.** The *how-to-analyze*
article is about post-hoc review, and maps onto tooling rather than the model:

- **"MOST IMPORTANT TURN"** — "the turn that I generally define as one player
  gaining an insurmountable lead". Computable as the largest per-turn change in
  security level. Nearly free once §3 exists, and a better game-log feature
  than anything currently in Battle Viewer.
- **Match-up diagnosis.** The article's signatures of "it's a team problem, not
  a play problem" are both computable: "you constantly feel the need to make
  predictions and make risky plays" = no action has an acceptable worst case,
  i.e. maximin is bad across the whole row set; "you can't handle a specific
  Pokémon" = an uncovered node in the §4b matching. This turns a subjective
  judgement into two numbers.
- **Anti-results-orientation.** "just because a play didn't work out doesn't
  necessarily make it incorrect (and vice versa)". This is a direct
  endorsement of §3c: score policies by exploitability, not by outcomes. It is
  the citation for retiring "beats N/90 brings" as the headline number.

**8. Belief-state constraints from item exclusivity.** *analyzing-teams*: "Are
there **multiple Pokémon that normally like to run the same item**, and if so,
which one do I think is actually carrying said item?" That is a joint
constraint across the belief state of §5 — per-mon independent distributions
cannot represent it. Cheap to add as an exclusivity constraint on
one-per-team items; worth noting now so §5's belief state isn't built
factorised-only.

### 8d. What this changes upstream

- **§4a** — `T` needs a joint/focus-fire dimension (8c.3) and acquires a second
  role as the opponent-action prior and double-oracle seed (8b.2).
- **§4b** — confirmed as written; extend to run at preview against all **six**
  of their Pokémon, not just the brought four (*bo1*: "have answers against all
  6").
- **§4c** — keep, but demoted: its VGC-principles justification was a
  misreading. Engine-fidelity arguments only.
- **§4d — added** — non-linear HP / functional-value / sacrifice-value term
  (8b.1), and negative KO value for enemy liabilities (8c.5).
- **§5** — the belief prior should be **usage-weighted and pessimistic** by
  default, not uniform: *bo1* says "assume they have all their strongest
  attacks (generally, their top 6 most common attacks, as seen on Pikalytics)
  until they reveal otherwise". Add item-exclusivity constraints (8c.8).
- **§6** — confirmed, and extended: in Bo3 the preview game is **repeated with
  belief carry-over**, and *revealed information is a cost*. "Conserving
  information is also critical in a best-of-3 — you want to focus on winning
  while **not revealing unnecessary information**." A one-shot preview solve
  is the Bo1 model only.
- **§3** — equilibrium is the right default but not the whole story; an
  exploitative mode is needed for the "behind" game state (8b.4), and the leaf
  objective should trend toward P(win) rather than expected points.
- **§7** — add three near-free outputs: opponent-decision-difficulty (8c.4),
  most-important-turn (8c.7), match-up-vs-play diagnosis (8c.7).

---

## 9. Prior art

Retrieved via search only — these are leads at summary-level confidence, not
papers I have read. Verify before relying on specifics.

**Egress status rechecked 2026-08-08:** arxiv.org and smogon.com are now
reachable (ieeexplore still is not — returns 418). They have *not* been read
yet, so the confidence level below is unchanged; this is now a reading task
rather than a blocked one. The Metamon arXiv ID was spot-checked and is
correct: 2504.04395 is Grigsby, Xie, Sasek, Zheng & Zhu, *Human-Level
Competitive Pokémon via Scalable Offline Reinforcement Learning with
Transformers* — note it is **Singles**, not VGC doubles, which limits how
directly its results transfer.

Per-item verification status is marked below: **[verified]** means I read the
source, **[partly verified]** means some claims held and others could not be
checked, **[unverified]** means it is still a search-summary lead.

- **Ihara et al. (2018)**, *Implementation and Evaluation of Information Set
  Monte Carlo Tree Search for Pokémon* (IEEE) — compares Cheating MCTS,
  Determinized MCTS and ISMCTS on Pokémon; motivates ISMCTS via strategy
  fusion. **[unverified]** — ieeexplore is still egress-blocked (418). Since
  §5 only cites this to motivate deferring ISMCTS to Phase E, nothing currently
  rests on it.
- **FoulPlay** — **[verified by source read; one claim refuted].**
  `pmariglia/foul-play` is a Pokémon Showdown battle bot that "uses poke-engine
  to search through battles". `pmariglia/poke-engine` is confirmed **Rust with
  Python bindings**, implementing expectiminimax, iterative deepening and MCTS
  (`src/mcts.rs`, `src/mcts_threaded.rs` — the latter supports the
  root-parallelisation claim).

  **The Damage Roll Grouping attribution is false.** `src/genx/damage_calc.rs`
  defines `pub enum DamageRolls { Average, Min, Max }` — three flat multipliers
  (`×0.925`, `×0.85`, `×1.0`) — and `src/genx/generate_instructions.rs` calls
  `calculate_damage(..., DamageRolls::Max)`. There is no bucketing, no
  outcome-equivalence-class grouping, and no per-class probability weighting
  anywhere in the engine. The enum is even marked `#[allow(dead_code)]`. This
  is **precisely the min/max/avg scheme §4c proposes to replace** — so the
  previous draft cited, as prior art for the fix, an engine that has the bug.

  The PokéAgent Challenge win remains unverified (pokeagent.github.io is
  egress-blocked), but it no longer matters: it was only ever load-bearing as
  authority for the roll-grouping claim, and that claim is now refuted on its
  own terms. §4c has been rewritten to stand on the engine-fidelity argument,
  which is checkable against our own `damage.py` and needs no citation. **The
  phase ordering in §10 is unaffected** — if anything this reinforces C's
  demotion, since we now know of no engine that does it.
- **Metamon** — *Human-Level Competitive Pokémon via Scalable Offline RL with
  Transformers* (arXiv 2504.04395). **[verified]** title, authors and subject;
  paper body not read. **Singles, not doubles.**
- **Smogon forum thread 3785316** — **[verified, and substantially downgraded].**
  Read in full. It is not a body of discussion to learn from: it is a
  three-week-old thread (started 15 Jul 2026) by a single university student,
  "Jaiva", describing an **unreleased solo thesis project**, with exactly one
  reply. The technical description in the previous draft was accurate as a
  restatement of their self-report — "Deep multi-turn search (CFR+ / ISMCTS)
  over the public game tree, with a Bayesian belief model tracking what the
  opponent's team could be" — but that is a claim about their own private code,
  not a published result. Their own stated caveats are severe: "Win rates
  aren't calibrated yet", "Estimates are one-sided and conservative", and it
  "needs more training, more search depth, and a lot more real games before
  every verdict is worth trusting". It is also a **post-game replay analyzer**
  (Lichess-style review of Showdown replays), not a play-time solver, which is
  a different problem from ours. Open-sourcing is aspirational.

  Two things are still worth taking from it. **(a)** Independent convergence:
  someone coming from poker solvers landed on the same framing this document
  did, which is mild evidence the framing is natural rather than idiosyncratic.
  **(b)** The single reply, from `opencover`, raises the one substantive
  technical objection in the thread, and it lands on **§3**: "vanilla CFR+
  requires solving the entire game tree and *growing tree* CFR requires a value
  estimator which is tricky and expensive to train." That is correct and is
  precisely the trap §3 avoids — we do **not** run CFR over the game tree. We
  solve a *per-turn* matrix game whose cells are one `run_turn` scored by a
  hand-written leaf evaluation, so there is no full-tree traversal and no
  learned value estimator to train. Worth stating explicitly in §3, because it
  is the obvious objection a reader with poker-solver background will raise.
  The thread's real value to us is a contact, not a source.
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

*Revised scope after §8:* `T` gains a joint/focus-fire dimension (§8c.3),
becomes the opponent-action prior and double-oracle seed (§8b.2), and A now
also absorbs the work that §8b.1 pulled out of Phase C — non-linear HP,
functional/sacrifice value, and allowing `_ko_threat_value` to go negative for
enemy liabilities (§8c.5). Also fold in open question 5 (make the eval
antisymmetric).

**Phase B — matrix game at the turn level.** Implement payoff-matrix
construction + LP (or regret matching) + double oracle behind a flag; A/B it
against the current solver on known matchups. Retire `CHECK_TOP_K`. Deliver
mixed-strategy output and exploitability in Battle Viewer.

*Revised scope:* **B is depth-2 by requirement, not depth-capable-in-principle.**
The unit of evaluation is *this turn plus the state at the end of next turn* —
not one turn, and not "depth 1 with a flag we might raise later".

The motivating case is a two-turn punish where each turn looks acceptable in
isolation and the pair loses: **I attack, they switch; I attack again, they
Protect; I have gained nothing across two turns and am now the one out of
position.** Note this is unrepresentable today for two *independent* reasons,
and fixing only one leaves it broken:

1. The payoff arrives on turn *t+1*, so depth 1 cannot see it — no leaf
   evaluation at the end of turn *t* distinguishes this from a good attack.
2. `greedy_opponent_joint_action` hard-codes `protect: return -1`, so the
   second half of the sequence is not even in the opponent's action set.

§8c.5 (burning a field-effect clock) and §8c.6 (forcing a Protect) are the same
shape, arrived at from the articles rather than from play, which is decent
converging evidence that depth 2 is the real floor.

**The recursion has to be equilibrium-valued.** Each cell of the turn-*t*
matrix must hold the *solved value of the turn-*t+1* matrix game*, not the
result of a greedy or scripted playout of turn *t+1*. Using a greedy playout
one level down reintroduces exactly the self-delusion §2b measured (232 points
/ turn) at depth 2, having just removed it at depth 1 — the most likely way to
build this and get a result that looks fine and is not.

*Cost — measured, see §2c.* ~~Estimated ~10×.~~ **Actually 47.9×**: 6778
simulated turns and 3.88 s per decision, against 118 sims / 0.08 s at depth 1.
A 12-turn depth-2 game is ~47 s. Selective deepening over the top-4 depth-1
rows reaches 15× (1.22 s/decision) but **moves the answer by 58.8 points mean
and 208.8 max**, so `k = 4` is not a safe approximation.

This does not change the *requirement* — the two-turn punish is still invisible
at depth 1 — but it forces Phase B to be explicit about scope:

- **Depth 2 is for interactive, single-decision use** (Battle Viewer, punish
  analysis, "explain this turn"), where ~4 s is acceptable.
- **Sweeps stay at depth 1**, and accept that they cannot see two-turn punishes.
  Anything that needs depth inside a sweep has to come from the *evaluation*
  instead (§4d's field-clock term is the worked example), not from search.
- **Use brute force for the inner game.** §2c.1: at 8×8 the double-oracle
  best-response scan evaluates ~57 of 64 cells, so DO buys nothing and costs
  complexity. DO belongs on the large outer matrix only.

Also in scope: the opponent-decision-difficulty output (§8c.4) and
most-important-turn (§8c.7), both nearly free once the matrix exists.

**Phase C — roll bucketing.** Replace `min/max/avg` with outcome buckets;
surface roll-sensitivity.

**Phase D — plausibility-weighted preview.** ~~Nash over the bring matrix,
credible-bring support, double oracle for the sweep.~~ **Revised — see §6a/6b:**
a logit plausibility weighting over their 90 brings plus a tail (CVaR)
objective, replacing both today's uniform worst-case and the previously
proposed equilibrium solve. No LP, no double oracle. Ships incrementally, since
`τ → ∞` reproduces current behaviour exactly.

**Phase E — belief state / ISMCTS.** Only if A–D justify it.

### Ordering: revised to A → B → D → C → E

The previous draft proposed **A → D → B → C → E**, putting D second because
"the preview game is where both the accuracy error (strawman brings) and the
runtime cost (1620 games) currently live." Both halves of that are still true.
The ordering is nonetheless wrong, for a reason that only became visible after
reading the articles and re-reading §2b together.

**D consumes B's output.** `B[our bring][their bring]` is "the result of that
4v4" — and that result is produced by playing the game out with the turn-level
model. §2b measured what that model currently reports: it overstates our
position by **232 points ≈ 1.3 Pokémon per turn**. Solving the preview matrix
for Nash does not correct that bias; it *propagates* it, and then does
something worse than propagating it — it uses those numbers to prune. Today's
worst-case-over-90 is biased but **conservative**: a strawman bring drags a
score down, which is a safe direction to be wrong in. An equilibrium computed
over biased cells deletes brings from the credible support on the strength of
those cells, and reports a confident, small, wrong answer. Double oracle makes
this sharper still, since it never materialises the cells it prunes.

This would be tolerable if the bias were roughly uniform across cells, since
argmax and equilibrium support survive a constant offset. It is not: §2b found
the divergence concentrated exactly where the equilibrium is mixed (58% of
turns), which is to say in the sharp, close matchups that decide which brings
are credible in the first place. The cells D most needs to be right are the
cells B fixes.

**A now feeds B directly, which it did not in the previous draft.** §8b.2 makes
`T` the source of the opponent-action prior and the double-oracle seed. Double
oracle's cost is dominated by how good the initial restricted action set is,
and pressure edges are the principled way to pick it. Running A → B back to
back means B is built on top of the structure that makes it cheap, rather than
seeded arbitrarily and retrofitted.

**D is now the most dependent phase, not the least.** Post-§8 it needs A
(matching at preview against all six — §8d), B (unbiased cells), and it reaches
into E (Bo3 preview is repeated with belief carry-over, and revealed
information is a cost — §8d). It should be built last among {A, B, D}, not
second.

**C moves behind D and stays there.** Its justification changed rather than
weakened. The "1 HP is infinitely more than 0 HP" support was a misreading
(§8b.1) and that work has moved into A; what remains for C is engine fidelity
— Sash, Sturdy, Multiscale, bulk-EV thresholds — argued from FoulPlay's prior
art, with no support anywhere in the VGC corpus. There is one countervailing
pull worth recording: §8b.4's game-state-conditional variance preference
cannot be expressed at all while every leaf is a point estimate, and C is what
makes leaf values distributions. But variance preference wants **win
probability over full-game rollouts**, not a per-damage-event roll bucket —
different machinery, and reachable from A + B without C. So the coupling is
real but does not reorder anything.

**E stays last and stays conditional**, though it has picked up two concrete
requirements it did not have: a usage-weighted pessimistic prior rather than a
uniform one, and item-exclusivity constraints across the belief state (§8d).

Net: **A → B → D → C → E.** The change from the previous draft is D moving from
second to third. The runtime argument for doing D early is unchanged and
genuine — the 121 s King run is real — but it is an argument for doing D
*soon*, not for doing it *on top of numbers we have already measured to be
wrong by 1.3 Pokémon per turn*.

**D has also got smaller.** §6 was revised from "Nash equilibrium + double
oracle over the bring matrix" to a plausibility weighting plus a tail
objective, because equilibrium support is the wrong model of an opponent who
brings *somewhat* unexpected leads without bringing their worst. That removes
the LP, the fixed-point iteration, and the double oracle from D entirely — it
is now a weighting over the 90 we already enumerate. Two consequences for
ordering:

- D is **less** of a prize than the previous draft implied, which independently
  supports not front-loading it. The headline runtime win from double oracle
  was an artifact of a design we are no longer proposing.
- The `τ → ∞` endpoint of §6a *is* today's behaviour, so §6 can ship
  incrementally: introduce the weighting with `τ` large (a no-op), then lower
  it. There is no flag-day, and no need to wait for B to get *some* of the
  benefit — only the tail values will be biased until B lands, and biased
  values do less damage under a soft weighting than under hard support-pruning,
  which was the whole objection.

That last point is the practical answer if runtime pain bites before B: lower
`τ` and evaluate the tail slices at Tier 2 (§6c), rather than pruning brings
on the strength of biased cells.

---

## 11. Open questions for the next session

1. ~~Does the vgcguide material change the eval terms in §4 — particularly
   "pressure" and the best-of-1 vs best-of-3 risk posture?~~ **Resolved — see
   §8.** Yes, in both cases, but not as expected. "Pressure" turned out *not*
   to be an eval term at all — it is the threat matrix `T` plus an
   opponent-action prior (§8b.2). The bo1/bo3 posture *is* a real eval change,
   larger than anticipated: it argues the leaf objective should be P(win)
   rather than expected points, and that an exploitative (non-equilibrium) mode
   is needed when behind (§8b.4). The unanticipated eval change came from
   elsewhere — "1 HP is infinitely more than 0 HP" is about HP non-linearity
   and functional value, not damage rolls (§8b.1).
2. ~~Is `scipy` acceptable as a dependency?~~ **Resolved: not needed for the
   game solve.** scipy isn't installed in this venv, so the baseline harness
   uses ~25 lines of hand-rolled regret matching, which converges fine at this
   matrix size (23×28) in 3000 iterations, well inside the per-cell budget.
   Still open for `linear_sum_assignment` in §4b — but max-weight matching on a
   ≤6×6 graph is small enough to hand-roll too (Hungarian, or brute-force
   permutations at 6! = 720).
3. Should Tier 2 *replace* Tier 1, or stay parallel? ~~§2 says cost-neutral,
   which argues for replacement~~ — **the cost-neutrality argument is
   withdrawn, and the question is now resolved by §2c: they must stay
   parallel.** Cost-neutrality was false even at depth 1 — Tier 2 measures
   0.08 s/decision (~0.96 s/game) against ~0.05 s/game for the current greedy
   solver, about **20×** — and at the depth 2 Phase B requires it is ~47 s/game,
   roughly **1000×**. Depth therefore becomes a per-call-site choice
   (interactive: 2; sweeps: 1), not a global default, and Tier 1 stays.
4. ~~How much does equilibrium play differ from current output in practice?~~
   **Resolved — see §2b.** Substantially: 58% of turns get a different play,
   58% of turns need a mixed strategy the current design cannot represent, and
   the current solver overstates its position by ~1.3 Pokémon per turn.
5. *(New, raised by §2b; **measured in §2c.4 — now a prerequisite, not a
   nicety**)* `heuristic_eval` is not antisymmetric, so the matrix isn't
   strictly zero-sum. Measured at **277.7 points mean asymmetry — larger than
   the mean |eval| itself (233.0)** — and symmetrising **changes the maximin
   pick on 65% of turns**. Cause identified: the `seen` filter is applied only
   to the opponent (`solver.py:290-298`). Fix: score
   `(eval(s,"p1") − eval(s,"p2")) / 2`, antisymmetric by construction, ~+20%
   per cell. **Do this first in Phase A**, and re-measure §2b afterwards — its
   magnitudes are provisional until then.
6. *(New, raised by §8b.4)* **Is the objective expected points or P(win)?** The
   corpus says P(win), and says so in a way that has teeth: Zheng's rules for
   when to commit to a read are win-probability-threshold rules, and they are
   the only place in the corpus where deliberately *lowering* expected value is
   correct. But P(win) requires a calibrated map from `heuristic_eval` to a
   probability, which we do not have and which would need fitting against
   played-out games. Possible middle path: keep points as the leaf value, and
   apply a risk transform whose curvature is set by the current estimated
   position. ~~Needs a decision before Phase B fixes the solver's objective.~~
   **Direction decided (2026-08-08): P(win) is the preferred target, but Phase
   A keeps points, and this is revisited immediately after Phase A** — before
   Phase B fixes the solver's objective, which is the real deadline. Phase A's
   terms (§4b matching, §4d functional value) are all expressed in points and
   port to a calibrated scale unchanged, so nothing built in A is wasted either
   way. What A *should* do is leave the leaf value behind a single accessor
   rather than scattering `heuristic_eval` calls, so the swap is one edit.
7. ~~How much depth does Phase B need?~~ **Decided: depth 2 — this turn plus
   the state at the end of next turn** (§10, Phase B). Three independent
   motivating cases agree: the attack→switch, attack→Protect punish sequence;
   burning a field-effect clock (§8c.5); and forcing a Protect (§8c.6).
   ~~What remains open is the cost.~~ **Measured — §2c.2, and much worse than
   estimated: 47.9×, not ~10×** (3.88 s/decision, ~47 s for a 12-turn game).
   Selective deepening at `k = 4` reaches 15× but moves the answer by 58.8
   points mean / 208.8 max, so it is not safe at that width. **What is now open
   is not the cost but the response to it:** depth 2 is affordable for a single
   interactive decision (~4 s) and unaffordable inside any sweep, so Phase B
   must say explicitly which tiers get depth 2 rather than making it a global
   default. Surviving sub-question: what `k` *does* preserve the ranking — it
   is larger than 4. Trick Room's 4 effective turns would argue for depth > 2,
   now definitively unaffordable — the fallback is a field-clock term in the
   eval (§4d) standing in for depth we cannot buy.
8. *(New, raised by §8b.3)* The engine's no-double-Protect rule
   (`battle.py:421`) is stricter than the real 1/3 mechanic. Once the matrix
   game can price the gamble, do we relax it to the true probability? The
   current rule exists to stop the solver farming free turns; the matrix should
   remove that failure mode by making Protect's cost visible, at which point
   the strictness is just an inaccuracy.
9. *(New, raised by §8d)* Bo3 as a repeated game with information cost is a
   genuinely new axis — every current tab implicitly models Bo1. Is Bo3 in
   scope at all, or do we state Bo1 as an explicit modelling assumption? This
   should be decided before Phase D, since it changes whether the preview solve
   is one-shot.
