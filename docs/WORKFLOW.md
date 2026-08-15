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

> **`X / 90` IS NOT A WIN RATE.** It breaks three things at once, and
> `src/win_rate.py` is the sound version. Read §4.0b before quoting any record.


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
| `--punish-screen [F]` | off (floor −250) | throw out a team whose OPENING is already lost, before the audit. Seconds per team. **Prints the number for every team it looks at, not only the ones it rejects** — see below |
| `--worst-matchup F` | off | reject a team whose WORST single matchup wins less than F, e.g. `0.89` for 80/90. Abandons on the first failing opponent |
| `--generations SPEC` | all | e.g. `1-5` |
| `--jobs N` | all cores | ~1 GB RAM per worker; use 8 on a 16 GB machine |
| `--sample-leads` | off | audit a sample of their leads instead of all 90. Faster, but the record becomes `X / 4` and stops being a total-pathing number |
| `--vs "Big 6"` | all | deep-search only these opponents — the biggest single lever |
| `--brings N` | tier default | audit only the N best of our brings per pairing |
| `--pick "4,10,12"` | off | deep-search only these stage 1 rank numbers, **ranges allowed** (`"1-40"`, `"1-10,25"`); results accumulate. On its own it does NOT regenerate |
| `--list` | off | stage 1 only — rate and rank, then stop |
| `--stage2-only` | off | skip generation, deep-search the shortlist on disk |
| `--regenerate` | off | force stage 1 even when `--pick` would have skipped it |
| `--deep-effort TIER` | thorough | `standard` / `thorough` / **`thorough+`** / `exhaustive` — how many of OUR brings get audited. `thorough+` is thorough with the equilibrium pilot: the only tier whose record is not inflated |
| `--pilot NAME` | tier default | **who plays the games the record comes from.** `greedy` is fast and inflated (see §4.0); `equilibrium` plays both sides as a matrix game, ~13× slower per game |
| `--evaluation NAME` | `sacrifice` | `sacrifice` scores speed control and discounts a spent Pokémon (measured cost: record 80% → 74%); `legacy` restores the previous evaluation exactly |
| `--beam-width N` | 30 | how wide the beam SEARCHES. `--candidates` already raises it to match; set this only to search wider than you rate |
| `--screen-vs NAMES` | all | rate STAGE 1 against only these opponents. Cost is linear in opponents, so two instead of eight is 4× more teams per night |
| `--substitute N` | off | after stage 1, try to improve the top N teams by swapping their worst member. The only stage that steers the search by the rating |

### Screening a large pool — the funnel, with its measured costs

`rate_team` already IS a funnel: cheapest screen first, and every stage
abandons the team before the next one runs.

| stage | s/team | 1000 teams, 8 workers | what it costs you |
|---|---|---|---|
| screener matrix | — | **112 s once** | shared by every team |
| beam → 1000 finalists | — | **77 s** | free, effectively |
| `--punish-screen` | 18 | **0.6 h** | opening only |
| `--min-winrate` (quick verify) | 94 | **3.3 h** | win count, greedy pilot |
| standard rating, greedy | 150 | 5.2 h | |
| standard rating, equilibrium | 600 | **20.8 h** | the honest one |

So 1000 teams rated properly is a 20-hour job, and 1000 teams *screened* down
to ~100 and then rated properly is a 4–5 hour one. A worked recipe:

```bat
:: 1. Screen wide and cheap, against TWO opponents rather than eight
overnight.bat --list --candidates 1000 --pool-size 50 ^
  --screen-vs "Big 6,Rain" --punish-screen --min-winrate 0.45 ^
  --pilot equilibrium --optimise-sets --jobs 8

:: 2. Deep-search the survivors against EVERYONE
overnight.bat --stage2-only --pick "1-40" --deep-effort thorough+
```

`--candidates` already raises the beam width to match — you cannot rate more
teams than the beam emits — so `--beam-width` is only for searching *wider*
than you rate (`--beam-width 2000 --candidates 1000` explores twice the space
and rates the better half).

`--screen-vs` is the biggest lever: cost is linear in opponents, so two instead
of eight is 4× more teams per night. The ranking it produces means "best
against these two", which is why the survivors are re-rated against the whole
library before you trust the order.

