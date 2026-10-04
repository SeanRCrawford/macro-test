# selfplay — design

A self-play reinforcement-learning agent for Gen 9 Champions VGC doubles,
modelled on mikumiku37 and Jaxcalibur. End uses: show a player the best leads
and moves in a position, and evaluate and construct teams.

This package is separate from `src/` (the analysis tools and Streamlit app).
It reuses `src/` only as a reference to test against.

## 1. What the public sources say

**mikumiku37** (Smogon thread 3789199, first post, by Shao):

- Small transformer, about 8.7M parameters, trained from scratch by self-play:
  PPO against a league of its own past versions. No human replays. The only
  reward is win or loss at the end of the game.
- Inputs: the battle from its own side plus static dex data (types, base
  stats, move data). No damage calculator, usage stats or speed resolver.
- Teams: about 1,260 public tournament teams from Reg M-B and M-C, with some
  spreads guessed. On ladder it rotated among the 10 teams that did best in
  training.
- Custom simulator, about 650x faster than Showdown.
- 48.3 hours on one RTX 5090. The checkpoint that reached #1 had played about
  330M training games, which is about 1,900 games per second.
- Search: one turn ahead, treated as a simultaneous-move game. For each of 16
  sampled worlds (guesses at the hidden information), it builds the payoff table
  of its top 8 joint actions against the opponent's top 8 and solves it for a
  mixed strategy. The value network scores the outcomes, so no game is played
  out to the end.
- Results: 1747 Elo with no search. With search it reached #1 at 1857 Elo,
  going 248–110.
- The engine and weights are not released. A technical writeup is promised.

**Jaxcalibur** (Smogon thread 3787537 and the writeup at jaxcalibur.github.io),
Gen 9 Random Battles, singles:

- **Model:** a non-causal transformer, 8.5M parameters, pre-RMSNorm. It uses a
  position-based mixture of experts: token positions of different kinds
  (Pokémon, moves, events) use different MLP weights. Smaller networks trained
  for longer beat larger ones trained for fewer steps.
- **Input tokens:** 1 battlefield (weather, hazards and so on), 2 active
  Pokémon (stats, moves, damage calcs), 5 candidate moves (Struggle gets its
  own), 6 own team, 6 opposing team (masked until revealed), 16 history
  tokens for the last 16 events (moves and switch-ins), and 24 matchup tokens
  with damage calcs for pairs of Pokémon. Each of its Pokémon is matched
  against 4 of the opponent's. The matchup tokens join the residual stream
  halfway through the network, so they can use fresh predictions of the
  opponent's moves, item and ability.
- **Outputs:** each team position emits a "switch to this slot" logit. Each
  move position emits two logits: use the move, or Terastallize and use it.
- **Training:** PPO with generalized advantage estimation. Undiscounted reward:
  1 for a win, 0 for a loss. Exploration comes from entropy regularization plus
  a "zero-avoiding" KL term toward the uniform policy, which keeps very
  situational moves such as Encore from reaching zero probability.
- **Auxiliary losses:** win probability (this is the value function); each
  opposing Pokémon's item, ability, moves and Tera type, with item and ability
  predictions carried across turns; and the opponent's next action. The author
  says predicting the opponent's next action made the bot substantially
  stronger.
- **No search in training.** About 100M self-play games over about a week on
  one H100.
- **Engine:** reimplemented in pure JAX, vectorized and run on the GPU. It
  executes every possible game step each turn and keeps only the relevant
  results, so every action has the same computational shape.
