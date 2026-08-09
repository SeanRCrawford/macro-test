# Where things stand

The single source of truth for what to run and what is current. If anything
below disagrees with a docstring, this file is right and the docstring is stale.

---

## 1. The question the system answers

**Commit to one bring-4 and one lead-2 against an opponent, before seeing what
they bring. How many of their 90 possible bring-4s does that beat, and how much
does a good player gain along the way?**

That is *total pathing*. A perfect answer to an opponent is **90 / 90**. It is
one committed choice, because that is all you get at preview.

Two numbers, and they are not the same thing:

| | |
|---|---|
| **Record (X / 90)** | commit to this plan, win X of their configurations |
| **Punish** | points a good player gains per turn along the way (a KO ≈ 180) |

Punish alone is a trap: a **lost** position has nothing left to punish, so it
scores near zero. Always read the record first.

---

## 2. What to run

```bat
overnight.bat --pool-size 50 --candidates 60 --keep 6 --optimise-sets --jobs 8
```

Generates teams, optimises their sets, screens out the ones that lose, audits
the survivors against **all 90** of each opponent's bring-4s, and writes the
workbook. Cached and resumable — if it dies, re-run the identical command.

Options worth knowing:

| Flag | Default | What it does |
|---|---|---|
| `--pool-size N` | 34 | how many Pokémon are ELIGIBLE — the search space, and the dial for finding a hidden gem |
| `--candidates N` | 40 | how many generated teams get rated |
| `--keep N` | 6 | how many reach the deep search |
| `--optimise-sets` | off | optimise item + 4 moves against the real metagame. **Use it** |
| `--min-winrate F` | 0.80 | skip auditing a team that cannot win |
| `--generations SPEC` | all | e.g. `1-5` |
| `--jobs N` | all cores | ~1 GB RAM per worker; use 8 on a 16 GB machine |
| `--sample-leads` | off | audit a sample of their leads instead of all 90. Faster, but the record becomes `X / 4` and stops being a total-pathing number |
| `--pick "4,10,12"` | off | deep-search only these stage 1 rank numbers; results accumulate |
| `--list` | off | stage 1 only — rate and rank, then stop |
| `--deep-effort TIER` | thorough | `standard` / `thorough` / `exhaustive` — how many of OUR brings get audited |

### Picking what is worth the night

Stage 2 is the expensive half, so you do not have to spend it on everything.

```bat
overnight.bat --list                     :: generate and rate, then STOP
overnight.bat --pick "6"                 :: deep-search #6 only
overnight.bat --pick "4,10,12"           :: later -- #6 is NOT redone
```

`--list` runs stage 1 and prints the ranked teams, so you can look before
committing. `--pick` takes stage 1's **rank numbers**.

Two things make coming back later work:

* stage 1 writes **every** rated team to `shortlist.json` in rank order, so
  `gen06` means the same team tomorrow as today — the numbering never shifts;
* stage 2 shares one cache, so a later `--pick` **adds** to it and the workbook
  is rebuilt to include everything searched so far.

Omit `--pick` and stage 2 searches the top `--keep`, as before.

### What `--audit-all` actually does, and how long it takes

Stage 2 prints this before it starts:

```
audit    : 6 of our brings x 90 of their bring-4s (ALL of them: leads AND backs)
           = 540 lines per pairing
estimate : ~4.5 h for 42 pairings on 8 worker(s)
```

A **line** is one full game: our committed bring against one of their bring-4s,
played to a finish, with a complete payoff matrix solved on every turn to
measure the punish. Their 90 comes from choosing 4 of their 6 and a lead pair:
15 brings x 6 lead orders.

Without `--audit-all` only their most plausible *leads* are sampled (4 at
thorough), their backs are assumed, and a plan that beats their lead but loses
to their back is scored as a win. That is why it is on by default: **90 is the
denominator that makes `Wins / Of` a total-pathing number.** The cost is
exactly the ratio, 90/4 -- about 22x the audit stage.

The up-front estimate uses a measured ~0.9 s per audited turn and an assumed
8-turn line. It is rough on purpose; once the first pairing finishes, each
progress line reports the real rate and a clock time:

