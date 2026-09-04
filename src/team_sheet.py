"""
Team sheet file format -- the bridge between the two tools.

`generate_team.py` writes `team.json` (names + optimised items + movesets),
and `run_search.py` reads it back with `--team-file team.json`, so the exact
team you generated (including its items and 4-move sets) is what gets
searched for leads/backs and gameplans. Without this, run_search would fall
back to raw usage-stat defaults and you'd be planning around a different
team than the one you generated.

Format:
{
  "pool": ["Incineroar", "Mega Charizard Y", ...],
  "sets": {
     "Incineroar": {"item": "Sitrus Berry",
                     "moves": ["Fake Out", "Parting Shot", "Flare Blitz", "Darkest Lariat"],
                     "evs": {"hp":32, "atk":0, ...}, "nature": "Careful"},   # both optional
     ...
  },
  "analysis": {   # optional -- a "team detail" snapshot, see
                  # team_search.compute_team_analysis. Lets the Team Builder
                  # tab show member/threat contributions right after a plain
                  # load, not only after a live generation run.
     "tested_against": ["Rain", "Sun Rain", ...],
     "members": {"Incineroar": {"wins": 12, "top_threats": [...], "irreplaceable": [...]}, ...}
  }
}
It's plain JSON -- edit it by hand freely (swap an item, change a move) and
re-run run_search to see the effect. "analysis" is a snapshot computed at
save time -- if you hand-edit "sets" afterwards it can go stale; re-run the
analysis (Team Builder tab) to refresh it.
"""
import base64
import json
from pathlib import Path

# A single-cell, copy-pasteable stand-in for a whole team.json's {"pool",
# "sets"} -- "export any of the teamsheets generated in the CLI's .xlsx to
# the streamlit app ... maybe with a base64 encoded pokepaste in an excel
# cell". Plain JSON in an Excel cell is fragile (quotes/newlines get mangled
# by copy-paste and by Excel's own CSV-flavoured clipboard format); base64
# survives that round-trip intact. The prefix makes a token unambiguous to
# detect (as opposed to a stray base64-looking string) and versions the
# format, so a future change to what's encoded is detectable instead of
# silently misparsed.
TEAMSHEET_TOKEN_PREFIX = "TSHEET1:"


def encode_teamsheet(pool, sets=None):
    """(pool, sets) -> a single-line `TEAMSHEET_TOKEN_PREFIX`-prefixed token,
    safe to drop in one Excel/CSV cell or paste as one line anywhere."""
    payload = {"pool": list(pool), "sets": sets or {}}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return TEAMSHEET_TOKEN_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii")


def decode_teamsheet(text):
    """The inverse of `encode_teamsheet`: token text -> (pool, sets). Raises
    ValueError if `text` isn't a recognised token (wrong/missing prefix,
    invalid base64, or the decoded JSON isn't the expected shape) -- a
    caller decides what to fall back to (raw pokepaste text, plain JSON)."""
    text = text.strip()
    if not text.startswith(TEAMSHEET_TOKEN_PREFIX):
        raise ValueError("not a teamsheet token (missing prefix)")
    b64 = text[len(TEAMSHEET_TOKEN_PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(b64.encode("ascii"))
        data = json.loads(raw)
    except Exception as e:
        raise ValueError(f"malformed teamsheet token: {e}") from e
    if not isinstance(data, dict) or "pool" not in data:
        raise ValueError("decoded teamsheet token has no 'pool'")
    return list(data["pool"]), dict(data.get("sets") or {})


def save_team(path, pool, sets=None, analysis=None):
    data = {"pool": list(pool), "sets": sets or {}}
    if analysis is not None:
        data["analysis"] = analysis
    Path(path).write_text(json.dumps(data, indent=2))
    return path


def load_team(path):
    """Returns (pool, sets). Tolerates a bare list of names as well."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return list(data), {}
    pool = data.get("pool") or []
    sets = data.get("sets") or {}
    if not pool:
        raise ValueError(f"{path}: no 'pool' list found in team file")
    return pool, sets


def load_analysis(path):
    """The persisted 'analysis' (team-detail/contribution) snapshot from a
    team.json, or None if absent -- an older file, a hand-built one, or one
    saved without it."""
    data = json.loads(Path(path).read_text())
    return data.get("analysis") if isinstance(data, dict) else None


def load_sets_override(path):
    """Load a standalone sets override file: either {"sets": {name: {...}}}
    (same shape team.json uses) or a bare {name: {...}} dict. Used for
    enemy-set overrides (e.g. pin a specific enemy Kingambit's exact
    moveset), which have no 'pool' of their own since they only override
    a subset of whatever roster the enemy team already brings."""
    data = json.loads(Path(path).read_text())
    if "sets" in data and isinstance(data["sets"], dict):
        return data["sets"]
    return data


def describe_sets(pool, sets, merged=None):
    """Human-readable summary lines for a loaded team sheet."""
    lines = []
    for n in pool:
        spec = sets.get(n) or {}
        item = spec.get("item")
        moves = spec.get("moves")
        if item or moves:
            lines.append(f"  {n:<20}{str(item or '(usage default)'):<18}"
                          f"{', '.join(moves) if moves else '(usage default moves)'}")
        else:
            lines.append(f"  {n:<20}(usage defaults)")
    return lines