**Is 1000 actually 1000 teams?** Measured on a 50-pool: the beam's finalists get
*more* diverse as you widen it, not less — 29 distinct Pokémon and mean pairwise
overlap 3.06/6 at width 40, against 49 distinct and 2.23/6 at width 1000. The
most common 4-Pokémon core appears in 35% of the top 40 but only 10% of the top
1000. So widening genuinely searches, it does not just permute. It is still one
beam around one objective — see gap 1.

**The recall of these screens is NOT measured.** That is the real risk, and this
repo has been bitten by it before: `prescreen.py` measured 4–15% recall and is
dead. A screen that discards the eventual winner is worse than no screen,
because the team never appears in the output to be missed. Two mitigations that
cost nothing: the punish screen prints its value for *every* team it looks at,
so after a night you can check where the winners actually sat in that
distribution; and every rating is cached by key, so raising a floor and re-running
only re-rates what the old floor rejected.

### Reading and calibrating `--punish-screen`

The number is in `heuristic_eval` points, the same scale the rest of the tool
uses — **≈180 points is one Pokémon**. It is the value of the board after
turn 1 that we can *guarantee*: we pick our best opening, they answer it with
a perfect read of what we picked. Negative is normal and expected, because a
perfect read is not a fair fight. Measured on real rosters: Big 6 **−80**,
Sand **−160**, NAIC **−208**, a deliberately bad junk team **−307**.

The default floor of −250 is calibrated on those few teams, so treat it as a
starting point rather than a setting. Every stage 1 run now prints the number
for teams the screen **accepted** and for teams a **later** screen rejected,
plus a distribution line at the end:

```
  [2/4] skipped: below --min-winrate (396/552 won)   open -215   ~3 min left
opening guaranteed across 4 teams: worst -239, median -215, best -180   (--punish-screen floor was -250)
```

Read that line as the answer to "where should the floor be?". If the worst
*kept* team is far above the floor, the floor rejected nothing and cost you
nothing; if the whole distribution sits just above it — as in the run above,
where four teams between −180 and −239 all cleared the floor and then all
failed `--min-winrate` — the floor is too permissive for that pool and the
audit budget is being spent on teams that were never going to pass.

It screens on the **guaranteed value, not the punish**. The obvious version is
measurably backwards: a team with no threats gives a best-responding opponent
nothing to gain, so turn-1 exploitability ranked the junk team *first* (3.5,
against 49.5 and 66.6 for real teams).

### Pinning a set in `preferences.csv`

`Include` / `Exclude` / `Prefer` take a bracket that pins what that Pokémon
actually runs:

```
Include
Mamoswine (Life Orb)
Mega Gengar (Substitute, Protect, Shadow Ball, Sludge Bomb)
```

Each part inside the brackets is an **item** if the dataset has ever seen it as
one, and a **move** otherwise, so both kinds go in the same list and neither
needs a marker. A pinned set **overrides the optimiser** — it is a statement
about what you are bringing, so it is applied after `--optimise-sets` has run,
not before. A Mega keeps its stone regardless; a Mega without one is not a Mega.

`Include` now means what it says: every generated team **contains** that
Pokémon (it used to only make it eligible).

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
| **Team preview — fast** | you have about a minute | **`preview_lead.rank_leads`**: solves TURN 1 as a matrix game for each of your 15 lead pairs against each of their 15, and ranks your leads by the value you can GUARANTEE against their best reply. Measured **26 s** on a real preview |
| **Team preview — full** | you have minutes to hours | runs the advanced model on the bring/lead decision and returns ONE committed plan, its record, and what beats it |
| **Deep dive** | you have led, and so have they | the same model one notch deeper on that position, ~5 s, and depth 2 is available here because one line can afford it |
| **Load an overnight run** | after a batch run | browse any committed line turn by turn, from the cache. Instant |

**Picking the opponent, in all three.** Every panel takes the enemy team the
same way: a **preset** from `teams.csv` (Big 6, Rain, …), six hand-picked
Pokémon, or a pokepaste. A preset brings its recorded sets with it, and fills
in its fixed lead and its scripted opening where it has them — hand-typing the
same six by name silently answers a slightly different question, because the
script and the sets do not come along.

