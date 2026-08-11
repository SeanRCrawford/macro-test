# Where things stand

The single source of truth for what to run and what is current. If anything
below disagrees with a docstring, this file is right and the docstring is stale.

**The short version:**

```bat
overnight.bat --list                  :: generate + rate teams, show the ranking
overnight.bat --pick "6"              :: deep-search #6 (results accumulate)
run.bat                               :: the app -- preview, deep dive, results
```

Read `tools\overnight_thorough.xlsx` → **Plan** sheet. It gives one committed
bring and lead per opponent, and how many of their 90 brings it beats.

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
| `--vs "Big 6"` | all | deep-search only these opponents — the biggest single lever |
| `--brings N` | tier default | audit only the N best of our brings per pairing |
| `--pick "4,10,12"` | off | deep-search only these stage 1 rank numbers; results accumulate. On its own it does NOT regenerate |
| `--list` | off | stage 1 only — rate and rank, then stop |
| `--stage2-only` | off | skip generation, deep-search the shortlist on disk |
| `--regenerate` | off | force stage 1 even when `--pick` would have skipped it |
| `--deep-effort TIER` | thorough | `standard` / `thorough` / `exhaustive` — how many of OUR brings get audited |
| `--substitute N` | off | after stage 1, try to improve the top N teams by swapping their worst member. The only stage that steers the search by the rating |

### Swapping a team's worst member — `--substitute`

```bat
overnight.bat --substitute 3                :: hill-climb the top 3, then deep-search them
tools\substitute.py --rosters shortlist.json --team 6 --rounds 3
```

Generation used to stop the moment a team was rated. This takes a rated team,
works out which member is not earning its slot, tries better ones, and keeps a
swap **only if the rating improves**:

* **what to drop** — first, whether the member appears in the bring the audit
  actually committed to against any opponent (a member on the bench in every
  matchup is doing nothing); then how much the team's screened coverage
  degrades without it. A member no pair can answer a threat without is a
  keystone and is dropped last.
* **what to bring in** — candidates are scored against the opponents the team
  is *failing*, weighted by `1 - adjusted win rate`, not against the whole
  field evenly.
* **whether to keep it** — the candidate is rated by the same audit, at the
  same effort, through the same cache as stage 1. The screener only proposes.

**The record comes first.** A swap that beats fewer of their 90 is rejected
however good its adjusted win rate looks — the first real run of this loop
raised adjusted wins 0.426 → 0.467 while the record fell 75/90 → 56/90, which
is not an improvement. `--max-record-loss` relaxes that if you mean to trade
record for line quality.

It is a local search: one member at a time, improvements only, so it finds the
best team *near* the one it started from. When a round finds nothing it says
so and stops rather than churning. The usual reason nothing changes is printed
too — if the audit never brings the new member, the swap changed no audited
line at all.

### Picking what is worth the night

Stage 2 is the expensive half, so you do not have to spend it on everything.

```bat
overnight.bat --pool-size 50 --candidates 60 --optimise-sets --list
                                         :: generate and rate, then STOP
overnight.bat --pick "6"                 :: deep-search #6 only, no regenerating
overnight.bat --pick "4,10,12"           :: later -- #6 is NOT redone
overnight.bat --stage2-only --keep 6     :: deep-search the top 6, same idea
```

`--list` runs stage 1 and prints the ranked teams, so you can look before
committing. `--pick` takes stage 1's **rank numbers**.

**A `--pick` command does not regenerate.** If nothing on the command line asks
for generation, `--pick` deep-searches the `shortlist.json` already on disk —
which is the only reading that makes sense, since the number you are picking
came from that file. `--stage2-only` says the same thing explicitly (and works
without `--pick`), and `--regenerate` forces stage 1 back on.

That matters more than it sounds, because **the numbering is only stable across
runs that generate the same teams**. Stage 1's flags decide which teams exist:
drop them and the defaults (34 / 40) take over. Measured on the real dataset,
`--pool-size 50 --candidates 60` and the defaults share **none** of their top
five teams — so a `--pick "5"` that quietly re-ran stage 1 was searching a team
you had never seen, after hours of re-rating nothing in the cache. Stage 1 now
records its settings in `shortlist.json` and warns loudly before renumbering
one that does not match.