- **Search (test time only):** pUCT, with three changes.
  - Simultaneous moves: edges are sampled with probability proportional to
    max(Q(s,a) − V(s), 0) + c_puct·P(s,a)/√N(s). As visits grow, this shifts
    toward regret matching.
  - Randomness: every rollout is replayed from scratch with a new seed.
    Statistics are kept in a dictionary keyed by game-state hash rather than in
    an explicit tree, with HP binned into 10 buckets.
  - Hidden information: possible "worlds" for the opponent's unknown sets are
    sampled from the network's predictions. The opponent's search is cut off
    once Jaxcalibur does something "surprising" (switching to an unrevealed
    Pokémon, or a move the opponent's view gave under 65%). From that point the
    opponent uses only the network prior, so search can't leak hidden
    information to it.
  - Settings: depth 4 (usually 2 turns), 20,480 rollouts over 32 worlds, a few
    seconds on a GPU. The author estimates search adds 100–150 Elo.
- **Results:** peak 2557 Elo and 95.3 GXE. The network alone was about 2400 Elo
  on a laptop. Not released.

**Nessie123** (Smogon thread 3789213, first post, by nessie_dev), Reg M-C
Open Team Sheet Bo3 ladder, briefly #1 on 2026-09-28:

- **Goals:** a tool for understanding sound VGC play, built on a hobbyist
  budget.
- **Model:** a value network of about 1.5M parameters. Training was search-
  guided self-play: about 375k games, about 20 hours on an M4 Mac Mini plus
  $120 of cloud compute. It plays on one CPU core with no GPU.
- **Teams:** the open team sheets of every 6-2-or-better team from the
  Baltimore Regional, with some stat points imputed. The ladder pool had
  about 300 teams. It judged itself slightly behind at team preview on
  average (about 48–52).
- **Near-perfect information:** it trains on a variant of the game where
  stat points are public and, after team preview, the chosen leads and
  reserves are revealed to the opponent. A crude harness adapts it to the real
  ladder and doesn't make the best use of hidden information.
- **Search:**
  - **Root:** a double oracle seeded with the network's guess of each side's
    most likely move.
  - **Chance outcomes:** for each pair of joint actions, it branches on the
    chance events that matter (rolls that can KO, crits, secondary effects),
    enumerates the outcomes with their probabilities, evaluates the leaves,
    and takes the probability-weighted sum as that cell's value. It then
    solves the matrix game for both sides' equilibrium strategies.
  - **Deepening:** it expands and solves leaves of the root solve, ordered by
    roughly (chance of reaching the leaf)² × (entropy of the leaf's static
    evaluation). The motivation: the KL divergence between the static
    evaluation and the depth-1 search value was observed to be roughly
    proportional to the static evaluation's entropy.
  - It also checks some off-equilibrium moves whose regret is low, and plays
    by sampling the equilibrium. It makes no attempt at exploitative play.
  - In many endgames (2v2 or fewer) it effectively solves the game, agreeing
    within about 0.5% with a strict solver that enumerates every damage roll.
- **Auxiliary heads:** future weather and terrain, who deals how much damage
  to whom, each player's next moves, how long each Pokémon survives, game
  length, and how much entropy collapses next turn.
- **Diversity:** half of its training games use teams whose stats, abilities,
  typing, moves or items have been mutated. The author credits search-labelled
  data, the auxiliary heads and the mutated teams for its sample efficiency.
  The trade-off is that search-guided games are far more expensive to produce.
- **Analysis board:** like lichess, with an evaluation bar. You pick each
  side's moves and the random outcomes, then analyse the resulting position.
  Release is planned around the end of Reg M-C.

**How the three bots differ:**

- **Damage calcs as inputs:** Jaxcalibur gives its network damage calcs
  (matchup tokens). mikumiku37 says its network has no damage calculator,
  speed resolver or usage stats.
- **Training data:** Jaxcalibur and mikumiku37 train a policy cheaply with PPO
  on hundreds of millions of games, with no search during training. Nessie
  trains a small value network on a few hundred thousand search-labelled
  games.
- **Search:** Nessie enumerates chance outcomes exactly and solves matrix
  games at depth. mikumiku37 samples worlds and solves one turn. Jaxcalibur
  samples rollouts.

## 2. Architecture

| Layer | Language | Responsibility |
|---|---|---|
| `engine/` | Rust | Rules, battle state, turn resolution, RNG. Knows nothing about neural nets. |
| `pybind/` | Rust (PyO3) | Thin bindings: `selfplay._engine`. Later, batched environments and observation encoding written straight into numpy buffers. |
| `python/selfplay/` | Python | Training (PPO and the league), the model, search, and a Showdown client for playing on the ladder. |

Why this split: the simulator decides whether training is feasible at all, so
it has to be compiled code with no Python in the inner loop. Keeping it free of
PyO3 means `cargo test` and benchmarks run without Python.

**CPU engine, not GPU.** Jaxcalibur ran its engine on the GPU in JAX. We run
ours on the CPU in Rust, for two reasons:

- JAX has no native CUDA support on Windows; it would need WSL2.
- A JAX engine has to execute every branch of every turn. That gets
  expensive with doubles' larger action space and long tail of mechanics.

mikumiku37's rate of about 1,900 games per second is about 20k turns per
second. A Rust engine at a few microseconds per turn reaches that on one or two
cores. The design that follows from this: many environments step in parallel
on CPU threads, and their observations are batched into one GPU forward pass.

## 3. Phase plan

Each phase has an exit test that has to pass before the next phase starts.

1. **Engine.** Exit: plays complete games between any two teams in
   `data/teams/`, with no panics over millions of random-action games. Agrees
   with `src/damage.py` and `src/battle.py` wherever both model a mechanic.
   Meets the speed target in section 4.7.
2. **Environment.** Observation encoding (what each side can see), action
   masks, batched environments. Exit: a random-play rollout of 1M games
   through the Python API, with a measured throughput.
3. **Learning.** Model plus PPO with a league of past versions. Exit: a tiny
   model trained on CPU clearly beats random play and a greedy-damage policy.
   Then scale up on your GPU.
4. **Search.** The one-turn simultaneous-move solve over sampled worlds. It
   reuses the solver in `src/matrix_game.py`, ported to Rust if it turns out to
   be a bottleneck. Exit: search beats the bare network in head-to-head games.
5. **Product.** Lead and move advice in the app, then team construction using
   the value network.

## 4. Phase 1: the engine

### 4.1 Format

**Reg M-C.** Doubles, bring 6 and pick 4, level 50, Mega Evolution (one per
side per battle), Champions stat points (not EVs), Open Team Sheets on the
Showdown ladder. Stats follow Showdown's formula (section 4.8). Species and
item clauses are checked
when a team is built. Training games are capped at 30 turns
(`Battle::turn_limit`) and count as a draw (reward 0) if they reach it.
Showdown has no such cap, but it stops games that would otherwise never end.

### 4.2 Decision points

Both sides choose simultaneously, the same way Showdown's request model works:

- **Team preview:** pick 4 of 6, in order. The first two lead. That gives
  6×5 lead orders × 6 bench pairs = 180 choices.
- **Move request:** for each active slot, either a move (1–4) with a target and
  an optional Mega flag, or a switch to a bench Pokémon. Joint constraints: the
  two slots can't switch to the same Pokémon, and only one Mega per battle.
- **Forced switch:** after a faint, each side that has a fainted active and a
  Pokémon left on the bench chooses a replacement. Sometimes only one side has
  to choose.
- **Mid-turn switch:** U-turn, Volt Switch, Parting Shot, Eject Button, Eject
  Pack and Emergency Exit ask one player to choose in the middle of a turn.

The mid-turn case drives a structural decision: **the turn's remaining action
queue lives in the battle state, not on the Rust call stack.** Resolution runs
until a choice is needed, then returns. The API is `request()` (who must
choose, and what kind of choice) plus `choose(p1, p2)`. Search and training
treat every decision point the same way.

### 4.3 State

- `Battle` is `Copy`, fixed-size, with no heap allocation. Search copies it
  many times per decision. Target size is under 1 KB.
- `Battle`: two `Side`s, the `Field`, the pending action queue, the turn
  counter, the RNG state, and the pending request.
- `Side`: the 4 brought Pokémon, which are in the active slots, side conditions
  (Tailwind, screens, Wide Guard and Quick Guard), and whether Mega Evolution
  has been used.
- `Pokemon`: an index into a per-battle team table (species, stats, moves,
  original ability and item). Plus its changing state: HP, status and its
  counters, stat boosts, volatiles as bit flags (Protect, flinch, Helping Hand,
  redirection and so on), the Protect counter, choice lock, current item,
  ability, species and types (Mega Evolution changes them), PP, the last move
  used, and turns on the field (for Fake Out).
- Static data (`Dex`) is a process-wide table indexed by `u16` ids. Strings
  only appear at the API boundary.

### 4.4 Randomness

- One PRNG, seeded per battle and stored in the state. Every random event goes
  through it: damage roll (16 values), critical hits, accuracy, secondary
  effect chances, speed ties, multi-hit counts, sleep length, full paralysis.
- Search has to reseed each copied state per sampled world. Otherwise every
  copy rolls the same "random" outcomes.
- A fixed-roll deterministic mode exists for differential tests against
  `src/battle.py`, whose default mode is deterministic.
- Matching Showdown's exact random-number sequence is not a goal. Its
  distributions are the target.
- **Chance outcomes can be enumerated as well as sampled.** Each random event
  in a turn goes through one chance interface. A turn can run in three ways:
  sampled (training), with every outcome forced (tests, and an analysis board
  that lets the user pick outcomes), or enumerated, returning each distinct
  outcome with its probability. This is what Nessie's search needs, and what
  the lead-and-move advice tool needs to show "X% to win if you do this".
- **As built** (`chance.rs`, `enumerate.rs`): a `Scripted` chance forces the
  class of outcome taken at each draw and records every class's
  probability; `enumerate` replays the turn best-first over the classes not
  taken, up to a cap, and reports the probability left out.
  - Draws are split only where the outcome plays differently: a damage roll
    into "KOs" and "doesn't" (optionally more bands, each represented by its
    middle roll), a secondary's `random(100)` into "triggers" and
    "doesn't", multi-hit counts into 2/3/4/5, Effect Spore into its four
    results.
  - Approximation: ties between event handlers (residuals, switch-in
    abilities of equally fast Pokemon) keep their collected order when
    enumerating, instead of doubling the outcomes for each tied sort.
    Speed ties in the action queue are enumerated.
  - Sampled and policy modes draw exactly as before, so the Showdown
    fixtures are unchanged. With 16 bands, enumerated expectations match
    sampling (`tests/enumerate.rs`).
  - Size, for a random joint action in mid-game corpus positions: median 6
    outcomes per cell, 1 in 10 over 54, at most 968.

### 4.5 Hidden information

The engine itself sees everything. A per-side observation function hides what
that player can't see.

The Reg M-C ladder uses Open Team Sheets, which reveal species, moves, items
and abilities, but not stat points. (Champions has no Terastallization.) What stays hidden is the
opponent's stat points and which 4 of the 6 they brought (and which 2 lead,
until the battle starts). That is much less than in Random Battles.