```
[3/42] gen01 vs Rain   7 min/pairing   elapsed 21 min   left ~4.6 h (done ~03:14)
```

Kill it whenever you like -- every batch is saved, and re-running the identical
command resumes.

### Reading the output — `tools\overnight_thorough.xlsx`

1. **Plan** — the answer. One committed bring/lead per opponent, `Wins / Of`,
   and **`Losing to`** naming the exact brings that beat it. Sorted worst
   first. This is the sheet to act on.
2. **Best lines** — that same committed plan, line by line, with the damage
   log, KOs and HP after every turn.
3. **Team sheets** — what each `genNN` actually is: members, item, ability,
   EVs, four moves, and whether the sets are optimised.
4. **Lines / Turns / Candidates / Teams** — diagnosis underneath.

### The app

`run.bat` → **Vs Team** tab:

- **Load an overnight run** — reads the files above, so generated teams become
  selectable and you can browse any committed line turn by turn. Instant; it
  reads the cache rather than recomputing.
- **Deep dive** — you have led, they have led. Runs the exhaustive analysis on
  that one position in ~5 s, and offers depth 2 (which the batch run cannot
  afford). This is the "having already led X" tool.

---

## 3. What is current, and what is not

**The engine (all new, all live):** `matrix_game` · `turn_game` · `turn_step` ·
`robustness` (exploitability lives here) · `team_rating` · `threat` ·
`matching` · `rolls` · `preview` · `deep_dive` · `search_effort` ·
`export_search` · `team_sheet_export` · `blas_limits`

**Tools you run:** `overnight.bat` (whole pipeline) · `generate.bat` (stage 1
alone) · `search.bat` (stage 2, or library teams) · `run.bat` (app)

**Regression guard:** `tools/golden_baseline.py` — run after any engine change.

**Evidence, never run:** the eight `tools/measure_*.py` scripts. They produced
the numbers this design rests on and are kept so the claims are checkable.

**Legacy but still used:** `team_search.py` (the beam search — see §4),
`fast_eval.py` (the cheap screen), `optimize_sets.py` (now wired in),
`run_search.py` and `generate_team.py` (older entry points, still work).

**Dead:** `prescreen.py` — measured 4–15 % recall, off everywhere, kept only as
a documented negative result.

---

## 4. Known gaps — read before trusting a number

1. **Generation does not optimise for punishability.** `team_search.py` has
   zero knowledge of exploitability; the beam ranks on coverage and synergy.
   `generate_overnight` widens the funnel (rate 40 finalists, not 3) but does
   not steer the search. The teams you get are *high-coverage teams that were
   then measured*, not *teams found by looking for low punish*.
2. **Depth-1 horizon.** The solver sees one turn ahead, so a turn that gains
   nothing looks free. This caused the Protect spam you saw; the pointless
   double-Protect case is now filtered, but the underlying blind spot is real —
   setting Tailwind still scores as a wasted turn. Depth 2 fixes it and costs
   ~48×, which only the app's Deep dive can afford.
3. **Two ranking numbers can disagree.** The **Teams** sheet averages across
   all audited candidates; the **Plan** sheet reports the one committed choice.
   Trust Plan.
4. **Wins and punish come from different pilots.** The win count uses the
   greedy solver; the audit uses the equilibrium solver. Not yet reconciled.
5. **No substitution loop.** Nothing takes a rated team, swaps its worst
   member, and re-rates. Given that Plan already names what beats each team,
   this is the highest-value thing left to build.

---

## 5. Speed

`--jobs N` is parallel across whole pairings, verified bit-identical to serial.
Measured **3.1× on 4 cores**. Memory, not CPU, is the limit.

Auditing all 90 brings multiplies the audit by 90/leads — that is the cost of a
real total-pathing number, and it is why this is an overnight job. Drop to
`--sample-leads` only to get a fast read, knowing the record is then a sample.

The biggest remaining speedup is pruning *enemy* configurations by
plausibility, which §6a of the design doc has evidence for and which has not
been measured yet.