Two things make coming back later work:

* stage 1 writes **every** rated team to `shortlist.json` in rank order, so
  `gen06` means the same team tomorrow as today — *provided the same stage 1
  flags*, which is what the warning above protects;
* stage 2 shares one cache, so a later `--pick` **adds** to it and the workbook
  is rebuilt to include everything searched so far.

Omit `--pick` and stage 2 searches the top `--keep`, as before.

### If the estimate is hours you do not have

Stage 2 prints the three levers when the wait exceeds 4 hours. They multiply:

| Lever | Effect | Cost |
|---|---|---|
| `--vs "Big 6"` | one opponent instead of 8 → **8x less** | you only learn about that opponent |
| `--brings 2` | 2 of our brings instead of 6 → **3x less** | the runner-up bring is not audited, so it cannot appear in the workbook |
| `--sample-leads` | their likely leads instead of all 90 → **~22x less** | the record stops being `X / 90` |

Measured, on the full library at thorough: `7.6 h` → `--vs "Big 6" --brings 2`
→ **`0.3 h`**. The first two keep the total-pathing property intact; only
`--sample-leads` gives it up.

The honest order to reach for them: narrow the opponents first (you usually
care about one matchup), then the brings, and only give up the 90 if you must.

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

The up-front estimate uses a measured ~0.41 s per audited turn and an assumed
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

`run.bat` → **Vs Team** tab. Three panels, matching the three moments of a game:

| Panel | When | What it does |
|---|---|---|
| **Team preview** | you see their six, not their four | runs the advanced model on the bring/lead decision and returns ONE committed plan, its record, and what beats it |
| **Deep dive** | you have led, and so have they | the same model one notch deeper on that position, ~5 s, and depth 2 is available here because one line can afford it |
| **Load an overnight run** | after a batch run | browse any committed line turn by turn, from the cache. Instant |

**Picking the opponent, in all three.** Every panel takes the enemy team the
same way: a **preset** from `teams.csv` (Big 6, Rain, …), six hand-picked
Pokémon, or a pokepaste. A preset brings its recorded sets with it, and fills
in its fixed lead and its scripted opening where it has them — hand-typing the
same six by name silently answers a slightly different question, because the
script and the sets do not come along.

**Deep dive assumes you cannot see their back.** You give their six and the two
they led with; the back is what you do *not* know at that moment, so by default
it audits **every back pair they could still be holding** behind that lead and
reports how the answer varies. One winning line is not a plan if the other five
lose. Pick a specific back only if you have actually scouted it.

**Every match, in every panel, has its log.** Team preview lists the games
behind its record, deep dive lists one per back, and the bottom section lists
one per enemy bring-4 — each with the full battle log and the turn-by-turn
punish analysis side by side.

**Lead / Back → Battle Viewer.** The two tabs are connected, in both the ways
that matter:

* **Salvage all N losses together.** Expand an opponent you lose to and the
  losing brings are solved *jointly*: every single change (EV spread, resist
  berry, one support or setup move) is replayed against every one of them and
  ranked by how many it fixes. One fix per loss is one team per loss, and the
  fix for one matchup routinely breaks another — so changes that break a
  matchup you already win are counted and shown. "Only show changes that fix
  ALL of them" is much faster, since a candidate is abandoned on its first
  miss.
* **Open in Battle Viewer.** Any individual battle can be sent to the Battle
  Viewer, where the punish check, path explorer and per-matchup salvage then
  act on that exact matchup instead of one rebuilt by hand from dropdowns.

**Team Builder — what you can set by hand.** Items, stat points and moves are
all overrides that travel together and are read by *every* simulation the app
runs, not just the tab you set them in. Items can be picked one at a time
(the optimiser decides all six at once, which is a different job); a
Mega-capable pick is locked to its stone, since a Mega is a species choice
here, and the "Any item…" option opens the full catalogue for something the
usage data has never seen.

**Where the advanced model is:** the *Advanced model strength* slider —
Quick → Standard → Thorough → Exhaustive — with a tooltip explaining each, plus
a checkbox for "test against ALL 90 of their brings". The same control appears
in every panel that runs the engine live, so there is one answer to "which
setting am I using".

