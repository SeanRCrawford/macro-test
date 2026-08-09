**Start here: [docs/WORKFLOW.md](docs/WORKFLOW.md)** -- what to run, how to read the output, what is current, and the known gaps.

# VGC Champions M-B Team/Lead/Back Search

Finds, for each opponent team in `data/teams.csv`, which (bring-4-of-6) x
(lead-2-of-4) arrangement of your Pokemon beats them -- using an actual
turn-by-turn battle simulation (not a static heuristic), with full
visibility into every alternative considered, not just the winning pick.

## Setup

**Windows:** double-click `run.bat` (or run it from a terminal). First run
creates a virtual environment and installs dependencies automatically.

**Mac/Linux:** `chmod +x run.sh && ./run.sh`

Requires Python 3.11+. If Python isn't installed, get it from python.org
(Windows: check "Add python.exe to PATH" during install).

## Interactive app (easiest way in)

```
./run.sh app
```

Opens a Streamlit UI in your browser with four tabs:

- **Team Builder** -- pick your 6 from a searchable list, load any saved
  `.json` from a dropdown or upload one, save under any filename, or download
  the sheet directly;
  see the full sheet with items/abilities/natures/EVs/moves, run item+moveset
  optimisation, and check the defensive-coverage table against the
  <=2-weaknesses rule.
- **Verified teams (leaderboard)** -- every team you solver-verify accumulates
  here, numbered and ranked by enemy bring-4s beaten across all opponents, with
  a per-opponent breakdown. Select any subset and export them as a ZIP of
  individual `.json` files, one combined `.json`, or write them to disk.
  Exports use download buttons rather than normal buttons, so the page no
  longer resets after an export. Inspecting a team shows, per member, how many
  threats it is the team's best answer to and how many it is the ONLY answer to
  -- the concrete case for its slot.
- **Move contributions** -- for the loaded team, shows per move how many enemy
  Pokemon it is the best option against and the largest share of any enemy's HP
  it removes. Moves that are best against nobody and do little damage are
  flagged as dead weight, with suggested replacements (including setup and
  utility moves, which a damage-only score ranks poorly but which can convert a
  losing matchup). Recomputed on demand, so it reflects a freshly loaded .json.
- **Configurable defensive rules** -- set the maximum members allowed to share
  a weakness, and optionally cap NET weakness (weak minus resistant) per type,
  which is usually the better rule.
- **Generate Team** -- run the full generation search with sliders for pool
  size, beam width, and how many finalists to solver-verify. Each result can be
  sent straight to the Team Builder or saved to its own `.json`.
- **Lead / Back Search** -- defaults to testing **all 90 enemy bring-4s** per
  opponent. Results branch: expand an opponent, then a specific enemy bring, to
  see that battle's turn-by-turn log, mega choices and result. A "show only
  losses" toggle cuts straight to the problems. Exports CSV.
- **Battle Viewer** -- simulate one specific bring-4 vs bring-4, see the result,
  which Mega each side chose, and the full battle log.

## Editing the data

The app keys its cache on the modification times of `mbsmogon.xlsx`,
`roster.csv`, `teams.csv` and `preferences.csv`, so hand-edited EV spreads,
items or moves are picked up on the next rerun. There is also a **Reload
data** button. (Earlier versions cached the parsed spreadsheet for the life of
the process, so edits appeared to have no effect -- the EV maths was always
correct, the app just never re-read the file.)

## Performance

A composition search that took ~40s now takes under a second, and
comprehensive mode for one opponent went from ~850s to ~18s. Profiling showed
**98.7% of solver runtime was `copy.deepcopy`** -- the search deep-copied the
whole battle (including the 954-move database and type chart) for every
candidate action it explored. Two targeted `__deepcopy__` methods (one on
`Battle`, one on `Combatant`) fixed it: read-only tables are shared by
reference and flat scalar dicts are copied directly instead of recursively.
Results were verified identical before and after on a spread of matchups.

## Two tools

### 1. Team GENERATION -- build an optimal 6 from the whole pool

```
./run.sh generate                        # default settings
./run.sh generate --pool-size 40          # consider more candidates (slower)
./run.sh generate --beam-width 60         # wider search
./run.sh generate --top 3                 # report the best 3 teams
./run.sh generate --verify 2              # solver-verify the top 2
./run.sh generate --no-verify             # fastest, screening only
./run.sh generate --optimise-sets         # optimise each member's item + 4 moves
```

**`--deep`** verifies finalists against EVERY enemy lead using the real solver
with full bring-4 (leads AND backs), rather than only each opponent's toughest
lead. This is the number to trust. It was impractical before the performance
work; it now takes ~5 minutes for one team across all five opponents.

**Each item may appear only once per team.** `optimise_team_unique` scores
every legal (Pokemon, item) pairing, then assigns greedily by regret -- the
Pokemon that loses most by being denied its first choice picks first -- so two
members never share a Life Orb. Mega Stones are species-specific and never
collide.

**Type-boosting items give +20% to that type** (Mystic Water/Water, Black
Glasses/Dark, Charcoal/Fire, and the rest), and **Expert Belt gives +20% on
super-effective hits**.

**Items are restricted to each Pokemon's own Items column** in
`mbsmogon.xlsx`. There is no hardcoded catalogue of "good items" -- the
optimiser will never hand something a Choice Band it is never recorded using,
and a Mega with a stone is locked to that stone.

**Protect/Detect is reserved on every non-Choice set** where the Pokemon has
access to it. In doubles it blocks spread damage, stalls out Trick Room and
Tailwind turns, and scouts -- a pure damage score can't see any of that. On a
Choice item it's a dead slot (you lock into it), so it's skipped there.

**Priority moves get an explicit bonus.** Accelerock, Aqua Jet, Sucker Punch
and similar are worth far more than their base power implies -- they revenge-kill
weakened threats and ignore the speed tier entirely. A damage-only score reads
Accelerock as a feeble 40 BP move and cuts it from the set; the optimiser and
the solver both now credit priority, with an extra bonus when the move actually
secures a KO (a KO in the priority bracket cannot be pre-empted).

