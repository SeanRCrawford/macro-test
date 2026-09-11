"""Shared setup for the measurement tools in this directory.

The tools all need the same three things: load the dataset, stand up a Battle
from two lists of species names, and advance a COPY of a battle by one turn
without disturbing the original. That last one is fiddly enough (actions hold
references to Combatant objects, which deepcopy replaces) that having one
correct implementation beats three.
"""
import os
import pickle
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from species_data import build_merged_dataset, load_teams  # noqa: E402
from combatants import make_team  # noqa: E402
from battle import Battle  # noqa: E402
from solver import build_moveset, build_wide_movesets, heuristic_eval  # noqa: E402

# "Find a way to make the golden baseline much faster - it takes far too
# long, especially for small changes." `build_merged_dataset` (parses
# mbsmogon.xlsx/roster.csv) is the real cost here (~0.3s, MEASURED) on every
# fresh process, even though a "small change" almost never touches those two
# files -- only the SOURCE CODE being verified. `_fingerprint` is a cheap
# (`os.stat`, no parsing) snapshot of just those two files; unchanged since
# the last call, the parsed result is reloaded from a pickle instead of
# re-parsed from scratch.
#
# `load_teams` is DELIBERATELY excluded from this cache -- it's already
# fast (~4ms, MEASURED, negligible next to the xlsx/csv parse), and unlike
# mbsmogon.xlsx/roster.csv its own data/teams/data/my_teams *.txt folders
# are legitimately mutable DURING a run (the Streamlit app's own "Save to
# My Teams" button writes there; so do this repo's own tests exercising
# that flow) -- caching it risks a call seeing a stale team roster if a
# file appears/disappears between one `load_world()` call and another's
# cache write, for a few ms of savings not worth that risk. Every call
# re-derives `teams`/`meta` fresh, keyed off the CACHED (or freshly built)
# `merged` dict either way.
#
# NOTE: `build_merged_dataset`/`load_teams` stay imported unconditionally
# above (not deferred into the cache-miss branch) because `combatants.py`
# (also imported unconditionally, needed for `make_team`) already imports
# `species_data` itself -- pandas/poke_env are paid for either way, so there
# is nothing left to save by delaying this particular import.
_CACHE_PATH = os.path.join(os.path.dirname(__file__), ".world_cache.pkl")
_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def _fingerprint():
    """[(path, mtime_ns, size), ...] for mbsmogon.xlsx/roster.csv -- the
    only two files `_dataset_only()` reads. Pure `os.stat`, no parsing, no
    pandas. A missing path's `st_size`/`mtime_ns` come back as `None`,
    distinct from any real stat result."""
    out = []
    for name in ("mbsmogon.xlsx", "roster.csv"):
        p = os.path.join(_DATA_DIR, name)
        try:
            st = os.stat(p)
            out.append((p, st.st_mtime_ns, st.st_size))
        except FileNotFoundError:
            out.append((p, None, None))
    return out


def _dataset_only():
    """`build_merged_dataset()`'s own return, cached to disk -- see the
    module-level comment above `_CACHE_PATH` for why `load_teams` is
    deliberately NOT part of this cache. Every value in `merged`/`moves`/
    `natures`/`typechart` is a plain dict/list/str/float already (confirmed
    via direct inspection), so nothing here needs pandas to unpickle."""
    fp = _fingerprint()
    if os.path.exists(_CACHE_PATH):
        try:
            with open(_CACHE_PATH, "rb") as fh:
                cached_fp, cached = pickle.load(fh)
            if cached_fp == fp:
                return cached
        except Exception:
            pass  # corrupt/stale/foreign-format cache -- rebuild for real
    result = build_merged_dataset()
    try:
        with open(_CACHE_PATH, "wb") as fh:
            pickle.dump((fp, result), fh)
    except OSError:
        pass  # a write failure (e.g. read-only checkout) just costs the
              # next call its cache hit -- never worth failing THIS call over
    return result


def load_world():
    """Dataset plus the team library. `build_merged_dataset` is slow
    (parses the sheets) on a cache miss (see `_dataset_only`); `load_teams`
    always runs fresh, so a file dropped into/removed from data/teams or
    data/my_teams between calls is never missed."""
    merged, _usage, moves, natures, typechart = _dataset_only()
    teams, meta = load_teams(with_meta=True, merged=merged)
    return dict(merged=merged, moves=moves, natures=natures,
                typechart=typechart, teams=teams, meta=meta)


def setup_battle(our4, enemy4, world, sets=None, enemy_sets=None):
    """A fresh Battle plus the movesets dict the solver functions expect.

    `sets` / `enemy_sets` are the per-Pokemon item/move/EV overrides `make_team`
    already understands -- needed so a caller can ask "what if this one held a
    Roseli Berry" without rebuilding the world.
    """
    ours = make_team(our4, world["merged"], world["natures"], sets=sets)
    theirs = make_team(enemy4, world["merged"], world["natures"],
                       sets=enemy_sets)
    movesets = {c.name: build_moveset(world["merged"][c.name], world["moves"])
                for c in ours + theirs}
    battle = Battle(ours, theirs, world["typechart"], world["moves"])
    # The answer-preservation term needs to know what each Pokemon can do, and
    # Combatant does not carry its moves. Attaching them here turns the term on
    # for every measurement tool at once.
    battle.movesets = movesets
    # The opponent's plausible move space is wider than the four we assume;
    # see solver.build_wide_moveset for the measurement behind this.
    # Must cover BOTH rosters: solve_turn can be called from either seat, and
    # "their" side is whichever one is not being solved for. Only the enemy
    # entries are widened -- we know our own four moves.
    battle.wide_movesets = {
        **movesets,
        **build_wide_movesets([c.name for c in theirs], world["merged"],
                              world["moves"]),
    }
    return battle, movesets


def enemy_bring(team_name, world):
    """The lead pair from team metadata, padded to four from the roster."""
    roster = world["teams"][team_name]
    lead = world["meta"].get(team_name, {}).get("lead") or roster[:2]
    return list(lead) + [x for x in roster if x not in lead][:2]


# Re-exported from src/turn_step.py, which the production turn solver also
# uses. Kept importable from here so the existing harnesses do not change.
from turn_step import step  # noqa: E402,F401


def evaluate(battle, side="p1"):
    return heuristic_eval(battle, side)


LOSS = -1e4  # score for a joint action that fails to simulate