Timing, measured: one preview pairing at **Standard is ~2 minutes**. Thorough
and Exhaustive are much longer — for those, use the overnight pipeline and read
the result here rather than waiting in the browser.

---

## 3. What is current, and what is not

**The engine (all new, all live):** `matrix_game` · `turn_game` · `turn_step` ·
`robustness` (exploitability lives here) · `team_rating` · `roster_rating`
(rates a whole six, shared by stage 1 and the substitution loop) ·
`substitution` · `threat` · `matching` · `rolls` · `preview` · `deep_dive` ·
`search_effort` · `export_search` · `team_sheet_export` · `blas_limits`

**Tools you run:** `overnight.bat` (whole pipeline) · `generate.bat` (stage 1
alone) · `search.bat` (stage 2, or library teams) · `run.bat` (app) ·
`tools/substitute.py` (swap a rated team's worst member — also reachable as
`overnight.bat --substitute N`)

**Regression guard:** `tools/golden_baseline.py` — run after any engine change.

**Evidence, never run:** the `tools/measure_*.py` scripts. They produced the
numbers this design rests on and are kept so the claims are checkable.
`measure_pilot_gap.py` is the newest: it replays the same configurations under
both pilots and reports how often the winner changes. `measure_robustness.py`
takes `--set NAME=VALUE` so any solver tunable can be swept through it.

**Legacy but still used:** `team_search.py` (the beam search — see §4),
`fast_eval.py` (the cheap screen), `optimize_sets.py` (now wired in),
`run_search.py` and `generate_team.py` (older entry points, still work).

**Dead:** `prescreen.py` — measured 4–15 % recall, off everywhere, kept only as
a documented negative result.

---

## 4. Known gaps — read before trusting a number

1. **The beam still ranks on coverage, but the search no longer ends there.**
   `team_search.py` has zero knowledge of exploitability; the beam ranks on
   coverage and synergy, and `generate_overnight` only widens the funnel (rate
   40 finalists, not 3). What is new is `--substitute` (§2): it takes the rated
   teams and hill-climbs them on the **rating itself**, accepting a swap only
   when the audit improves. So the shortlist is no longer purely
   *high-coverage teams that were then measured* — its top entries have been
   refined against the real objective. The generator's own objective is still
   coverage, and a local search around a coverage-picked team cannot reach a
   great team that the beam never proposed.
2. **Depth-1 horizon — now addressed, at a measured cost.** The solver sees one
   turn ahead, so a turn that gains nothing looks free. This caused the Protect
   spam, and it made setting Tailwind score as a wasted turn.

   Two terms are now **on**:

   * `solver.SPEED_CONTROL_WEIGHT` scores who moves first under the current
     field. Switched on, 11 of the golden baseline's 33 pinned turns change,
     and several are `Farigiraf: Protect` becoming `Farigiraf: Trick Room` —
     the wasted turn becoming a real play.
   * `solver.FRAGILE_HP` stops paying a full threat credit for a Pokémon that
     is one hit from gone and outsped. On the reported position, the cost of
     sacrificing it drops from 192 points to 37, against 50 for feeding a
     healthy team-mate half its HP — so the solver now sacrifices instead of
     switching out and losing something better.

   **What it costs, measured over 9 pairings (3 teams × Rain / Sand / King,
   standard tier):** mean adjusted wins `0.518 → 0.444`, record `80% → 74%`.
   That is a real cost, and it is on anyway: the behaviour was reported twice
   from real games as a direct cause of losses, and a nine-pairing sweep whose
   per-pairing numbers swing by 30 points from a 25% change in one constant
   cannot resolve an effect this size. Watch the record on your own runs;
   setting both back to `0.0` restores the previous evaluation exactly (and
   means bumping the two cache schemas — see the comments in `src/solver.py`).

   A third term, `UNSPENT_MEGA_PREMIUM`, was written for the other half of that
   report — the Pokémon that came in to cover the spent one was a Mega that had
   not transformed, so it took the hits in its frailer base form. It is **off**:
   measured, it is inert for the Megas people actually bring (a level-50 Mega
   Metagross is already at the value clamp) and it does not move the reported
   decision, because being chipped is not a KO and depth 1 cannot see the faint
   it leads to. Pricing that properly needs the Mega's post-transform stats,
   and probably depth 2.