**The one-minute answer: which lead is not already lost?** The full model
cannot serve team preview — it takes minutes to hours and you have a minute.
What *is* answerable in that time is the opening: one matrix solve is ~0.5 s, so
your 15 lead pairs against their 15 is 225 solves ≈ 112 s — and **maximin
prunes**. A lead is worth its WORST case over their leads, and a minimum only
falls, so once a lead is behind a fully-checked one nothing can rescue it.
Measured: **58 solves, 26 s** on a real preview instead of 225.

It reports, per lead: the guaranteed turn-1 value, WHICH of their leads is the
one that hurts, and whether your answer to it is to **attack, protect or
pivot** — the last being the "I can switch out to create a winning position"
case, named rather than buried in a log. A lead abandoned by pruning shows its
number as `<= N`, an upper bound, because on a real preview a pruned lead read
81 against a proven 73 and that ordering is backwards.

**Then the back two, which is a separate search.** The lead ranking solves
turn 1, where your back is off the field and barely matters — so it uses a
placeholder. The back decides what you pivot *into* and whether the endgame is
2v2 or 2v1, so `preview_lead.rank_brings` plays all six possible back pairs out
against their leads and keeps the best WORST case. On a real preview it moved
the answer: the assumed back scored 25%, a searched one 50%.

**And then what — the line.** Ranking leads says what to send out; it does not
say what to play. `preview_lead.lines_for_lead` takes the chosen lead and
returns the turn-by-turn plan against each of their leads: your joint action
each turn, their equilibrium reply, the KOs, and **the probability that line
wins**, sampled over real damage rolls and speed ties with an interval on it.

Played against their **equilibrium reply**, not against a best response to the
move you just made — that opponent is clairvoyant and beat every team tested
(0 wins in 24 lines the same teams won 180/180 against the standard model).

Hardest enemy lead **first**, so the budget can only ever cut the easy ones.
Measured end to end: **30 s for the lead + 34 s for the lines**, and it is
honest about bad news — on the sample preview the best lead still returns a 0%
loss against Pelipper/Mega Swampert, which is the thing you want to know before
you sit down rather than after.

What it does **not** know is everything after turn 1 *for the lead ranking*. A
lead that opens cleanly and collapses on turn five ranks fine; the line is what
tells you it collapses.

**Deep dive assumes you cannot see their back.** You give their six and the two
they led with; the back is what you do *not* know at that moment, so by default
it audits **every back pair they could still be holding** behind that lead and
reports how the answer varies. One winning line is not a plan if the other five
lose. Pick a specific back only if you have actually scouted it.

**The opening is chosen at the information set you actually have** — your four,
your lead, their lead. Not their backs, which are face down, and not their
turn-1 action, because the turn is simultaneous. So the *Lead / Back* tab's
opening breakdown picks **one** turn-1 action and plays that same action against
every opening variant crossed with every back pair, each side bringing four.
Previously it solved our response separately per variant, with that variant's
script handed to the solver — Hard Trick Room got one answer if its Incineroar
faked out the left slot and a different one if it faked out the right, which is
a plan that reads their mind. Turns after the first still use the solver,
script included; by then their line is observed.

**Betting on a back pair is allowed and priced.** Name the pair you think they
brought and the opening is chosen against it alone — but it is still *reported*
against all six, so you can see what the hunch costs against the other five.

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

**Generate Team — screening before reporting.** The *"give up on a team as soon
as ANY single matchup drops below X%"* slider drops a team from the report
entirely, rather than annotating it after the fact, and promotes the next
candidate in its place. It checks one opponent at a time and abandons on the
first failure, so a team with a hole costs one matchup instead of eight, and the
rejects are listed with the opponent that ended each of them. The aggregate win
rate cannot express this: 90/90 seven times and 20/90 once averages 88%.

**Team Builder — what you can set by hand.** Items, **abilities**, **moves**
and stat points are all overrides that travel together and are read by *every*
simulation the app runs, not just the tab you set them in.

* **Abilities.** Arcanine-Hisui is 68% Rock Head / 32% Intimidate, and which one
  you run changes the matchup. A Mega pick's Mega-form ability is fixed by the
  species; the override sets its **base** form's, which is what it has on the
  turn it comes in.
* **Moves.** Up to four, from what it is recorded using — or from every move in
  the game with the tick-box, which does **not** check learnset legality
  (the dataset has none). A move the usage data has never seen is built anyway
  and reported at 0% usage.