**Speed control and turn denial are valued explicitly**: Tailwind, Trick Room,
Icy Wind, Electroweb, Bulldoze, Rock Tomb, Thunder Wave, plus Fake Out and
Follow Me / Rage Powder redirection. Icy Wind does little damage but dropping
both foes' Speed can decide every later turn, which raw damage misses.

**`--optimise-sets`** computes, once, the value of every candidate move
against every distinct Pokemon in teams.csv, then picks the 4-move set with
the best coverage of that specific threat list -- plus the best item, scored
on offensive coverage AND survivability (Focus Sash on frail sweepers,
Leftovers/Assault Vest on walls, with Life Orb's 10% recoil counted against
it and Choice items heavily penalised on sets that need flexibility). This
is what surfaces picks like Gallade with Leaf Blade. It uses 1v1 damage
maths, not full battles: it knows what hits hard, not what survives long
enough to use it.

Move selection also accounts for doubles utility that a pure damage score
can't see: **a protect-type move is reserved on every non-Choice-locked
Pokemon** (Choice users skip it, since they'd be locked into a dead slot),
and speed control (**Tailwind / Trick Room**, plus damaging speed drops like
**Icy Wind / Electro Web**), **redirection** (Follow Me / Rage Powder),
**Fake Out**, and setup moves all carry explicit utility value.

**Ranking is lexicographic:** the number of enemy lead pairs a team beats
always ranks first. Type cores, the weakness rule, and Score are bounded
tiebreakers that can never buy back a lost matchup.

Pipeline: filter the ~270-mon dataset by Score and preferences.csv -> build a
pair matrix (every candidate lead pair of yours vs all 75 possible enemy lead
pairs, via a fast greedy screener) -> beam search 2->6 members scoring on
simulated coverage plus synergy -> switch-rescue analysis -> verify the
finalists with the real solver.

Reports the full team sheet (**items**, abilities, natures, EVs, top moves,
Score), a synergy breakdown (which type cores matched, which types break the
<=2-weaknesses rule), and verification results per opponent.

`--pool-size` cost grows roughly quadratically: 24 -> ~20s matrix,
34 -> ~35s, 50 -> ~80s. Verification adds ~3 min per team verified.

**Two-speed design, stated plainly:** stages 2-4 use a fast greedy screener
(both sides just pick max damage, no lookahead) so that tens of thousands of
matchups are affordable. Only the finalists get the real solver. Coverage and
margin numbers are screening numbers; the verification block is the
trustworthy one. If they disagree, believe verification.

### Porting a generated team into the lead/back search

`generate_team.py` writes **`team.json`** (names + optimised items + 4-move
sets) next to the package root. Feed it straight in:

```
./run.sh generate --optimise-sets      # writes team.json
./run.sh --team-file team.json          # searches leads/backs using those exact sets
```

Without `--team-file`, the search falls back to raw usage-stat defaults --
i.e. you'd be planning around a different team than the one you generated.
`team.json` is plain JSON: edit an item or swap a move by hand and re-run.

### 2. Lead/back SEARCH -- arrange a team you already have

```
./run.sh                                 # quick mode, default 6-mon pool, all 5 teams
./run.sh --mode comprehensive              # test every possible enemy lead pair (slow)
./run.sh --pool "A,B,C,D,E,F"              # your own 6 Pokemon
./run.sh --team "Big 6"                    # only run against one team
./run.sh --max-turns 20                    # raise the draw/timeout turn cap
./run.sh --show-top 10                     # print more alternatives per team
./run.sh --team-file team.json             # use a generated team sheet (items + moves)
./run.sh --all-backs                       # test all 90 enemy bring-4s, report best worst case
```

**Quick mode** (default) finds each enemy team's single toughest lead pair
against a default answer first, then searches your compositions against
just that. ~60-70s/team.

**Comprehensive mode** (`--mode comprehensive`) tests all 15 possible enemy
lead pairs per team and finds the single combo of yours that wins most
often across all of them. Far more robust, but ~15x slower -- realistically
10-15 minutes *per team*, so run it overnight for a final answer, or scope
it to one team at a time with `--team`.

## Output: three ways to see the results

1. **Console** -- summary + top N alternative combos per team as it runs.
2. **`results.xlsx`** -- full transparency. A `Recommendations` sheet
   summarizing the pick per team, plus per-team `<Team> Combos` sheets
   listing **every single bring-4/lead-2 combination that was tested**
   (not just the winner) sorted best-first with win/loss/turns, and
   `<Team> Gameplan` sheets with the actual turn-by-turn play-by-play
   (every move, target, damage number, flinch, faint, switch, weather/
   Trick Room/Tailwind change) of the recommended composition's battle.
   In **comprehensive mode** the workbook additionally contains a
   `<Team> All Leads` sheet (one row per possible enemy lead pair, with our
   best answer, result, and how many of our combos beat it) plus a separate
   gameplan sheet per enemy lead -- not just the single toughest one.
3. **`results.html`** -- open it in any browser, no server needed. Same
   gameplan data as the Excel Gameplan sheets, but as a readable, clickable
   report: click a team's header to expand its turn-by-turn log, with
   inline HP bars and color-coded win/loss badges. In comprehensive mode each
   team expands into every possible enemy lead, each with its own gameplan.

Both files are regenerated (overwritten) every run, saved next to this
package's root folder unless `--out-dir` is given.

## Mega Evolution mechanics (important, affects team-building)

- **At most one Pokemon per brought team (4 or 6) can actually Mega
  Evolve.** You CAN still bring a *second* mega-capable species -- it just
  runs in its base (non-mega) form the whole game. This is legal and is a
  real tech: e.g. Tyranitar's Sand Stream already works unevolved, so you
  can bring a base-form Tyranitar for guaranteed immediate weather control
  alongside a different Pokemon that's your team's actual Mega Evolution
  (say, Mega Charizard Y for Drought/offense elsewhere).
- **Convention used by this tool:** in any list of Pokemon names you give
  it (`--pool`, or a `teams.csv` row), the *first* Mega-named entry (e.g.
  `"Mega Charizard Y"`) is the one that actually transforms. Any
  additional Mega-named entries after it automatically run in base form
  all game. Reorder your list if you meant a different one to transform.
  This is why `teams.csv`'s five given teams each list two Mega-prefixed
  Pokemon -- that's intentional flexibility in how they were built, not
  a data error, and the tool resolves it the same way.
- **A Mega pick starts battle in its BASE form** (base stats, base
  ability, base typing) and only transforms the turn it's actually sent
  out (lead or later switch-in) -- matching the real game (Mega Evolution
  happens the instant a Pokemon is given an action, not before). This
  matters a lot for weather: Mega-exclusive weather abilities (Charizard
  Y's Drought, Abomasnow's Snow Warning) simply don't exist until that
  specific Pokemon is actually on the field transformed, unlike a
  base-ability weather setter (Tyranitar's Sand Stream, Pelipper's
  Drizzle) which works from the instant it switches in, mega slot or not.
  A benched mega weather-setter provides **zero** weather control until
  it's brought in -- plan around that, don't assume it's active turn 1
  unless it's actually leading.

## Stats: the Champions M-B house rule

EVs are flat +1-per-point bonuses added AFTER the normal Lv50 stat
formula, not the standard /4 investment system. Hardcoded in `src/stats.py`.

## Known limitations (read before trusting the numbers blindly)

- **The opponent AI is a greedy heuristic, not a perfect player.** Each
  enemy Pokemon uses whichever of its top-3-usage moves does the most
  immediate damage (or its known setup move like Trick Room/Tailwind on
  turn 1), and it never uses Protect. Not a game-theoretic guarantee
  against a human playing optimally -- see `src/solver.py` docstring.
- **Enemy back-2**: by default the tools use one arbitrary back pair
  (whichever two are listed next in `teams.csv`). Pass `--all-backs` to test
  **every** enemy bring-4 instead -- all C(6,2) lead pairs x C(4,2) back pairs
  = 90 configurations per opponent -- and report the composition with the best
  worst case. That matters: several compositions that look clean against one
  sampled back pair lose 8 of 90 once every back is tried. It also removes
  nonsense enemy brings (leading Mega Charizard Y with Mega Floette in the
  back, where only one can transform) by letting the opponent pick their own
  best configuration.
- **Still not modeled** (audited against the actual top-4 movesets in your
  data, so this list is real rather than reassuring): multi-hit moves
  (Bullet Seed, Population Bomb), variable-power moves that use a
  `basePowerCallback` (Acrobatics, Grass Knot, Heavy Slam, Eruption, Hex --
  these currently use their listed base power), `damageCallback` moves
  (Endeavor, Super Fang, Final Gambit), Body Press-style
  `overrideOffensiveStat`, self-switch moves (U-turn, Flip Turn, Parting
  Shot's switch half), forced switches (Roar/Whirlwind), OHKO moves
  (Fissure), Feint's protect-breaking, Destiny Bond, Encore, Taunt,
  terrain, and abilities outside the wired list below.
- **Not modeled at all:** sleep/freeze/confusion, *status conditions*
  inflicted as a move's secondary effect
  (e.g. Scald's burn chance -- flinch and stat-change secondaries ARE
  wired in, status-condition ones are not), miss chance, critical hits
  (damage uses the deterministic average roll by design, so lines are
  reproducible rather than luck-dependent).

### Turn order (corrected)

Each turn resolves in this order:
1. **Voluntary switches** (fastest first), firing switch-in abilities
   (Intimidate, Drizzle, Sand Stream...).
2. **Mega Evolution** -- resolved at the START of the turn, after all switches
   and before ANY move, in speed order -- but ONLY for Pokemon that are acting
   this turn.
   A Pokemon that switched in this turn does not act, so it stays in BASE form
   (base stats, base typing, base ability) until the following turn. Battle
   logs mark it `[base form]` while it is untransformed, which matters most
   when a team brings two Mega stones and only one will ever transform. This ordering
   matters: a Mega whose weather ability only exists post-transform
   (Charizard Y's Drought) resolves *after* a base-ability setter that
   switched in the same turn (Pelipper's Drizzle), so the Mega's weather is
   the one that sticks. At most one Mega per side, per battle.
3. **Moves**, by priority bracket then speed (inverted under Trick Room).
   Ability priority modifiers apply: **Prankster** (+1 to status moves, so a
   Whimsicott Tailwind or Trick Room resolves before attacks), **Gale Wings**
   (+1 to Flying moves at full HP, e.g. Talonflame), **Triage** (+3 to
   draining moves).
4. **End-of-turn** residuals (burn/poison, weather & field countdowns).
5. **Fainted Pokemon are replaced at the END of the turn**, so the incoming
   Pokemon is on the field and able to act starting the NEXT turn -- it does
   not get an action on the turn it arrives.

### Mega Evolution timing

A Pokemon Mega Evolves **only when it is about to move**, not on switch-in and
not at the start of the turn. One that is KO'd by a faster attack first never
transforms at all -- and its weather ability (Drought, Snow Warning) never
comes up. Still at most one Mega per side per battle.

### Held item recovery

Pinch berries (Sitrus 25%, Oran, and the flavour berries) are consumed and
restore HP as soon as the holder drops to half or below, checked immediately
after each hit lands. These were previously never consumed at all, which made
Sitrus Berry -- the single most common item in this metagame -- effectively a
blank slot in every simulation.

### Switch-in vs Mega ordering

Switch-in abilities fire on the **base** form, then the Mega transforms. So a
Mega Gyarados sent out drops the foe's Attack with base Gyarados's Intimidate
first, and only then becomes Mold Breaker. Same for Staraptor-style cases.
Weather is the exception worth noting: a Mega whose weather ability only
exists post-transform resolves *after* base-ability setters that entered the
same turn, so the Mega's weather wins.

### Mirrored species

`Combatant` uses identity equality (`@dataclass(eq=False)`). With dataclass
value equality, two distinct Pokemon of the same species and set -- your
Garchomp and their Garchomp at full HP -- compared EQUAL, so `side_of()`,
`side.active.index(...)` and every `in` check could attribute one side's
Pokemon to the other mid-turn. Battle logs also tag mirrored species with
their side (`Garchomp(p1)` vs `Garchomp(p2)`) so mirror matches are readable.

### Spread moves and your own ally

`allAdjacent` moves -- Earthquake, Surf, Discharge, Bulldoze, Explosion --
hit **every** adjacent Pokemon, including your own partner. `allAdjacentFoes`
moves (Rock Slide, Heat Wave, Dazzling Gleam, Muddy Water) hit only the
opposing side. Both the solver and the fast screener build spread targets
accordingly, and damage dealt to your own ally counts against the move rather
than for it, so the AI will avoid clicking Earthquake next to a Ground-weak
partner -- or pair it with Protect on that partner, which is the real answer.

### Targeting

Targets resolve at execution time, not selection time, and they follow the
SLOT rather than the Pokemon:

* if the target switched out before the move resolved, the attack hits whoever
  now occupies that same slot (the departing Pokemon's slot index is recorded
  at switch time). Redirecting to "the other active Pokemon" instead sends
  moves at entirely the wrong target;
* if the target fainted earlier in the turn, a single-target move redirects to
  the remaining opposing Pokemon rather than fizzling.

### Focus Sash / Sturdy

A holder at FULL HP survives a would-be KO at 1 HP and the Sash is consumed.
Not applied below full HP, and Sturdy behaves the same without being consumed.

### Pinch berries

Sitrus Berry (25% max HP at or below half) and the other pinch berries are
consumed the moment a hit takes the holder under the threshold, and again at
end-of-turn residuals. These were previously never consumed at all, so every
Sitrus holder -- the most common item in this metagame -- was effectively
itemless in every search.

### Switching

Voluntary switches are candidate actions for every active Pokemon, not just
forced replacements after a faint. Two things were needed to make pivoting
work at all:

* the bench must be passed into the action generator (it previously was not,
  so a voluntary switch was never even considered); and
* the evaluation needs positional terms. Pure HP differential can never
  favour a switch at depth 1 -- you spend a turn and usually take a hit -- so
  it now also scores accumulated stat drops, Choice locks, and the defensive
  matchup of your actives against what is facing them.

That makes the solver reset a Choice lock, pivot out of stacked stat drops,
and bring a resist in to absorb a hit that would otherwise lose the board.
Cost: a composition search goes from ~0.7s to ~10s because the branching
factor grows. Worth it -- enabling switches took the hardest Big 6 matchup
from 80% to 100%.

### Moves retarget when the target leaves

If the intended target switches out, the move hits whatever replaced it in
that slot. Previously the Action kept a reference to the departed Pokemon and
struck it on the bench, so switching acted as a free dodge.

### Mid-turn speed changes

Moves resolve one at a time and the remaining order is RE-SORTED after each,
because speed can change mid-turn -- Tailwind going up, an Intimidate, a Speed
drop -- and from Gen 5 onward that affects Pokemon that have not moved yet.
Sorting once at the start meant a Tailwind set this turn did nothing until the
next one.

### Sucker Punch

Fails unless the target is using a damaging move that turn: it whiffs into
status moves, Protect, and switches.

### Speed ties and damage rolls

`turn_order` breaks equal-Speed cases deterministically, which would report a
coin flip as a certainty. Two things address that:

* Every turn records any **speed tie** (opposing Pokemon, same priority
  bracket, exactly equal effective Speed) into `battle.speed_ties` and the
  log. A line that depends on winning one is not guaranteed.
* `matchup_search.win_probability(...)` is the honest headline number: a
  Monte-Carlo over damage rolls, secondary effects (Rock Slide / Iron Head
  flinches, etc.) and speed ties. Crucially the solver plans on a
  DETERMINISTIC clone and its chosen actions are then executed against the
  stochastic battle -- otherwise the solver searches the same RNG stream it
  is scored on and simply picks whichever branch the dice favoured, reporting
  near-100% for matchups that are really coin flips.
* **Planning is deterministic, outcomes are not.** The solver searches with
  average damage rolls and no secondary procs, then the chosen action is
  executed on the real battle where rolls, flinches and ties actually resolve.
  Previously the planned copy was adopted as the outcome, so the line and the
  result were the same sample -- every 30% Rock Slide flinch and every coin
  flip landed in our favour by construction, reporting ~100% win rates.
* `matchup_search.evaluate_risk(..., tie_bias=None)` gives a fair-coin win
  probability over N games; `tie_bias="p2"` gives the worst case.
* `matchup_search.evaluate_tie_branches(...)` runs the matchup with ties
  forced BOTH ways (`Battle(tie_bias='p1'|'p2')`). A line that only wins when
  you win the coin flip is not a plan -- this is the reliable check.
* `matchup_search.evaluate_risk(...)` forces ties to the OPPONENT and re-runs with **randomised
  damage rolls** (the real 0.85x-1.00x 16-step spread) and randomised
  tie-breaking, returning a win rate over N games rather than a single
  average-roll verdict. NOTE: ties must be forced, not randomised -- the
  solver plans against the same RNG stream it is scored on, so with random
  ties it just selects the branch where the shuffle favoured it and reports
  ~100%. That bias is why `evaluate_risk` pins ties to the opponent.

### Speed control

Tailwind and Trick Room are treated as genuine board-state swings by both the
solver and the fast screener: a user will set them whenever they aren't
already up (not just on turn 1), and Prankster / Gale Wings users value them
higher still since they get them off before attacks. Re-setting an effect
that's already active is penalised.

### Perish Song and Shadow Tag

Perish Song sets a 3-turn counter on every Pokemon on the field that can hear
it (Soundproof is immune). Switching out CLEARS the counter -- which is exactly
why Shadow Tag is the other half of a Perish Trap: it denies the escape. Shadow Tag stops
non-Ghost Pokemon switching out; Ghost types, Shed Shell holders and other
Shadow Tag users escape, and switching MOVES (U-turn, Parting Shot) are
unaffected.

### Scripted opponent openings

Three teams do not play greedily -- they execute a rehearsed line a
damage-maximising AI would never find (double Protect, a 0-damage Perish Song,
spending a turn on Shell Smash). `src/scripted_openings.py` scripts them:

* **King** -- redirect or Fake Out to buy a turn while Mega Blastoise uses
  Shell Smash or Mega Delphox uses Nasty Plot, then sweep.
* **Hard Trick Room** -- T1 Fake Out + Trick Room, T2 Parting Shot into a
  sweeper under TR.
* **Perish Trap** -- T1 Fake Out + Perish Song, then Protect/Protect while
  Shadow Tag holds you in and the counter runs down.

### Fixed-lead teams are scored on their fixed lead

A team declaring a `Lead` in teams.csv is verified on that lead only (6 brings,
not 90) -- the other 84 are brings it never makes. Generation also now applies
the opponent's SCRIPT during verification. Both were missing before, which is
why King read 90/90 in the generate tab but 72/90 in the lead/back tab: the
generate tab was testing it unscripted. The two now agree.

### Punish check (minimax turn 1)

`committed_plan.worst_response(...)` plays our committed action against EVERY
legal opponent response, not just their greedy pick or their script. The point
is punishment: if we assume they make the obvious play we may Protect the slot
they were going to hit and eat a KO on the other. A play is only safe if no
response of theirs blows it up. `find_unpunishable_plan(...)` combines both
tests -- beats every scripted opening AND survives every response -- and is the
strongest (and slowest) guarantee here.

### Team generation checks the special (scripted) cases the same way as everything else

A scripted team (King / Hard Trick Room / Perish Trap) is verified on equal
footing with every other opponent: `search_robust_composition` plays its
rehearsed script -- AND the generic "one Protects, the other attacks"
alternative any fixed-lead team could run instead (both choices of which
active protects), plus the plain unscripted 2v2 -- against every one of our
bring-4/lead-2 combos, with OUR side adapting each turn via the real solver
(only the opponent is scripted; you genuinely do see their move before
choosing yours on every turn after the first). `wins == total` already means
the team survives every scripted line as well as the conventional 90 -- there
is no separate stricter "one blind turn-1 action must work" requirement on
top of that (see `committed_plan.find_plan_unknown_backs` below, which is
still available as a standalone, stronger analysis but is no longer part of
generate_team.py's default pass/fail check, per user request: it's a much
stricter bar than the game actually demands, since you're never really
choosing turn 1 blind past the opponent's lead reveal).

### Unknown backs (partially complete -- read before trusting)

At turn 1 you have seen the opponent's lead and nothing else, so a plan must not
depend on which two Pokemon are behind it.

DONE and verified: `heuristic_eval` previously summed the opponent's ENTIRE
roster, including a bench that had never been on the field, so the solver was
quietly scoring positions using information the player does not have.
Combatants now carry a `revealed` flag (set on switch-in; only the two leads
start revealed) and only revealed Pokemon are counted.

WRITTEN BUT UNVERIFIED: `find_plan_unknown_backs(...)` requires ONE turn-1
action to beat every back pair the lead could be hiding (all C(4,2)) crossed
with every scripted opening. It imports and is syntactically sound, but the
validation run timed out before producing a result -- treat it as untested.
Cost is 6 back pairs x 3 variants x every candidate turn-1 action, so it likely
needs a screening pass or parallelism before it is practical.

### Committed plans (`src/committed_plan.py`)

`find_plan(...)` answers the question that actually matters: **is there a
single turn-1 action that beats every way the opponent might open?**

This is not the same as beating each opponent variant separately. The solver
picks our move after computing the opponent's action that turn, so evaluating
variants one at a time quietly lets us use a different answer to each -- not a
plan you could execute. find_plan PINS turn 1, then plays to completion against
every scripted opening plus the plain greedy 2v2.

`roll="min"` forces worst-case damage on every hit, so a plan is only reported
as GUARANTEED if it holds without a single favourable roll. `Battle.force_roll`
(`'min'`/`'avg'`/`'max'`) drives this.

### Committed plans -- older notes

`matchup_search.find_committed_plan(...)` answers the question that actually
matters: **is there a single turn-1 action that beats every opponent variant?**

This is not the same as beating each variant separately. The solver chooses our
move after computing what the opponent does that turn, so evaluating variants
one at a time silently lets us use a different answer for each -- which is not
a plan you could execute. At preview you commit to a lead, and on turn 1 to a
move, without knowing whether they will run their script, Fake Out the other
slot, or just attack with both.

find_committed_plan enumerates our legal turn-1 joint actions, FIXES each one,
and plays it against every variant. Only an action that wins them all counts.
From turn 2 the solver may adapt, since their line is observed by then.

The **unscripted greedy opponent is always evaluated as one of the variants**.
A scripted team is not obliged to run its script -- it can attack with both
slots, or Protect the setup threat and attack with the other. A composition
that beats the rehearsed line but loses the plain 2v2 is not an answer, so the
conventional game is always checked alongside.

Each script has multiple **opening variants** -- principally which of your two
slots it Fake Outs -- and every variant is played out, with the WORST result
for you reported. A forced-lead team has a tiny decision space, so it is
searched exhaustively rather than assuming it always targets slot 1. This
matters: a lead that looks clean against one targeting choice can collapse
against the other.

The script drives the opponent for as long as the plan lasts, then hands back
to the greedy model. A team is only safe into these opponents if it survives
the scripted line, not the greedy approximation.

### Quick Guard and Upper Hand

Both sit in the +3 priority bracket and answer a Fake Out lead. Quick Guard
blocks priority moves aimed at your side. Upper Hand blocks AND damages, but
only connects if the target is actually using a damaging priority move that
turn. Because Upper Hand is itself a priority move, it is stopped by Armor
Tail / Queenly Majesty / Dazzling on the other side -- Quick Guard is not.

Note that your own Fake Out cannot answer an Incineroar lead partnered with
Farigiraf, because Farigiraf's Armor Tail (99% usage) blocks your priority
outright. Quick Guard is the reliable answer there.

### Priority-blocking abilities

Queenly Majesty, Dazzling and Armor Tail block ALL incoming priority moves
aimed at their side -- held by either the target or its partner. This is the
standard answer to a Fake Out lead, and it is ignored by Mold Breaker,
Teravolt and Turboblaze.

### Weather effects

Sun/rain boost Fire/Water by 1.5x and halve the other. Sand gives Rock-types
1.5x Sp. Def; snow gives Ice-types 1.5x Defence. **Weather Ball** becomes the
weather's type at 100 BP. Weather-speed abilities (Chlorophyll, Swift Swim,
Sand Rush, Slush Rush) double Speed in their weather.

### -ate abilities

Pixilate / Aerilate / Refrigerate / Galvanize convert Normal moves to their
type and add 1.2x.

### Move selection guards

Moves under 80% accuracy are excluded unless the user has No Guard, or the move
is perfectly accurate in a weather the team itself sets (Hurricane/Thunder in
rain, Blizzard in snow). A recoil move that KOs is scored slightly below a
clean KO -- same result, less self-damage.

### Auras

Fairy Aura and Dark Aura boost moves of their type by 1.333x for **every**
Pokemon on the field, both sides -- they are field effects, not personal
boosts. Aura Break inverts them to 0.75x. Mega Floette carries Fairy Aura; its
BASE form has Flower Veil, which protects Grass allies from stat drops and is
not a damage boost at all, so the two are easy to confuse in a battle log.

### Abilities affecting damage

Type immunities (full, damage becomes 0): **Levitate** (Ground), **Flash Fire**
(Fire), **Water Absorb / Storm Drain / Dry Skin** (Water), **Volt Absorb /
Motor Drive / Lightning Rod** (Electric), **Sap Sipper** (Grass). These were
previously ignored entirely -- a Levitate user took full Earthquake damage,
which mattered because Hydreigon and Mega Delphox both run it.

Offensive: **Sheer Force** (+30% on moves with a secondary, and the secondary
is suppressed and Life Orb recoil cancelled), **Sharpness** (+50% to slicing
moves -- Gallade's Sacred Sword / Psycho Cut / Leaf Blade / Night Slash),
**Technician** (+50% at <=60 BP), **Tinted Lens** (x2 into resists),
**Tough Claws / Iron Fist / Strong Jaw**, **Adaptability**, **Guts**.

Defensive: **Thick Fat**, **Heatproof**, **Filter / Solid Rock / Prism Armor**,
**Multiscale**, **Fur Coat**, **Ice Scales**, **Rock Head** and **Magic Guard**
(recoil), **Defiant / Competitive / Clear Body** (stat drops), **Intimidate**.

### Perish Song and Shadow Tag

Perish Song sets a 3-turn counter on every Pokemon on the field that can hear
it (Soundproof is immune). Switching out CLEARS the counter -- which is exactly
why Shadow Tag is the other half of a Perish Trap: it denies the escape. Shadow Tag stops
non-Ghost Pokemon switching out; Ghost types, Shed Shell holders and other
Shadow Tag users escape, and switching MOVES (U-turn, Parting Shot) are
unaffected.

### Scripted opponent openings

Three teams do not play greedily -- they execute a rehearsed line a
damage-maximising AI would never find (double Protect, a 0-damage Perish Song,
spending a turn on Shell Smash). `src/scripted_openings.py` scripts them:

* **King** -- redirect or Fake Out to buy a turn while Mega Blastoise uses
  Shell Smash or Mega Delphox uses Nasty Plot, then sweep.
* **Hard Trick Room** -- T1 Fake Out + Trick Room, T2 Parting Shot into a
  sweeper under TR.
* **Perish Trap** -- T1 Fake Out + Perish Song, then Protect/Protect while
  Shadow Tag holds you in and the counter runs down.

### Fixed-lead teams are scored on their fixed lead

A team declaring a `Lead` in teams.csv is verified on that lead only (6 brings,
not 90) -- the other 84 are brings it never makes. Generation also now applies
the opponent's SCRIPT during verification. Both were missing before, which is
why King read 90/90 in the generate tab but 72/90 in the lead/back tab: the
generate tab was testing it unscripted. The two now agree.

### Punish check (minimax turn 1)

`committed_plan.worst_response(...)` plays our committed action against EVERY
legal opponent response, not just their greedy pick or their script. The point
is punishment: if we assume they make the obvious play we may Protect the slot
they were going to hit and eat a KO on the other. A play is only safe if no
response of theirs blows it up. `find_unpunishable_plan(...)` combines both
tests -- beats every scripted opening AND survives every response -- and is the
strongest (and slowest) guarantee here.

### Team generation checks the special (scripted) cases the same way as everything else

A scripted team (King / Hard Trick Room / Perish Trap) is verified on equal
footing with every other opponent: `search_robust_composition` plays its
rehearsed script -- AND the generic "one Protects, the other attacks"
alternative any fixed-lead team could run instead (both choices of which
active protects), plus the plain unscripted 2v2 -- against every one of our
bring-4/lead-2 combos, with OUR side adapting each turn via the real solver
(only the opponent is scripted; you genuinely do see their move before
choosing yours on every turn after the first). `wins == total` already means
the team survives every scripted line as well as the conventional 90 -- there
is no separate stricter "one blind turn-1 action must work" requirement on
top of that (see `committed_plan.find_plan_unknown_backs` below, which is
still available as a standalone, stronger analysis but is no longer part of
generate_team.py's default pass/fail check, per user request: it's a much
stricter bar than the game actually demands, since you're never really
choosing turn 1 blind past the opponent's lead reveal).

### Unknown backs (partially complete -- read before trusting)

At turn 1 you have seen the opponent's lead and nothing else, so a plan must not
depend on which two Pokemon are behind it.

DONE and verified: `heuristic_eval` previously summed the opponent's ENTIRE
roster, including a bench that had never been on the field, so the solver was
quietly scoring positions using information the player does not have.
Combatants now carry a `revealed` flag (set on switch-in; only the two leads
start revealed) and only revealed Pokemon are counted.

WRITTEN BUT UNVERIFIED: `find_plan_unknown_backs(...)` requires ONE turn-1
action to beat every back pair the lead could be hiding (all C(4,2)) crossed
with every scripted opening. It imports and is syntactically sound, but the
validation run timed out before producing a result -- treat it as untested.
Cost is 6 back pairs x 3 variants x every candidate turn-1 action, so it likely
needs a screening pass or parallelism before it is practical.

### Committed plans (`src/committed_plan.py`)

`find_plan(...)` answers the question that actually matters: **is there a
single turn-1 action that beats every way the opponent might open?**

This is not the same as beating each opponent variant separately. The solver
picks our move after computing the opponent's action that turn, so evaluating
variants one at a time quietly lets us use a different answer to each -- not a
plan you could execute. find_plan PINS turn 1, then plays to completion against
every scripted opening plus the plain greedy 2v2.

`roll="min"` forces worst-case damage on every hit, so a plan is only reported
as GUARANTEED if it holds without a single favourable roll. `Battle.force_roll`
(`'min'`/`'avg'`/`'max'`) drives this.

### Committed plans -- older notes

`matchup_search.find_committed_plan(...)` answers the question that actually
matters: **is there a single turn-1 action that beats every opponent variant?**

This is not the same as beating each variant separately. The solver chooses our
move after computing what the opponent does that turn, so evaluating variants
one at a time silently lets us use a different answer for each -- which is not
a plan you could execute. At preview you commit to a lead, and on turn 1 to a
move, without knowing whether they will run their script, Fake Out the other
slot, or just attack with both.

find_committed_plan enumerates our legal turn-1 joint actions, FIXES each one,
and plays it against every variant. Only an action that wins them all counts.
From turn 2 the solver may adapt, since their line is observed by then.

The **unscripted greedy opponent is always evaluated as one of the variants**.
A scripted team is not obliged to run its script -- it can attack with both
slots, or Protect the setup threat and attack with the other. A composition
that beats the rehearsed line but loses the plain 2v2 is not an answer, so the
conventional game is always checked alongside.

Each script has multiple **opening variants** -- principally which of your two
slots it Fake Outs -- and every variant is played out, with the WORST result
for you reported. A forced-lead team has a tiny decision space, so it is
searched exhaustively rather than assuming it always targets slot 1. This
matters: a lead that looks clean against one targeting choice can collapse
against the other.

The script drives the opponent for as long as the plan lasts, then hands back
to the greedy model. A team is only safe into these opponents if it survives
the scripted line, not the greedy approximation.

### Quick Guard and Upper Hand

Both sit in the +3 priority bracket and answer a Fake Out lead. Quick Guard
blocks priority moves aimed at your side. Upper Hand blocks AND damages, but
only connects if the target is actually using a damaging priority move that
turn. Because Upper Hand is itself a priority move, it is stopped by Armor
Tail / Queenly Majesty / Dazzling on the other side -- Quick Guard is not.

Note that your own Fake Out cannot answer an Incineroar lead partnered with
Farigiraf, because Farigiraf's Armor Tail (99% usage) blocks your priority
outright. Quick Guard is the reliable answer there.

### Priority-blocking abilities

Queenly Majesty, Dazzling and Armor Tail block ALL incoming priority moves
aimed at their side -- held by either the target or its partner. This is the
standard answer to a Fake Out lead, and it is ignored by Mold Breaker,
Teravolt and Turboblaze.

### Weather effects

Sun/rain boost Fire/Water by 1.5x and halve the other. Sand gives Rock-types
1.5x Sp. Def; snow gives Ice-types 1.5x Defence. **Weather Ball** becomes the
weather's type at 100 BP. Weather-speed abilities (Chlorophyll, Swift Swim,
Sand Rush, Slush Rush) double Speed in their weather.

### -ate abilities

Pixilate / Aerilate / Refrigerate / Galvanize convert Normal moves to their
type and add 1.2x.

### Move selection guards

Moves under 80% accuracy are excluded unless the user has No Guard, or the move
is perfectly accurate in a weather the team itself sets (Hurricane/Thunder in
rain, Blizzard in snow). A recoil move that KOs is scored slightly below a
clean KO -- same result, less self-damage.

### Auras

Fairy Aura and Dark Aura boost moves of their type by 1.333x for **every**
Pokemon on the field, both sides -- they are field effects, not personal
boosts. Aura Break inverts them to 0.75x. Mega Floette carries Fairy Aura; its
BASE form has Flower Veil, which protects Grass allies from stat drops and is
not a damage boost at all, so the two are easy to confuse in a battle log.

### Abilities affecting damage

Immunities: Levitate (Ground), Flash Fire (Fire), Water Absorb / Storm Drain /
Dry Skin (Water), Volt Absorb / Motor Drive / Lightning Rod (Electric),
Sap Sipper (Grass) -- these grant true 0x immunity, not a reduction.

Offensive: Sheer Force (+30%, secondary suppressed), Sharpness (+50% to slicing
moves), Technician, Tinted Lens, Iron Fist, Strong Jaw, Tough Claws,
Adaptability, Guts. Defensive: Thick Fat, Heatproof, Filter / Solid Rock /
Prism Armor, Multiscale, Fur Coat, Ice Scales. Recoil: Rock Head and Magic
Guard negate it.

### Also modeled

- **Recoil** (Flare Blitz/Wave Crash 33%, Head Smash 50%, ...) as a fraction
  of damage actually dealt; negated by Rock Head / Magic Guard.
- **Life Orb recoil** (10% max HP per connecting attack). Previously the
  1.3x damage boost was applied with no cost, systematically overrating
  Life Orb users.
- **Drain** (Giga Drain, Drain Punch, ...).
- **Follow Me / Rage Powder redirection**: single-target moves aimed at that
  side get pulled onto the redirector. Spread moves are unaffected;
  Stalwart / Propeller Tail ignore it; lasts one turn.

### Mega rules

- A team may bring TWO Mega-capable Pokemon (more options at preview), but
  **only one can Mega Evolve per battle** -- the other plays the whole game
  in base form.
- **Which one transforms is decided per matchup, not per team.** Both tools
  try each option and keep whichever wins that specific matchup, so the same
  team can Mega Gyarados into one opponent and Mega Charizard Y (bringing
  base Gyarados) into another. You never need to reorder your team list.
  The enemy's Mega choice is treated adversarially -- assumed to be whichever
  is worst for you.
- **Duplicate rows:** `Mega Floette` and `Mega Slowbro` each appear TWICE in
  `mbsmogon.xlsx` -- once as the real Mega (holding Floettite / Slowbronite,
  with the Mega ability) and once as that species' BASE form mislabeled with
  the Mega name (ordinary item, base ability). The loader keeps the
  stone-holding row as the Mega and files the other under the base species
  name, which is also where the pre-transform form's data comes from
  (`Floette` has no row of its own otherwise). Both CLIs print a note when
  this happens. Earlier versions silently let the second row overwrite the
  first, which lost the stone and used the wrong Nature/EVs/moves.
- **A Mega must hold its Mega Stone and cannot hold any other item.** The
  item optimiser is forbidden from assigning Megas anything else.
- A base-form Pokemon still holds its stone (that's why it was brought); it
  simply never gets to use it that battle.

### Stat-stage changes (modeled as of the latest version)

- **Self-inflicted drops** from `self.boosts` moves: Draco Meteor,
  Overheat, Leaf Storm (-2 SpA), Close Combat (-1 Def/SpD), etc. These
  apply once per use if the move hit, and correctly do NOT trigger
  Defiant/Competitive and are NOT blocked by Clear Body (self-inflicted,
  not from a foe).
- **Target drops from secondaries**: Snarl (-1 SpA), Icy Wind (-1 Spe),
  etc., including the Defiant (+2 Atk) / Competitive (+2 SpA) inversions
  and Clear Body / Full Metal Body / White Smoke immunity.
- **Pure stat status moves** (Swords Dance, Nasty Plot, Growl, ...) via
  the `boosts` field.
- All stages clamp to the standard -6..+6 range and feed directly into
  the damage and speed calculations.

### Protect: no double Protect

A protect-type move **fails outright if that same Pokemon successfully
protected on the immediately preceding turn**. (Real games use a decaying
1/3^n success chance; this format's rule -- and this tool's
deterministic-by-design stance -- uses the simpler "consecutive use always
fails".) Switching out resets the streak. This matters a lot: without it
the solver farms free turns by protecting forever, which badly distorts
which composition looks best.
- **Ability/item coverage is partial**: Intimidate, Defiant, Competitive,
  Guts, Adaptability, the 4 weather setters, the 4 weather speed-boosters,
  Choice Band/Specs/Scarf, Life Orb, Expert Belt, type-boosting held
  items. Anything else has no special effect beyond raw stats/typing.
- **Only the top 3 usage moves per Pokemon** are considered as candidate
  actions (`TOP_K_MOVES` in `src/solver.py`) -- raise it for a wider
  search at the cost of speed.
- Single-target move candidates are pruned to whichever target does the
  most immediate damage, not all possible targets.
- **Team generation uses a fast screener for the bulk of its search.**
  The pair matrix and beam search play both sides greedily with no
  lookahead; only the finalists are checked with the real solver. Treat
  screening coverage/margin as a ranking signal, not a result.
- **Beam search is not exhaustive.** It finds a strong team, not a proven
  optimum -- widen `--beam-width` and `--pool-size` for a better search.
- **Switch-rescue analysis is screener-based** and tests bringing a bench
  member in as a replacement; it does not exhaustively search every
  possible mid-battle pivot timing.
- `preferences.csv` semantics: **Include** entries are seeded into the beam
  search from the start so every candidate team contains them (filtering
  afterwards, as an earlier version did, silently dropped the constraint
  whenever the search never happened to build such a team -- an unsatisfiable
  include now warns instead). **Exclude** drops the Pokemon and its Mega/base
  counterpart. **Prefer** adds a small tiebreak bonus, bounded far below the
  per-matchup weight so it can never buy back a lost matchup.
- `preferences.csv` IS applied by `generate_team.py` (exclusions dropped,
  includes/prefers always kept in the candidate pool). `run_search.py`
  still expects you to pass `--pool` yourself.

## File structure

```
data/
  mbsmogon.xlsx     -- your Nature/EV/move/item/ability usage export
  roster.csv        -- Score (effective stat) + defensive type chart
  teams.csv         -- known enemy team rosters, plus two optional columns:
                        Lead  -- a FIXED opening pair ("Incineroar+Farigiraf") for
                                 teams whose whole plan depends on one lead. Search
                                 then tests 6 brings instead of 90.
                        Note  -- free text describing that team's gameplan and how
                                 to beat it; shown in the CLI and the app.
  preferences.csv   -- your include/exclude/prefer list (manual use for now)
src/
  species_data.py   -- merges your files with Showdown's base stat/move/
                        type/nature data; Mega base-form resolution helpers
  combatants.py       -- builds battle-ready Combatants, incl. Mega
                        base-form/mega-form dual construction
  stats.py             -- Lv50 stat calculator (Champions M-B flat-EV rule)
  damage.py            -- doubles damage formula
  engine.py             -- turn-order, switch-in triggers, Mega Evolution trigger
  battle.py             -- full multi-turn battle loop + structured event log
  solver.py             -- turn-by-turn move-choice search (expectimax vs
                        greedy opponent model)
  matchup_search.py    -- plays out full games, sweeps enemy lead pairs,
                        searches your bring-4/lead-2 combinations
  fast_eval.py          -- fast greedy playout used to screen tens of
                        thousands of matchups during team generation
  team_search.py        -- candidate filtering, pair matrix, synergy
                        scoring, beam search, switch-rescue analysis
  generate_team.py      -- CLI for team GENERATION (./run.sh generate)
  export_excel.py       -- results.xlsx builder
  export_html.py        -- results.html builder
  run_search.py          -- CLI entry point (what run.bat/run.sh call)
```

## Extending this

1. Auto-apply `preferences.csv` in `run_search.py` (currently manual)
2. Search the full ~270-mon pool for your best 6, not just arrange a
   pool you hand-pick
3. Exhaustive enemy back-2 search, and exhaustive search over *which*
   Mega transforms when a team brings 2 (currently always "first listed")
4. A second, more defensive opponent model (one that actually Protects)
   for worst-case rather than best-case lines
5. Deeper solver lookahead (`depth` param in `solve_best_action`) --
   currently 1 ply for speed
