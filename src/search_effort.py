"""Search effort tiers, and a resumable result cache.

The robustness rating needs a full payoff matrix per turn, which is orders of
magnitude dearer than the screen it refines. Two consequences, both handled
here:

  1. Effort has to be a DIAL, not a constant. The cheap end reproduces the
     original behaviour so nothing is lost; the dear end is for finding a
     hidden great team and is expected to take a long time.
  2. A long search has to survive interruption. Anything that runs for hours
     must be resumable, or in practice it never gets run to completion.

The tiers below deliberately vary the things that actually cost time, in the
order they cost it:

    candidates   how many survivors get the expensive treatment
    leads        how many of their plausible leads each is audited against
    turns        the CAP on line length, not a fixed audit depth
    robustness   whether the exploitability rating runs at all

`turns` is a cap rather than a budget for a specific reason. The rating is
"does this line WIN against an opponent punishing every turn", and a line cut
off at turn 4 has not won -- it is unresolved, which counts as a loss. Set the
cap too low and every team scores zero wins and the ranking collapses to
exploitability alone, which is the failure mode where a team that loses
everything ranks first. So each tier's cap must be long enough for a game to
actually finish.

`nash` is NOT a tier setting: the audit is always piloted with the equilibrium
solver, because rating a team while piloting it badly measures the pilot rather
than the team. What varies is how much auditing happens, not how well it is
done.

`prescreen` is None on every tier by default, deliberately. A prescreen
discards candidates before they are ever simulated, so if it is set too narrow
the search becomes faster and SILENTLY WRONG -- the dropped candidate never
appears in the output to be missed. The safe width is a measurement, not a
guess: run tools/measure_prescreen.py, read the recall column, and pass the
narrowest width that still recalls ~100% via --prescreen.
"""
import hashlib
import json
import os
import tempfile

TIERS = {
    "quick": dict(
        label="Quick",
        verify_top=3, robustness=False, leads=0, turns=0, prescreen=None,
        blurb="Screen plus win-count verification. The original behaviour: "
              "fast, and ranked by games won against a fixed opponent model.",
    ),
    "standard": dict(
        label="Standard",
        verify_top=3, robustness=True, leads=2, turns=16, prescreen=None,
        blurb="Audits the top 3 brings against their 2 most plausible leads, "
              "playing each line to a finish against an opponent who punishes "
              "every turn. Ranks by wins that hold up.",
    ),
    "thorough": dict(
        label="Thorough",
        verify_top=6, robustness=True, leads=4, turns=18, prescreen=None,
        blurb="6 brings against 4 of their leads, longer lines. Minutes rather "
              "than seconds; the setting to trust before committing to a team.",
    ),
    "exhaustive": dict(
        label="Exhaustive",
        verify_top=20, robustness=True, leads=6, turns=20, prescreen=None,
        blurb="20 brings against 6 of their leads. For finding a hidden team "
              "with great lines; expected to take a long time, and designed to "
              "be run in resumable batches.",
    ),
}

TIER_ORDER = ["quick", "standard", "thorough", "exhaustive"]


def tier(name):
    return TIERS[name if name in TIERS else "standard"]


def relative_cost(name):
    """Rough multiple of the quick tier, for warning the user before they wait.

    Audit cost scales with candidates x leads x turns; the screen is shared.
    """
    t = tier(name)
    if not t["robustness"]:
        return 1.0
    return 1.0 + (t["verify_top"] * t["leads"] * t["turns"]) / 6.0


class ResultCache:
    """A JSON-backed store of completed work, safe to interrupt.

    Keyed by a hash of everything that would change the answer, so a re-run with
    the same settings resumes instead of repeating, and a re-run with DIFFERENT
    settings does not silently reuse stale results -- the failure mode that
    makes caches worse than useless.

    Written atomically (temp file plus rename) because the whole point is to
    survive being killed partway through, and a half-written JSON file would
    lose the entire run rather than one entry.
    """

    def __init__(self, path):
        self.path = path
        self.data = {}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    self.data = json.load(fh)
            except (OSError, ValueError):
                self.data = {}   # corrupt or truncated: start over rather than crash

    @staticmethod
    def key(*parts):
        blob = json.dumps(parts, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        self.data[key] = value

    def save(self):
        if not self.path:
            return
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh)
            os.replace(tmp, self.path)     # atomic on POSIX
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def __len__(self):
        return len(self.data)


def batches(items, size):
    """Split work into chunks so progress can be saved between them."""
    size = max(1, size)
    for i in range(0, len(items), size):
        yield items[i:i + size]