* **Items** can be picked one at a time (the optimiser decides all six at once,
  which is a different job); a Mega-capable pick is locked to its stone, since a
  Mega is a species choice here, and "Any item…" opens the full catalogue.
* **Apply is per-editor and no longer destructive.** Each Apply merges into the
  same override dict, and the optimiser merges rather than replacing, so
  optimising items+moves keeps a hand-set ability and stat spread. Applying one
  editor also no longer discards a pending edit in another.

**The sets travel with the team you picked, not with the Team Builder.** Every
panel's *Our side* control offers the loaded team, a saved team, or any Pokémon
— and each supplies its own overrides. Choosing a saved team used to keep
applying the Team Builder's items and moves to somebody else's Pokémon.

**Model settings — the sidebar.** Two controls, because they change what every
other number means: **who plays the games** (greedy / equilibrium) and **which
evaluation** (`sacrifice` / `legacy`). The greedy default carries a standing
warning that records are inflated. `legacy` is the answer to "my winning lines
got worse" — it turns off the speed-control and spent-Pokémon terms, restoring
the pre-§4.2 evaluation exactly.

**Everything the CLI can do, the app can now do.** `--punish-screen` (with its
floor, and the distribution printed so it can be calibrated), the per-matchup
floor, `--pilot`, `--evaluation`, and the `--substitute` hill-climb, which is
in the Team Builder as *"Improve this team — swap its worst member"*. It
proposes swaps, rates each one the same way a generated team is rated, and
rejects any that loses record — record first, exactly as the CLI loop does.

**A/B test one change — Lead / Back tab.** Two variants of the loaded team
against the same opponents, same turn cap, same enemy overrides, with only one
member's ability or moves different. Reports wins per opponent for each and the
net games gained, so "is Intimidate better than Rock Head here" is one number
instead of two runs compared by eye.

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

0e. **OUR SIDE SWITCHES AND PROTECTS INSTEAD OF PLAYING.** The live defect, and
   it is a PILOT problem, not a mechanics one.

   Found while checking a reported "0% that seems winnable". The mechanics were
   fine — Mega Charizard Y does get Drought on turn 1 and it does override
   Pelipper's rain (`tests/test_mega_weather.py` pins it). What loses the game
   is what our side *does*:

   | line | result | OUR switches | THEIR switches | our Protects |
   |---|---|---|---|---|
   | Charizard/Garchomp vs Pelipper/Swampert | loss, 11 turns | **6** | 1 | **8** |
   | Charizard/Garchomp vs Incineroar/Torkoal | win, 9 turns | 5 | 2 | 2 |
   | Big 6 vs Sand | loss, 5 turns | 2 | 1 | 3 |

   Fourteen non-attacking actions out of twenty-two slot-turns in the losing
   line. It sets sun on turn 1 and then switches the Charizard out, spends the
   five sun turns pivoting, and attacks twice.

   **The likely mechanism is the information asymmetry.** `_attach_movesets`
   gives THEM six plausible moves per Pokémon and US our known four (§4.0,
   deliberately). In the payoff matrix that prices every aggressive option of
   ours against a wider set of their threats, while their options are priced
   against our narrow four — so passive play is systematically cheaper for us
   than for them. That is a hypothesis with a mechanism, not a measurement;
   the honest test is to re-run these lines with `--symmetric-info` and count
   switches again.


0c. **A TURN CAP IS A CLOCK, AND A CLOCK IS ADJUDICATED.** Capped games used to
   count as losses. That is not conservative, it is wrong: a 3-1 position with
   their last Pokemon on 8 HP is a won game, and calling it a loss discards
   exactly the grind-out lines most real games are.

   `Battle.adjudicate()` applies the real VGC rule — **Pokemon remaining, then
   HP percentage** (a fraction, so a bulky survivor does not beat a healthy
   frail one for free). All three `win_rate` estimators use it; `timeouts`
   still records how many games needed adjudicating.