3. **Two ranking numbers can disagree.** The **Teams** sheet averages across
   all audited candidates; the **Plan** sheet reports the one committed choice.
   Trust Plan.
4. **Wins and punish still come from different pilots — now measured, and
   selectable.** The win count is played by the greedy solver against
   `greedy_opponent_joint_action`; the audit is played by the equilibrium
   solver against their equilibrium reply, with their *wider* six-move set. So
   "beats 75 of their 90, concedes 56 points a turn" is two sentences about two
   different pairs of players.

   `tools/measure_pilot_gap.py` says how much that matters: replaying the same
   40 configurations under both pilots (Rain and Big 6, turn cap 18), the
   greedy pilot wins 23 and the equilibrium pilot 3 — and **the winner changes
   on 50% of them**. The flip rate, not the difference in counts, is the
   number: two pilots winning the same total is not agreement if they win
   different games.

   Any caller can now ask for either (`pilot="equilibrium"` through
   `verify_with_solver` / `rate_team`, `--record-pilot` on `substitute.py`),
   and a record carries the name of the pilot that played it. The default is
   unchanged — greedy, as every workbook to date — because the equilibrium
   pilot costs a payoff matrix per turn. Reconciled in the sense that the
   disagreement is measured and switchable, not in the sense that one number
   now covers both.
5. ~~**No substitution loop.**~~ **Built** — `overnight.bat --substitute N`,
   or `tools/substitute.py`. See §2 for what it drops, what it brings in, and
   why a swap that gives up record is rejected however good its adjusted win
   rate looks.

---

## 5. Speed

`--jobs N` is parallel across whole pairings, verified bit-identical to serial.
Measured **3.1× on 4 cores**. Memory, not CPU, is the limit — roughly 1 GB per
worker, since each holds its own copy of the dataset.

### If it is slower than the core count suggests

**Stage 1 used to cap itself at eight workers**, whatever you passed. It rated
teams one at a time and split each rating across the eight opponents, so `--jobs
16` left half the machine idle and `--jobs 32` left three quarters. Worse, the
opponents are wildly unequal — a scripted team like King measured 3–6× a plain
one — so the makespan was the slowest opponent even below eight: measured, 8
opponents on 4 cores took 81 s against 126 s serial, a 1.56× return on 4×.

Stage 1 now hands each worker a **whole team**, which is a unit there are dozens
of. Measured on the same work, 4 teams × 8 opponents on 4 cores: **485 s → 248 s,
1.95×**, and it keeps scaling — the ceiling is now the number of teams left to
rate, which is `--candidates`. It says so at startup, and tells you when
`--jobs` exceeds what it has to hand out.

Stage 2 parallelises across pairings (teams × opponents), which was already
wide, but it rebuilt its worker pool once per `--batch` and waited for every
pairing in a batch before starting the next. With unequal pairings that idled
cores at every batch boundary and re-loaded the dataset in each worker. It is
now one pool for the whole run; `--batch` only controls how often results are
written to disk.

So: give it `--jobs <cores>`, and raise `--candidates` if you want stage 1 to
use more of them.

The equilibrium solver's inner loop is vectorised. Profiling put **69 % of an
audited line inside `solve_matrix`** — two matrix-vector products written as
nested Python sums, 15 million generator calls for a single 30×60 turn. With
numpy, plus cutting the scalar path's iteration count (a 4x4 subgame is
converged long before 3000 sweeps): **one audited line 15.8 s → 2.1 s, 7.5×**,
golden baseline unchanged through both changes. Small matrices keep the scalar
path; the 64-cell crossover is measured, not guessed. Gains are largest on `--audit-all` runs, where the audit dominates —
a preview pairing, which is mostly screening, went 126 s → 94 s.

Auditing all 90 brings multiplies the audit by 90/leads — that is the cost of a
real total-pathing number, and it is why this is an overnight job. Drop to
`--sample-leads` only to get a fast read, knowing the record is then a sample.

The biggest remaining speedup is pruning *enemy* configurations by
plausibility, which §6a of the design doc has evidence for and which has not
been measured yet.
