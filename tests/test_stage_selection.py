"""Coming back the next day and running stage 2, without redoing stage 1.

THE REPORTED BUG. You generate 60 teams from a pool of 50, look at the ranking,
and come back with

    overnight.bat --deep-effort thorough --pick "5" --vs "Big 6"

...and stage 1 runs all over again. Two separate failures behind that:

  1. there was no way to say "skip generation, I already have a shortlist"; and
  2. the stage 1 flags are absent from that command, so it silently fell back
     to the defaults (34 / 40), which generate DIFFERENT teams -- measured on
     the real dataset, pool 50/60 and 34/40 share none of their top five -- so
     the shortlist is renumbered and `--pick "5"` means a team you never saw.

These cover the Python half (the settings fingerprint and the warning). The
batch half -- which flag combination runs which stage -- is exercised by
replaying overnight.bat's own parser, since a .bat cannot be run here.
"""
import argparse
import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import generate_overnight as go  # noqa: E402

BAT = os.path.join(os.path.dirname(__file__), "..", "overnight.bat")


def args(**kw):
    base = dict(pool_size=50, candidates=60, generations=None, beam_width=30,
                effort="standard", turns=10, optimise_sets=True,
                min_winrate=0.8, script_screen=False, punish_screen=None,
                worst_matchup=0.0, out="shortlist.json")
    base.update(kw)
    return argparse.Namespace(**base)


def warn_output(current, stored):
    """What _warn_if_renumbering prints, given what is already on disk."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "shortlist.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"teams": {"gen01": ["A"] * 6}}
                      | ({"settings": stored} if stored else {}), fh)
        current.out = path
        buf = io.StringIO()
        with redirect_stdout(buf):
            go._warn_if_renumbering(current)
        return buf.getvalue()


class TestRenumberWarning(unittest.TestCase):

    def test_dropping_the_stage1_flags_is_called_out(self):
        text = warn_output(args(pool_size=34, candidates=40),
                           go._stage1_settings(args()))
        self.assertIn("WARNING", text)
        self.assertIn("pool_size", text)
        self.assertIn("candidates", text)
        self.assertIn("--stage2-only", text,
                      "the warning must say what to run instead")

    def test_an_identical_rerun_says_nothing(self):
        """Resuming an interrupted run is normal and must stay quiet."""
        self.assertEqual(warn_output(args(), go._stage1_settings(args())), "")

    def test_a_shortlist_from_before_this_change_is_not_second_guessed(self):
        """No recorded settings means no basis for a comparison. Warning anyway
        would cry wolf on every first run after upgrading."""
        self.assertEqual(warn_output(args(pool_size=34), None), "")

    def test_no_shortlist_at_all_is_silent(self):
        a = args(out=os.path.join(tempfile.gettempdir(), "does-not-exist.json"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            go._warn_if_renumbering(a)
        self.assertEqual(buf.getvalue(), "")

    def test_the_fingerprint_covers_what_changes_the_teams(self):
        base = go._stage1_settings(args())
        for field, value in (("pool_size", 34), ("candidates", 40),
                             ("generations", "1-5"), ("beam_width", 60),
                             ("effort", "thorough"), ("turns", 12),
                             ("optimise_sets", False), ("min_winrate", 0.5),
                             ("script_screen", True), ("punish_screen", -200.0),
                             ("worst_matchup", 0.89)):
            self.assertNotEqual(base, go._stage1_settings(args(**{field: value})),
                                f"{field} does not reach the fingerprint")

    def test_the_fingerprint_is_written_into_the_shortlist(self):
        """Nothing to compare against next time if it is not persisted."""
        source = open(os.path.join(os.path.dirname(__file__), "..", "tools",
                                   "generate_overnight.py"),
                      encoding="utf-8").read()
        self.assertIn('"settings": _stage1_settings(args)', source)


class TestBatchStageSelection(unittest.TestCase):
    """Replay overnight.bat's own parser. The flag table is READ OUT of the
    file rather than restated here, so this cannot pass against a .bat that
    says something different."""

    @classmethod
    def setUpClass(cls):
        cls.text = open(BAT, encoding="utf-8").read()
        cls.table = {}
        for flag, body in re.findall(
                r'^if /i "%~1"=="(--[a-z0-9-]+)"\s*\((.*?)\)\s*$',
                cls.text, re.M | re.I):
            cls.table[flag] = (body.count("shift") > 1,
                               dict(re.findall(r"set ([A-Z0-9]+)=([^&]*)", body)))
        cls.defaults = {k: v.strip() for k, v in re.findall(
            r"^set ([A-Z0-9]+)=(.*)$", cls.text, re.M)}

    def stages(self, argv, shortlist_exists=True):
        v = dict(self.defaults)
        i = 0
        while i < len(argv):
            self.assertIn(argv[i].lower(), self.table, f"unknown flag {argv[i]}")
            takes_value, sets = self.table[argv[i].lower()]
            for name, expr in sets.items():
                v[name] = (expr.replace("%~2", argv[i + 1]).strip()
                           if takes_value else expr.strip())
            i += 2 if takes_value else 1
        if (v.get("PICK") and not v.get("GENFLAGS") and not v.get("REGEN")
                and not v.get("LISTONLY") and shortlist_exists):
            v["STAGE2ONLY"] = "1"
        stage2only, listonly = bool(v.get("STAGE2ONLY")), bool(v.get("LISTONLY"))
        return (not stage2only), (stage2only or not listonly)

    def test_the_reported_command_now_runs_stage_2_only(self):
        self.assertEqual(
            self.stages(["--deep-effort", "thorough", "--pick", "5",
                         "--vs", "Big 6"]),
            (False, True))

    def test_stage2_only_is_explicit_too(self):
        self.assertEqual(self.stages(["--stage2-only", "--pick", "5"]),
                         (False, True))

    def test_list_still_stops_after_stage_1(self):
        self.assertEqual(
            self.stages(["--pool-size", "50", "--candidates", "60", "--list"]),
            (True, False))

    def test_a_plain_run_still_does_both(self):
        self.assertEqual(self.stages([]), (True, True))
        self.assertEqual(
            self.stages(["--pool-size", "50", "--optimise-sets"]), (True, True))

    def test_asking_to_generate_AND_pick_still_generates(self):
        """--pick is only 'use what I have' when nothing asked for new teams."""
        self.assertEqual(
            self.stages(["--pool-size", "50", "--candidates", "60",
                         "--pick", "1,2"]),
            (True, True))

    def test_regenerate_overrides_the_shortcut(self):
        self.assertEqual(self.stages(["--pick", "5", "--regenerate"]),
                         (True, True))

    def test_with_no_shortlist_on_disk_it_generates(self):
        self.assertEqual(self.stages(["--pick", "5"], shortlist_exists=False),
                         (True, True))

    def test_the_goto_target_exists(self):
        """`goto stage2` against a missing label is a hard batch error."""
        self.assertIn(":stage2", self.text)


if __name__ == "__main__":
    unittest.main()
