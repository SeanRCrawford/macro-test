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

**Jaxcalibur** (Smogon thread 3787537, first post), Gen 9 Random Battles:

- Self-play RL plus AlphaGo Zero-style pUCT search, built on poke-env and Jax.
  The search goes 4 plies deep with 20,480 rollouts across 32 sampled worlds.
- Peaked at 2557 Elo. The network alone was about 2400 Elo on a laptop.
- More than six months of work on an H100. Not released.

**Not yet read:** the Jaxcalibur technical writeup at jaxcalibur.github.io.
This environment's network policy blocks it. mikumiku37 says part of its design
comes from that writeup, so the model and training sections below are
provisional until it has been read.

## 2. Architecture

| Layer | Language | Responsibility |
|---|---|---|
| `engine/` | Rust | Rules, battle state, turn resolution, RNG. Knows nothing about neural nets. |
| `pybind/` | Rust (PyO3) | Thin bindings: `selfplay._engine`. Later, batched environments and observation encoding written straight into numpy buffers. |
| `python/selfplay/` | Python | Training (PPO and the league), the model, search, and a Showdown client for playing on the ladder. |

Why this split: the simulator decides whether training is feasible at all, so
it has to be compiled code with no Python in the inner loop. Keeping it free of
PyO3 means `cargo test` and benchmarks run without Python.

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

Doubles, bring 6 and pick 4, level 50, Mega Evolution (one per side per
battle), Champions stat points. The stat rule follows `src/stats.py` and is
covered by a parity test (section 4.8). Species and item clauses are checked
when a team is built. Training games are capped at a fixed number of turns and
count as a draw (reward 0) if they reach it. Showdown has no such cap, but it
stops games that would otherwise never end.

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

### 4.5 Hidden information

The engine itself sees everything. A per-side observation function hides what
that player can't see: the opponent's unrevealed moves, items, abilities and
spreads, and which 4 of the 6 species shown at preview were actually brought.
In search, sampled worlds fill in the hidden parts from a set prior, built from
the team corpus and conditioned on what has been revealed.

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

1. Rust unit tests for each mechanic.
2. Differential tests against `src/`, the repo's current model of the format,
   including its house rules. Stats (done), then damage over every
   attacker, defender and move in the pool, then whole turns in deterministic
   mode.
3. Differential tests against Showdown itself, which is the ground truth for a
   ladder bot. Run the `pokemon-showdown` npm package's `simulate-battle`
   with the same choices and compare damage ranges and outcomes as
   distributions.

### 4.9 Phase 1 milestones (each about one commit)

- [x] 1a. Workspace scaffold, embedded dex, stat calculation, parity with `src/stats.py`.
- [ ] 1b. Move data in the dex and the damage formula. Parity with `src/damage.py` over the pool.
- [ ] 1c. Team construction from Showdown pastes, team validation, team preview.
- [ ] 1d. Turn resolution core: ordering, moves, damage, faints, forced switches, end of turn. Random-play smoke test with invariant checks.
- [ ] 1e. Mechanics breadth, driven by the coverage report.
- [ ] 1f. Turn-level differential tests against `src/battle.py`.
- [ ] 1g. Benchmark and speed gate.
- [ ] 1h. Differential tests against Showdown.

## 5. Open questions

1. **Stat points.** The engine follows `src/stats.py`: a flat +1 per point,
   added after the formula. It has to match how Showdown's Champions format
   actually computes stats, since that's where the bot plays. Check before 1h.
2. **Regulation.** `src/` targets Reg M-B and mikumiku37 played Reg M-C. Which
   legal pool should the engine and team corpus target?
3. **Team corpus.** mikumiku37 used about 1,260 public tournament teams. This
   repo has 17 teams in `data/teams/` and the usage sets in `mbsmogon.xlsx`. A
   source for a larger corpus is needed by phase 3.
4. **Champions mechanics changes.** Are there any differences from Scarlet and
   Violet (move or ability changes, Mega Evolution details)? Showdown's
   Champions mod is the authority here.
5. **Training machine.** Operating system and GPU. JAX's GPU support on native
   Windows is poor, so PyTorch is the default unless training runs on Linux or
   WSL.