Following Nessie, the first training variant can make stat points and the
brought 4 public as well, so the game has nearly perfect information. The
observation function then handles the real ladder: sampled worlds fill in
stat points and the brought 4 from a prior (usage stats now, the team corpus
later) conditioned on what has been revealed.

### 4.6 Which mechanics, in what order

Coverage follows actual use. A coverage tool lists every move, item and
ability in the team corpus with its usage count and whether it's implemented,
and work goes in usage order. Building a team that uses something unimplemented
is an error unless it is explicitly allowed. That way a missing mechanic is
reported instead of silently treated as neutral.

The core set needed before any training:

- The damage formula with the standard modifiers: spread reduction, weather,
  STAB, type effectiveness, critical hits, burn, screens, and the damage items
  and abilities used by the pool.
- Speed order: priority, Trick Room, Tailwind, paralysis, Choice Scarf.
- Protect and its variants, Fake Out, Follow Me / Rage Powder, Helping Hand,
  Intimidate, weather and terrain setters.
- The major statuses, stat boosts, switching, Mega Evolution, choice lock,
  Focus Sash, Sitrus Berry.
- Fainting and replacement, and the end-of-turn order.

### 4.7 Speed

mikumiku37 averaged about 1,900 games per second including network inference.
The engine should be a small fraction of that cost. The phase 1 gate is
**at least 100k random-action turns per second on one core** in a release
build, measured by a benchmark binary that ships with the engine. Showdown's
own speed should be measured on the same machine for comparison.

