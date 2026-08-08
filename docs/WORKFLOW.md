# Workflow: how to actually use this

Written to answer four questions: what did the redesign build, which files
matter, what the honest gaps are, and what to run.

---

## 1. What the new engine actually achieved

One thing, and everything else follows from it: **a way to measure how
punishable a play is, instead of how often it beats our own bot.**

```
exploitability(play) = equilibrium value of the turn
                     − worst case of the play actually chosen
```

Zero is unpunishable. It is measured in points (a KO ≈ 180), and it is *not* a
win rate. That distinction is the whole point. Win rate against a fixed
opponent is biased by construction: the greedy solver best-responds to that
exact opponent, which is why it scores 65% while the equilibrium solver scores
31% in the same harness — and why the equilibrium solver nevertheless beats it
**60% [55–65%], n=384** head-to-head.

Measured results from the redesign:

| Change | Effect |
|---|---|
| Equilibrium solver vs greedy, head-to-head | **60%** [55–65%], n=384 |
| Exploitability: greedy → Nash+mixing+wide movesets | **65.1 → 43.0** |
| Line audit, mean exploitability per turn | **95.9 → 8.5** |
| Worst single turn in a greedy line | 395 points (over two Pokémon) |
| Cost | 0.08s/decision at depth 1 (~16× greedy) |

Bugs this surfaced and fixed, each of which was silently corrupting results:
an evaluation asymmetry of 277 points; a +50 reveal incentive created by the
design doc's own recommended symmetrisation; a +63 reveal incentive from a
KO-threat placeholder; four separate `__deepcopy__` omissions that silently
zeroed features in every simulated state; and a **biased head-to-head harness
whose null control returned 44% instead of 50%**, which had invalidated every
head-to-head verdict made before it was caught.

VGC principles that became code rather than prose: the threat matrix
("pressure" as directional edges, `threat.py`), plausibility-weighted opponent
brings so an opening no good player makes cannot drag a rating down
(`preview.py`), damage rolls as distributions with exact k/16 KO probabilities
(`rolls.py`), and answer-preservation via max-weight matching so trading your
only answer to their win condition is scored as the loss it is
(`matching.py`).

---

## 2. Which files matter

**The engine (9 files).** `matrix_game.py` (equilibrium solving, no Pokémon
knowledge) · `turn_game.py` (one turn as a matrix game) · `turn_step.py` ·
`robustness.py` (**exploitability lives here**) · `threat.py` · `matching.py` ·
`rolls.py` · `preview.py` · `team_rating.py`.

**The tools you run (3).** `tools/generate_overnight.py` · `tools/search_teams.py`
· `src/app.py`.

**Regression guard (1).** `tools/golden_baseline.py` — run after any engine
change; it pins 6 matchups and 33 turns.

**Everything else in `tools/` is measurement scaffolding**, not workflow. The
eight `measure_*.py` scripts are the experiments that produced the numbers in
section 1. They are kept because a claim without its measurement is an opinion,
but you never need to run them. `src/prescreen.py` is a **documented negative
result** — 4–15% recall, off everywhere, kept so nobody rebuilds it.

**The Streamlit app** did gain the engine: a Nash checkbox with depth
(off by default — it is ~16× slower), an effort slider on Vs Team, a "How
punishable is this line?" audit, and a turn-1 equilibrium panel. You were right
that it is thin relative to the backend. The app is for inspecting one matchup;
the batch tools are where the search happens.

---

## 3. The gap you identified, stated plainly

> *"the reward in generation may be completely unrelated"*

**Correct, and it is the most important limitation of the current system.**

`team_search.py` — which drives stages 1–4 of generation (pool filter, pair
matrix, beam search, switch-rescue) — contains **zero** references to
exploitability, the threat matrix, or the equilibrium solver. It ranks teams on
type coverage and synergy heuristics scored by a greedy screener.

So generation optimises coverage-and-synergy, and we then judge the winner by
exploitability. Those are different quantities, and nothing guarantees the beam
search's favourite is anywhere near the least punishable team available.

`generate_overnight.py` does not fix this. What it does is **widen the funnel**:
rate 40 beam finalists instead of 3, and let exploitability — not the beam —
choose the shortlist. That is the honest version available today. The residual
bias is that every rated team still came from a beam search with the wrong
objective.

Fixing it properly means putting exploitability (or a cheap proxy that
correlates with it) inside the beam's scoring function. That has not been
built, and it should be **measured before being trusted** — the prescreen
failure is exactly what happens when a plausible-sounding proxy is adopted
without checking its recall.

Two other things you asked about that **do not exist**:

- **No iterative moveset refinement loop.** `optimize_sets.py` picks moves and
  items by 1v1 coverage against the threat list, as a one-shot before the
  search. Nothing feeds exploitability back into set selection.
- **No substitution loop.** Nothing takes a rated team, swaps its worst member,
  and re-rates. This is the single most valuable thing left to build, because
  it attacks the section-3 gap directly and cheaply: the shortlist already
  names each team's worst matchup and the turn that gets punished.

---

## 4. The workflow

### Overnight, unattended

```bat
overnight.bat
```

Stage 1 generates and rates 40 teams by exploitability, writes
`tools/shortlist.json`. Stage 2 takes the top 8 and re-tests them deeply
against every library team, writing `tools/overnight.xlsx`. Both stages are
cached and resumable — if it dies, run the same command again.

`overnight.bat 60 10` for 60 candidates, top 10 into the deep search.

### Or the stages by hand

```bat
generate.bat --candidates 40 --effort standard --jobs 0
search.bat --rosters shortlist.json --teams "gen01,gen02,gen03" ^
           --effort thorough --jobs 0 --export
```

### Reading the output

Open `tools/overnight.xlsx`.

1. **Teams sheet** — ranked by mean exploitability, lowest first. **Always read
   it next to the win column.** A lost position has nothing left to punish, so
   it also rates near zero; a team can score 8 while winning 0/6. The red
   "Read with care" column flags anything winning under half its games.
2. **Turns sheet** — sort descending by Exploitability. Those rows are your
   list of specific plays a good player would punish, with the exact answer
   they would use. This is the actionable output.
3. **Candidates sheet** — the runner-up bring next to the winner.

Exploitability within about ±1 of zero is noise (regret-matching convergence).

### Answering "can this team beat a good player" without a full run

You cannot, exactly — that is what the measurement is for. But the ordering is
cheap → dear, so use it: `--effort quick` (seconds, win counts only) →
`standard` (~5× quick) → `thorough` (~25×) → `exhaustive`. Screen wide at
`standard`, spend `thorough` only on survivors.

---

## 5. Speed

Everything was single-core. Both batch tools now take `--jobs N` (`0` = one per
core), parallel across whole pairings or opponents — coarse units, no shared
state, and **verified bit-identical to serial output**.

Measured: 4 pairings on 4 workers, **68s wall vs 209s CPU — 3.1×**.

- `--jobs 0` uses every core. Budget ~1GB RAM per worker; if you have 16 cores
  and 16GB, use `--jobs 8`.
- The dataset load is ~14s per worker, paid once, so parallelism only pays on
  long runs. Don't use it for a single pairing.
- The pair matrix is pickled and reused across generation runs.
- `--batch` controls the save interval, not speed. Keep it ≥ `--jobs`.

Biggest remaining speedup is not more cores: it is **pruning enemy
configurations**. Verification plays every survivor against all 90 of their
configurations while §6a establishes most are implausible and provides the
weighting to say which. That is potentially several-fold, and unlike the
prescreen it has evidence behind it — but it must be measured before being
switched on.