0d. **THE LINE AND THE WIN RATE USED TO PLAY DIFFERENT OPPONENTS.** Found while
   chasing a reported "0% that seems winnable": `robustness.turn_robustness`
   advanced the line against their **modal** action (`argmax(q)`) while
   `matchup_search` sampled their mixture. A mode is a pure strategy and the
   equilibrium does not prescribe one — it is a systematically weaker opponent.

   Measured on Charizard-Y + Garchomp into Pelipper + Mega Swampert: the LINE
   reported a **win in 15**, the same position played out **0-4 against us in
   11**. Both now sample, and both say loss in 11.

   So the 0% was right and the *line* was wrong. Every "line to a win" this
   system produced before this fix was generated against a weaker opponent than
   the probability beside it was measured against.


0b. **THE RECORD IS NOT A PROBABILITY, AND ITS AGGREGATE ASSUMES THEY CHOOSE AT
   RANDOM.** Reported as "these winrates are not sound". They are not.

   `wins / 90` plays ONE game per enemy bring, on the average damage roll, with
   ties broken deterministically, scores it 1 or 0, and takes the uniform mean.
   Three separate errors:

   **P1 — a matchup's value is a probability, not a boolean.** Damage varies
   over a 16-step range, speed ties are coin flips, secondaries fire or do not.
   Measured on NAIC vs Hard Trick Room: **39 of 48 cells (81%) were genuinely
   uncertain, averaging 46%** — near coin flips, each recorded as a clean win
   or loss.

   **P2 — their bring is chosen, not drawn from a hat.** At preview both sides
   see the other's six and pick four *simultaneously*. That is a matrix game
   and it has a value. The uniform mean is the number you would want if they
   picked by lottery.

   **P3 — a proportion without its interval is not a measurement.** Wilson, not
   the normal approximation, because these numbers live near 0 and 1.

   Measured, NAIC vs Hard Trick Room, 4 of our brings × 12 of theirs × 8
   samples:

   | reading | value | what it assumes |
   |---|---|---|
   | uniform (today's `X / 90`) | **41.1%** | they choose at random |
   | **game value** | **30.6%** | both sides choose well — *the answer* |
   | worst case | **0.0%** | they counter and we cannot mix |

   The worst case being **zero** is the finding that matters: every one of our
   brings loses to something they can bring, so **mixing is worth the entire
   30.6%**. "Commit to ONE bring" — §1's framing — is itself unsound against
   that opponent. `game_value` always sits between the other two, and the test
   suite pins that ordering.

   **What this costs, and which estimator to use.** The full version is
   `samples × our brings × their brings`, so the per-cell estimator decides
   whether it is affordable. Measured on 10 real cells against a 40-game ground
   truth (`tools/measure_estimator.py`):

   | estimator | cost/cell | mean abs error | worst cell | ranking |
   |---|---|---|---|---|
   | old (1 game, avg roll) | 0.7 s | 0.25 | 0.78 | 96% |
   | quadrature (3 rolls × 2 ties) | 4.5 s | 0.17 | 0.78 | 91% |
   | sample-8 | 6.1 s | 0.13 | 0.35 | — |
   | **adaptive** (8–24 games) | 11.4 s | **0.05** | **0.17** | — |
   | truth (40 games) | 31.4 s | — | — | — |

   **Adaptive is the one to use** — a fifth of the old method's error at 2.7×
   cheaper than the ground truth. It samples in batches and stops once the
   Wilson interval is narrow enough, so a settled cell costs 8 games and a
   coin-flip cell costs 24. It cannot bias the estimate; it changes only how
   many honest games get played.

   **Quadrature — "use 3 damage rolls instead of 16" — was tried and LOSES.**
   It halves the mean error but keeps the same catastrophic worst cell (truth
   78%, estimate 0%) and *ranks worse* than the single playout it replaces.
   The reason is structural: pinning every roll in a game to one index is a
   **correlated** extreme, and real games roll independently. "All rolls low"
   is a game nobody plays, and the mid scenario is not the median game but the
   all-median-rolls game — a line needing one high roll somewhere in twelve
   turns gets it almost always in reality and never under quadrature.

   **Double oracle over the bring matrix also saves nothing**: 720/720 cells at
   8 × 90 on random matrices, 556/720 (77%) when their brings cluster into
   archetypes. Computing the column player's best response requires scanning
   every column. Kept as correct-but-inert.

   At 11.4 s/cell an 8 × 90 audit is **~2.3 hours for one pairing** — the
   decision point (team preview), not screening.

   **But P2 is nearly free.** `win_rate.aggregate()` takes floats, so the
   existing deterministic 0/1 matrix can be run through it at zero extra
   simulation cost and gives the game value instead of the uniform mean. That
   fixes the largest of the three errors everywhere, today.

   **Not yet wired into the pipeline.** `X / 90` is still what the workbook and
   the app report.


0. **THE RECORD IS MEASURED AGAINST AN OPPONENT WHO PLAYS BADLY. Read this
   before any other number in this file.**

   `play_out_pair` plays OUR side with the real solver and THEIR side with
   `greedy_opponent_joint_action`, a policy this codebase wrote itself. That is
   a systematic advantage for whichever side is passed as p1, and it is large.

   `tools/measure_side_bias.py` plays the same two bring-4s twice with the
   sides swapped. If a result is real, mirroring it flips the winner.

   | pilot | contradictions | direction | cost |
   |---|---|---|---|
   | greedy (default) | **21 / 27 decided (78%)** | **all 21 are "p1 wins both"** | 3s |
   | equilibrium | 9 / 22 decided (41%) | 3 p1 / 6 p2 — no side advantage | 40s (13×) |

   The greedy figure is not noise: every single contradiction points the same
   way. The visible symptom is that all eight teams in `teams.csv` score
   83–99% against each other, which cannot be true — Rain and Big 6 both count
   their head-to-head as a win.

   **What this invalidates:** every `Wins / Of` in every sheet, the generator's
   ratings, `--min-winrate`, `--worst-matchup`, and any comparison between two
   teams that were each rated as p1. They measure "how does this team do
   against a bad opponent", and a line that wins there can lose a real game —
   which is what "a great number of losses were marked as wins" is.

   **What it does not invalidate:** the punish/exploitability numbers, which
   already run a best-responding opponent, and `heuristic_eval`, which is
   perfectly antisymmetric over 8242 states (`measure_antisymmetry.py`).

   The equilibrium pilot removes the *greedy* bias. **A smaller, opposite one
   remains, and it is not yet fixed.** Two obvious explanations were tested and
   both are wrong:

   * *"It is unresolved games."* No. At `--turns 24` every matchup decides
     (undecided 6 → 0) and contradictions do not fall: 41% → 43%.
   * *"A mixed equilibrium is sampled, so one playout is a coin flip."* No. The
     same matchup, same sides, run 8 times, gives the same winner 8 times under
     both pilots — the mixture draw is seeded per decision.

   The residual turned out to be **two separate things, and only one of them
   was a bug.** 28 mirrored matchups at `--turns 24`:

   | pilot / seat rule / info | contradictions | direction |
   |---|---|---|
   | greedy | 78% | **21 p1 / 0 p2** |
   | equilibrium, ours samples + theirs modal | 43% | 3 p1 / 9 p2 |
   | equilibrium, both seats sample | 29% | 0 p1 / 8 p2 |
   | equilibrium, old rule, `--symmetric-info` | 29% | 4 p1 / 4 p2 |
   | **equilibrium, both sample + `--symmetric-info`** | **14%** | **2 p1 / 2 p2** |

   **The seat rule was a bug, and is fixed.** `_equilibrium_joint_actions`
   sampled OUR action from mixture `p` but took THEIR single modal action from
   `q` — a pure strategy, which is not what the equilibrium prescribes. Two
   different players wearing one name. Both seats now sample. Worth ~15 points.

   **The wider opponent move space is not a bug.** `_attach_movesets`
   deliberately gives the opponent six plausible moves per Pokémon against our
   known four, because assuming they run the usage-standard four is
   self-fulfilling and measured at 10 points of win rate. It is correct
   modelling that rides with the *seat*, so it shows up in a mirror test as a
   p2 lean — strip it with `measure_side_bias.py --symmetric-info` and the
   direction balances exactly (4/4, then 2/2). Do not "fix" it.

   **What is left: 14%, pointing both ways.** Genuine near-ties, now measured
   rather than assumed. `q` was also checked and is a real equilibrium mixture
   from `solve_matrix`, not a best response — that hypothesis was wrong too.

   ### The trap the honest pilot sets

   **Every screen threshold in this repo was calibrated against greedy
   records, and those are roughly double.** Leave `--min-winrate` at its 0.80
   default under `--pilot equilibrium` and it rejects teams better than
   anything in `teams.csv` — the best hand-built team scores **60.6%** under
   this pilot. Use `--min-winrate 0.45` and scale `--worst-matchup` the same
   way. Stage 1 now prints a warning if you forget.

   **What the fix is worth, measured.** Same three teams, same opponents, same
   effort, same evaluation — only the pilot differs
   (`tools/measure_generator.py --control-only --pilot equilibrium`):

   | team | greedy | equilibrium |
   |---|---|---|
   | Rain | 449/462 **97.2%** | 232/462 **50.2%** |
   | Big 6 | 425/462 **92.0%** | 280/462 **60.6%** |
   | Hard Trick Room | 455/546 **83.3%** | 185/546 **33.9%** |

   Records roughly halve. The greedy column is the one that claimed every team
   in the library beats every other one; the equilibrium column is teams
   beating a third to two thirds of their opponents' configurations, which is
   what a field of comparable teams should look like. Cost was ~600s per team
   against ~150s — about 4×, not the 13× the single-playout sweep suggested,
   because the audit is unchanged and is a large share of the total.

   Adjusted wins and punish move slightly too (Big 6 0.71 → 0.60; Rain
   unchanged), because the pilot also decides which brings are selected for
   auditing. The audit itself was always equilibrium-piloted.

   **How to get an honest number today:**

   ```bat
   overnight.bat --pilot equilibrium ...      :: any tier
   overnight.bat --gen-effort thorough+ ...   :: the same thing, as a preset
   ```

   In the app it is the sidebar's **Model settings → Who plays the games**, and
   the strength slider's **Thorough+** tier forces it for that panel regardless
   of the sidebar. The greedy default is left in place because it is 13× faster
   and still useful for ranking, but every panel now says which one produced
   the number in front of you.


1. **The beam still ranks on coverage, and a better objective did not beat it
   (yet).**
   `team_search.py` has zero knowledge of exploitability; the beam ranks on
   coverage and synergy, and `generate_overnight` only widens the funnel (rate
   40 finalists, not 3). What is new is `--substitute` (§2): it takes the rated
   teams and hill-climbs them on the **rating itself**, accepting a swap only
   when the audit improves. So the shortlist is no longer purely
   *high-coverage teams that were then measured* — its top entries have been
   refined against the real objective. The generator's own objective is still
   coverage, and a local search around a coverage-picked team cannot reach a
   great team that the beam never proposed.

   The objective itself was rewritten once and **measured**, because that is the
   only way to tell: score the WORST third of threats instead of the mean,
   require a real margin before calling a threat answered, and credit a second
   independent answering pair. Then play the top 4 teams each objective proposes
   against the whole preset library with the real engine
   (`tools/measure_objective.py`):

   | objective | record across its 4 teams | best single team |
   |---|---|---|
   | old (mean coverage) | 1927/2208 (87.3%) | 92% |
   | new (worst third + answer depth) | 1885/2208 (85.4%) | 89% |

   So it lost, and the terms ship at zero rather than being deleted. The likely
   reason is worth knowing before trying again: **the objective can only be as
   good as its input**, and the input is a greedy 2v2 playout margin. "Average
   margin" tracks "average wins" well; the tail and the redundancy the new terms
   score are real properties the screener margin barely sees. In order of
   promise: a better screener margin, then scoring against the leads they would
   *plausibly* bring rather than all C(6,2) uniformly, and measure every
   candidate the same way — intuition lost here.

   The second of those was then tried, and it is a **dead heat**:
   plausibility-weighting the enemy leads scores 1928/2208 against the uniform
   1927/2208, picking almost the same teams. Both attempts now point the same
   way: what limits the search is the **screener margin underneath it** — a
   greedy 2v2 playout — not the shape of the function applied to it. That is
   the one worth attacking next, and it is not a quick change.
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

   The committed choice now reads the **record first**, then adjusted wins,
   then punish. It used to rank on adjusted wins alone, and that committed to
   brings beating fewer of their configurations — measured, NAIC vs Big 6: a
   bring going 89/90 with 61 audited lines won was chosen over one going 90/90
   with 68, because its wins were rated less punishable. Records are compared
   to the nearest whole percent, so inside a band the sounder line still wins.
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