### 4.8 Validation

**Showdown's source is the rules authority.** Its current `champions` mod
defines Reg M-C, including the Champions changes: paralysis fully paralyses 1
time in 8, sleep lasts 2–3 turns, freeze thaws within 3 turns, Unseen Fist and
Piercing Drill do 1/4 damage through Protect, Aura Guard halves contact damage,
and moves have new PP and handlers.
`src/` predates this and is not used as a reference. For example, its stat
formula adds stat points after the nature; Showdown adds them before.

1. Rust unit tests for each mechanic.
2. **Fixtures computed by Showdown's own code** (`tools/showdown/gen_fixtures.js`),
   committed under `engine/tests/fixtures/`, so `cargo test` needs no Node.
   Done so far:
   - Stats for every pool species and spread: 10,175 cases, exact match.
   - Damage for all 16 rolls: 4,000 random doubles situations built from
     usage stats, with random weather, terrain, screens, boosts, statuses,
     HP, Helping Hand, crits and spread hits. 3,973 match exactly, none
     mismatch, and 27 are reported unsupported (see 4.10).

   Next: whole turns, with the same seeded choices played through both
   simulators and the results compared.
3. Self-play smoke tests: millions of random-action games with invariant
   checks (HP bounds, legal requests, termination).

### 4.9 Phase 1 milestones (each about one commit)

- [x] 1a. Workspace scaffold, embedded dex, stat calculation.
- [x] 1a′. Dex exported from Showdown's champions mod; stats match Showdown exactly.
- [x] 1b. The damage formula. Exact match with Showdown fixtures over the pool.
- [x] 1c. Team construction from Showdown pastes, team validation, team preview.
  `engine/src/team.rs`. The parser mirrors Showdown's importer (plus the repo's
  `Nature: X` lines, which Showdown ignores) and fills missing stat points from
  the most common usage spread. The validator mirrors Showdown's Reg M-C
  validator, including its Mega shorthands, plus the usage-stats item rule.
  Tests: the 17 repo teams parse field for field like Showdown, and 600
  generated legal and rule-breaking teams get Showdown's verdict. Legality and
  learnsets come from Showdown (`checkCanLearn`) via the dex export.
- [x] 1d. Turn resolution core: ordering, moves, damage, faints, forced switches, end of turn. Random-play smoke test with invariant checks.
  `engine/src/battle/` is a port of Showdown's battle loop: queued actions
  sorted by order, priority and speed (`speedSort` with tie shuffles),
  instaswitch, gen-8+ re-sorting after every action, the remaining queue kept
  in the state. Plain attacks only so far (`support.rs` refuses anything
  else). Checked by replaying 300 Showdown battles (3,941 decisions) under
  three chance policies: identical state, legal-choice counts and results at
  every decision.
- [ ] 1e. Mechanics breadth, driven by the coverage report.
  Batch 1 done (`battle/conditions.rs`, `battle/moves.rs`): the hit steps
  (Protect's TryHit, type and powder immunity, accuracy), `spreadMoveHit` /
  `runMoveEffects` with Showdown's `combineResults`, secondaries and self
  drops, stat stages (`battle.boost`), statuses with the champions changes
  (1/8 full paralysis, sleep 2-3 turns, freeze thaws within 3), their
  BeforeMove and Residual handlers in Showdown's handler order, flinch and
  Fake Out, Protect/Detect with the stall counter, recoil, drain and healing
  moves. Checked on 1,500 replayed battles.
  Batch 2: Focus Sash, Sitrus Berry (Showdown's Update events), Life Orb,
  Leftovers, Choice Scarf and its lock (`battle/items.rs`); Tailwind and
  Trick Room; Intimidate and the weather/terrain-setting abilities with
  weather and terrain residuals and their status/priority/accuracy effects
  (`battle/field.rs`); Follow Me, Rage Powder, Helping Hand; U-turn, Volt
  Switch, Flip Turn and Parting Shot. 429 moves supported; by Reg M-C usage,
  83% of move slots, 78% of items and 49% of abilities. Checked on 3,000
  replayed battles (47,466 decisions; 300 committed).
  Batch 3: Dire Claw, Sucker Punch, Grassy Glide, Encore, Throat Chop, Low
  Kick/Grass Knot, Reflect/Light Screen (Light Clay), Wide Guard; Unburden,
  Defiant, Competitive, Prankster, Good as Gold, Armor Tail; terrain seeds,
  type-resist berries, Rocky Helmet, White Herb. 439 moves supported; by Reg
  M-C usage, 91% of move slots, 99% of items and 72% of abilities. Checked on
  3,000 replayed battles (46,972 decisions; 300 committed).
  Later batches brought the long tail in: 650 moves, 201 abilities and every
  pool item are supported. By Reg M-C usage, 0.28% of team slots still hold
  an unsupported move or ability: Dragon Darts, Gravity, Octolock, Cud Chew,
  Round, Pollen Puff, Smack Down, Mirror Coat and smaller ones. Each check
  replays 6,000 battles, plus 900 led by the new feature's users
  (`FORCE=ids` in `gen_fixtures.js`).
  Scope from here: add a move or ability only when a team paste we're
  given uses it, or to fix a deviation from Showdown. Unsupported features
  can't produce wrong results: the generator leaves them out and the engine
  refuses sets that use them.
- [x] 1f. Turn-level differential tests against Showdown: random battles
  between supported teams, Showdown's PRNG replaced by the threshold policy
  `Chance::Policy` mirrors (`tools/showdown/gen_fixtures.js`,
  `engine/tests/battle_fixtures.rs`). Grows with each mechanic: a mechanic
  joins `support.rs`, `data/support.json` is regenerated, the fixture
  teams start using it, and the replay must still match.
- [ ] 1g. Benchmark and speed gate. `cargo run --release --example bench`
  plays random legal games between weighted corpus teams with the 30-turn
  cap. After a profiling pass (callgrind) the engine does about 26,700 turns/s
  on one core (from 9,500). The 100k gate isn't met. Most of the remaining cost
  is string-keyed ability and item checks throughout the engine, and the
  damage module's event collection. Interning those as integer ids is the
  next step if speed becomes the bottleneck. Games run in parallel, so it
  doesn't block training.
- [ ] 1h. Driving a real Showdown battle (protocol client) from the engine's choices.

### 4.10 How the damage code mirrors Showdown

`engine/src/damage.rs` follows `getDamage` and the champions mod's
`modifyDamage` step by step, including the 4096-based fixed-point rounding
(`engine/src/fixed.rs`).

Modifiers come from event handlers, collected the way Showdown's `runEvent`
collects them: the attacker, the defender, both allies, screens, weather,
terrain and the move itself. They are sorted by Showdown's keys (order,
priority, holder speed, effect kind) and applied in that order, because
chained modifiers round at every step. Handler priorities come from the
dex. The fixture generator avoids exact speed ties, which Showdown breaks at
random.

The dex lists every handler each effect has, so an effect whose handler
isn't implemented returns `Unsupported` rather than a wrong number. The
current unsupported cases need battle history the damage function doesn't
have: whether the user's last move failed (Stomping Tantrum, Temper Flare),
whether the target has moved (Payback, Round), stats lowered this turn (Lash
Out), modified Speed (Gyro Ball). Also unsupported so far: Stance Change,
Disguise and OHKO moves. The turn engine (1d) supplies that context.

### 4.11 Phase 2: the environment

`engine/src/env/` (Rust) and `python/selfplay/env.py`:

- **Teams** come from `data/corpus` (462 tournament and ladder pastes;
  `engine::corpus`). Each game draws both teams by weight: Champion 8, Runner
  Up 6, Top 4 5, Top 8 4, Top 16 3, Top 32 2, deeper 1.5, unplaced 1. So about
  two thirds of games use a team that placed.
- **Actions** (`env/action.rs`): team preview is one of 180 ordered picks.
  A move or switch request is a joint action `slot0 * 47 + slot1` over 47
  per-slot actions: 4 moves x 5 targets x Mega, switch to a party position
  (also Revival Blessing's pick), and pass. A mask marks the legal ones;
  every legal choice has its own index.
- **Observations** (`env/obs.rs`), per side: 12 Pokemon tokens (own six,
  then the opponent's six, in team order) with 9 dex ids each for
  embeddings and 90 features, plus 55 field features. Open Team Sheets
  hide the opponent's stats and which four it brought until each appears;
  `perfect_info` shows them (the first training variant).
- **Batching** (`env::VecEnv`, `selfplay._engine.VecEnv`): N self-play games
  stepped together on all cores, written straight into numpy arrays. A
  finished game reports +1/-1/0 per side and is replaced.
- **Exit test** (`python -m selfplay.rollout --games 1000000`): see 4.12.

### 4.12 Phase 2 exit test

1,000,008 random-play games through the Python API (1,024 environments,
4 cores, 30-turn cap), with no errors:

- 268 s total: 3,732 games/s, 37,540 turns/s and 97,250 decisions/s. A
  test suite ran alongside for part of it; unshared, the rate is about
  4,500-4,700 games/s.
- Games average 10.1 turns. Side 0 wins 49.8%, and 0.35% reach the cap.

### 4.13 Phase 3: first training runs (CPU)

`python/selfplay/model.py`, `train.py`, `evaluate.py`; baselines in
`engine/src/env/policy.rs`.

- **Baselines.** Random play. Greedy damage: each slot uses its
  highest-expected-damage move and target (`Battle::estimate_damage`),
  Mega Evolves when it can, and switches only when forced. Greedy beats
  random 97% of the time.
- **Model.** About 0.2M parameters: a 2-layer, d=64 transformer over the
  field and 12 Pokemon tokens. Team preview scores each Pokemon as a lead or a
  bring, so it carries across teams. With `perfect_info`, each active
  Pokemon's token also gets the expected damage of each move at each target
  (the damage calcs of 5). Runs 1-5 below scored a joint action as the sum
  of its two slot logits; that was replaced by the joint head (4.14).
- **PPO.** GAE (gamma 1, lambda 0.95) along each side's own decisions. Half
  the games are self-play. In the other half, side 1 is a league member:
  past snapshots (one every 10 updates, the last 8 kept) and the greedy
  baseline. Optional warm start: `--imitate-minutes` clones greedy first.

Results on this container's 4 CPU cores (2,000-game evaluations, model on
side 0 playing deterministically, 95% intervals):

| Run | Setup | vs random | vs greedy |
|---|---|---|---|
| 1 | 30 min, no damage features | 0.96 (400 games) | 0.45 (400 games) |
| 2 | 30 min, damage features | 0.97 (400 games) | 0.51-0.55 (400 games) |
| 4 | 3 min imitation, then 27 min with 4x the gradient steps and 25% of games vs greedy | 0.986 ± 0.005 | 0.583 ± 0.022 |
| 5 | run 4 plus 60 min | 0.979 ± 0.006 | 0.576 ± 0.022 |

Exit test: the tiny CPU model beats random play clearly and greedy damage
significantly (about 58%). The margin over greedy stops growing at this
scale; runs 4 and 5 sit at about 56-62% in the evaluations along the way.
Next: scale up on the GPU (`--device cuda`, larger `--d`/`--layers`, more
environments), then the one-turn search (phase 4).

### 4.14 Joint actions, and the plan for search

**Why joint actions.** Doubles is a simultaneous-move game: the value of a
position is the value of the matrix game between both sides' joint actions
(with chance inside each cell), and good play is that game's equilibrium
mix, not a best reply to one guess at the opponent. A summed per-slot logit
is a product distribution: it can't put half on "Protect + Tailwind" and half
on "attack + Moonblast" without a quarter on each crossed pair, so the
network could neither represent the mixes search finds nor learn from them.
Jaxcalibur gives every action its own logit; in doubles that means every
joint action.

**The joint head** (`model.py`). Each slot's 47 actions are embedded: a
move from its move embedding, PP, disabled flag, the expected damage at that
target, the target's token (none, a foe's active, an ally) and the Mega
flag; a switch from the token of the Pokemon it brings in (a party-position
one-hot was added to the observation for this); pass as a learned vector.
Each is added to the slot's own token and the field token. A pair scores
u0(a) + u1(b) + <P e0(a), Q e1(b)> / sqrt(d), masked to the legal pairs
and normalised as one softmax over 2,209. A test checks that it learns a
50/50 mix of two pairs with under 5% on the crossed pairs.

**Opponent prediction.** The same scoring over the opponent's actives
(shared action embeddings, its own u, P, Q) predicts the opponent's actual
joint action, as an auxiliary cross-entropy (`--opponent-coef`, 0.5).
Jaxcalibur credits this head with a large gain. The search uses the policy
run on the opponent's own view; this head is what is left to use under
hidden information. PPO also has the zero-avoiding KL(uniform || policy)
term (`--uniform-kl`).

**What to measure.** Beating the greedy baseline rewards exploiting a fixed,
weak opponent. The measures that matter for equilibrium play: head-to-head
against past versions, how well an exploiter trained against a frozen
version does (exploitability), the solver gap of the one-turn matrix, and
in the end the ladder.

**Hardware and route.** Target machine: RTX 4070 Laptop GPU (8 GB) and
16 CPU cores. Estimates, from measurements on this engine (a state copy
2.6 us; copy plus one turn 33 us, about 30,000 per core per second;
86 legal joint actions per side on average mid-game, 1 in 10 positions
over 166):

| | Nessie-size | mikumiku37-size |
|---|---|---|
| Network | ~1.5M | ~8.7M |
| Games | ~375k, every turn searched | ~330M, no search in training |
| Engine | ~3M searched turns x ~2,000 leaves (a guess: Nessie's budget isn't published) = ~6e9 one-turn simulations = ~55 core-hours, ~4-6 h on 16 cores | ~20k turns/s, within the engine's speed |
| Network | ~5e17 FLOP: ~10 h on the 4070 Laptop | ~2e19 FLOP: ~2 days on an RTX 5090, about 2 weeks on the 4070 Laptop |

So the Nessie route fits this machine (about a day per run, if leaf
evaluations are batched across many games onto the GPU), and it produces
what the analysis-board goal needs: equilibrium supports, payoff matrices
and chance outcomes. PPO stays as a cheap warm start for the policy prior.

Order of work:

1. Joint head and opponent head (done).
2. Chance enumeration in the engine (done, 4.4).
3. One-turn matrix search (done): `engine/src/search.rs` and
   `python/selfplay/search.py`. Both sides' top-k joint actions come from
   the policy, each on its own view. Every pair is played through its chance
   outcomes (up to 16, the rest renormalised away), the value network scores
   the leaves (the mean of side 0's estimate and minus side 1's), and
   regret matching+ solves the k x k matrix. Leaves from all searched games
   are evaluated in large batches.
4. Search-labelled self-play (`python/selfplay/search_train.py`): both
   sides play the search's mix; targets are each side's mix (policy), the
   opponent's mix (opponent head) and half search value, half game result
   (value). At Nessie's size on the GPU machine.
5. Double oracle at the root and selective deepening (built; see below),
   then the analysis board. `engine::search::Tree` holds positions (roots
   copied from games, leaves an expansion keeps) and expands any (node,
   action, action) cell, so Python can search from any node.
   - Double oracle: start from each side's top 2 actions; scan each side's
     legal actions (or its top `--scan`) against the opponent's current
     mix; add up to 2 replies per side that gain more than 0.005; solve
     again; stop when neither side gains. Each scanned action's loss
     against the final mix is reported (the analysis board's "nearby
     alternatives"). A test checks it reaches the full matrix's value.
   - Selective deepening: re-expand the root's support cells keeping their
     leaves, rank leaves by (reach probability)^2 x entropy of the static
     evaluation, give the top `--deepen` a one-turn search of their own
     (top 4 x 4), replace their values and solve the root again.

Results so far, on this container's 4 CPU cores:

- PPO with the joint head (run 6: 3 min imitation, then 30 min): 0.982 ±
  0.006 against random, 0.588 ± 0.022 against greedy, after 25 updates;
  runs 4-5 needed 77-157 updates for the same.
- One-turn search (k = 8) with run 6's network against the same network's
  raw policy: 0.695 ± 0.045 (410 games). About 590 leaves and 100 ms per
  searched turn, solver gap under 1e-4.
- Search-labelled training from run 6 (run 7: 60 min, 3,880 games, 80k
  examples, only about 300 gradient steps because play dominates on a
  CPU): the new raw policy beats run 6's raw policy 0.525 ± 0.022 (2,000
  games), and scores 0.972 against random and 0.580 against greedy. The
  search still beats the policy it guides (0.71-0.79 in 100-game checks),
  so there is a lot left to distil. This is the start of the curve, not a
  result: Nessie used about 100 times as many games.

- Step 5 with run 7's network, against plain one-turn search (top 8):
  double oracle 0.545 ± 0.067 (212 games; about 760 cells, 8,350 leaves and
  1.6 s per turn, 14 times the cost); one-turn plus deepening of 16 leaves
  (about 10 qualify per turn) 0.468 ± 0.069 (200 games, 2.3 times the
  cost). Neither is a clear gain yet. Both spend more evaluations of the
  same value network, which is the weak part at this size; they are worth
  measuring again with a GPU-trained network. Deepening also mixes depths
  within a cell (a deepened leaf's value comes from a restricted 4 x 4
  game), which can bias it.

Rough cost on the target machine: at about 600 leaves per searched turn and
games of about 10 turns, 375k games is about 2 billion leaf evaluations.
With the network on the GPU and the engine on 16 cores, that's roughly a
day; it's worth measuring the first hour before committing to it.

## 5. Model and training (provisional; settled in phases 2–3)

Two recipes have reached #1 in Reg M-C:

- **mikumiku37:** a large policy trained by cheap PPO on a GPU, plus a light
  one-turn search.
- **Nessie:** a small value network trained on expensive search-labelled
  games, plus deep matrix-game search with exact chance enumeration.

The engine supports both: fast sampled turns, plus enumerated chance outcomes
(4.4). The end goal of showing a player the best leads and moves suits
Nessie's style, because it gives equilibrium strategies and win
probabilities you can inspect. The policy network from the PPO recipe is
still useful there as the move-ordering prior and the double oracle's seed.
The plan is to build the PPO recipe first, because it's cheaper to iterate
on with your GPU. Then add Nessie-style search on top, and use search-
labelled games to fine-tune the value network if the PPO value plateaus.

Starting from Jaxcalibur's recipe, adapted to doubles:

- **Framework:** PyTorch, which supports CUDA natively on Windows.
- **Network:** a non-causal pre-RMSNorm transformer of about 8–9M parameters,
  with position-based mixture of experts (MLP weights per token kind).
- **Tokens:** field; 4 active (2 ours, 2 theirs); our active Pokémon's moves;
  our 4 brought Pokémon; the opponent's 6 from team preview, masked until
  revealed; and recent events.
- **Damage calcs as inputs:** Jaxcalibur used them as matchup tokens and
  mikumiku37 did without. Start without them, as mikumiku37 did, and measure
  adding them as an experiment.
- **Action heads:** per active slot, a logit for each (move, target, Mega)
  combination and each switch, then joint legality masking. Team preview
  gets its own head. Whether the two slots' joint action is factored
  autoregressively (slot 1, then slot 2 given slot 1) or scored jointly is an
  open choice. The one-turn search needs the top-k joint actions either way.
- **RL:** PPO with GAE, undiscounted reward of 1 for a win and 0 for a loss, an
  entropy bonus plus a zero-avoiding KL-to-uniform term, and a league of past
  versions (as mikumiku37 did).
- **Auxiliary heads:** win probability (the value); the opponent's stat
  points and brought 4 (the only hidden information under Open Team
  Sheets); and the opponent's next joint action. Nessie's further heads are
  cheap to add: damage dealt between each pair of Pokémon, how long each
  survives, future weather and terrain, game length.
- **Team diversity:** following Nessie, train half the games on mutated
  teams (perturbed stats, moves, items, abilities) so the network doesn't
  overfit the corpus. This needs the engine to accept arbitrary legal-shaped
  sets, which the data-driven dex already allows.
- **Search:** mikumiku37's one-turn matrix solve first, because it suits
  doubles' large joint action space. Then Nessie's double oracle with chance
  enumeration and selective deepening. `src/matrix_game.py` already has a
  double oracle, which can be ported to Rust if it becomes a bottleneck.
  Jaxcalibur's regret-pUCT is a later option.

## 6. Decisions and open questions

Decided:

- Rust engine with PyO3 bindings, as a top-level `selfplay/` package.
- Training on your Windows GPU machine, with PyTorch.
- Target regulation is Reg M-C.
- Showdown's Champions format uses stat points, not EVs.
- **Reg M-C legal pool:** `data/regmc_pool.json`, built by
  `tools/gen_pool.py` from Smogon's September 2026 usage stats
  (`data/smogon_stats/`, 1760+ rating cut-off). It has 341 species, 154
  items, 391 moves and 203 abilities, plus each species' common spreads,
  moves, items and teammates.
  - Species: every species in the leads file, plus Zoroark and
    Zoroark-Hisui. Illusion means they never show up as leads.
  - Items: an item is legal only if it appears somewhere in the stats. Any
    legal item can go on any species; a Mega Stone on the wrong species does
    nothing.
  - Moves and abilities: these lists are the ones in use. Rarer ones are
    folded into "Other" in the stats, so a move outside the list isn't
    necessarily illegal.
  - The stats name Megas by forme ("Salamence-Mega"). In a team that means the
    base species holding its stone.
  - This pool sets the order in which mechanics get implemented (section 4.6).
    The usage stats also give the engine its first set prior for sampled
    worlds until the team corpus arrives.

Open:

1. **Team corpus.** Supplied: `data/corpus`, 462 pastes. 460 are legal, and
   all of those are supported. More pastes can go in the folder as they come.
2. **`src/` disagrees with Showdown on stats.** `src/stats.py` adds stat
   points after the nature, so a boosted stat with points in it reads up to 3
   too low. The existing app and CLI inherit this. Not changed here.
