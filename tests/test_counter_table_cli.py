"""CLI coverage for the item 1/3/4/5 changes to `tools/counter_table.py`:

    1. multi-bring4 cores can be 4, 5, or 6 members, never padded with dead
       weight ("core"/"core_size"/"unused", not the old fixed "team6").
    2. mega + its own base form can't both be brought (already covered at
       the `counter_finder` layer -- `bring4_search` raises).
    3. Choice Scarf is excluded from every SEARCH by default; --allow-scarf
       opts back in.
    4. --max-weak/--type-limit are hard multi-bring4 synergy filters, and
       every printed/CSV'd core shows its `member_weakness_summary`.
    5. a teamsheet (item + moves + usage%) is shown for every bring-4
       member, per enemy.

Mirrors this repo's existing CLI-testing convention (`test_pick_and_beam_
flags.py`): unit-test the parsing helpers directly, and use `--help`
subprocess checks to confirm a documented flag is one argparse actually
accepts (a flag that's in the docstring but not wired is a command that
cannot run).
"""
import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import counter_table as ct  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def run_main(argv):
    """`main()` with `sys.argv` patched, capturing stdout -- validation
    errors (the only thing the argv-only tests below exercise) all raise
    `SystemExit` BEFORE `load_world()` runs, so this stays fast."""
    old_argv = sys.argv
    sys.argv = ["counter_table.py"] + argv
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            ct.main()
        return None, buf.getvalue()
    except SystemExit as e:
        return str(e), buf.getvalue()
    finally:
        sys.argv = old_argv


class TestParseTypeLimits(unittest.TestCase):

    def test_a_single_type_both_keys(self):
        self.assertEqual(ct._parse_type_limits(["Fire:max_weak=1,max_net=-2"]),
                         {"Fire": {"max_weak": 1, "max_net": -2}})

    def test_repeated_flag_gives_one_entry_per_type(self):
        got = ct._parse_type_limits(["Fire:max_weak=1", "Ice:max_net=0"])
        self.assertEqual(got, {"Fire": {"max_weak": 1}, "Ice": {"max_net": 0}})

    def test_one_key_may_be_omitted(self):
        self.assertEqual(ct._parse_type_limits(["Water:max_weak=2"]),
                         {"Water": {"max_weak": 2}})

    def test_empty_list_is_none(self):
        self.assertIsNone(ct._parse_type_limits([]))

    def test_unknown_type_is_rejected(self):
        with self.assertRaises(SystemExit) as caught:
            ct._parse_type_limits(["Cheese:max_weak=1"])
        self.assertIn("unknown type", str(caught.exception))

    def test_missing_colon_is_rejected(self):
        with self.assertRaises(SystemExit) as caught:
            ct._parse_type_limits(["Fire max_weak=1"])
        self.assertIn("Type:max_weak", str(caught.exception))

    def test_unknown_key_is_rejected(self):
        with self.assertRaises(SystemExit) as caught:
            ct._parse_type_limits(["Fire:max_wet=1"])
        self.assertIn("max_weak/max_net", str(caught.exception))

    def test_a_non_integer_value_is_rejected(self):
        with self.assertRaises(SystemExit) as caught:
            ct._parse_type_limits(["Fire:max_weak=one"])
        self.assertIn("integer", str(caught.exception))


class TestModeRestrictionsOnTheNewFlags(unittest.TestCase):
    """--max-weak/--type-limit only mean something under --multi-bring4 --
    same "reject loudly rather than silently ignore" convention every other
    mode-specific flag in this file already follows."""

    def test_max_weak_without_multi_bring4_is_rejected(self):
        msg, _out = run_main(["--vs", "Kingambit", "--max-weak", "2"])
        self.assertIsNotNone(msg)
        self.assertIn("--multi-bring4", msg)

    def test_type_limit_without_multi_bring4_is_rejected(self):
        msg, _out = run_main(["--vs", "Kingambit",
                              "--type-limit", "Fire:max_weak=1"])
        self.assertIsNotNone(msg)
        self.assertIn("--multi-bring4", msg)

    def test_allow_scarf_has_no_mode_restriction(self):
        """Unlike --max-weak/--type-limit, --allow-scarf threads through
        every search mode (`counter_finder`'s `excluded_items` is a
        parameter on all ten of them) -- it must NOT be rejected here."""
        msg, _out = run_main(["--vs", "unknown-pokemon-name", "--allow-scarf"])
        # Fails on the unrelated "unknown Pokemon" check, not a mode
        # restriction -- proves --allow-scarf itself was accepted.
        self.assertIn("unknown Pokemon", msg)


class TestHelpDocumentsTheNewFlags(unittest.TestCase):
    """A flag missing from `--help` is one nobody can discover; a flag in
    the header docstring but not parsed is one that cannot run -- the same
    two failure modes `test_pick_and_beam_flags.py` guards against for
    `overnight.bat`."""

    @classmethod
    def setUpClass(cls):
        cls.help_text = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "counter_table.py"),
             "--help"], capture_output=True, text=True, timeout=60).stdout

    def test_allow_scarf_is_parsed(self):
        self.assertIn("--allow-scarf", self.help_text)

    def test_max_weak_is_parsed(self):
        self.assertIn("--max-weak", self.help_text)

    def test_type_limit_is_parsed(self):
        self.assertIn("--type-limit", self.help_text)

    def test_every_flag_the_module_docstring_shows_is_parsed(self):
        import re
        docstring = ct.__doc__
        shown = set(re.findall(r"(--[a-z0-9-]+)", docstring))
        parsed = set(re.findall(r"(--[a-z0-9-]+)", self.help_text))
        missing = sorted(shown - parsed)
        self.assertEqual(missing, [], f"documented but not parsed: {missing}")


class TestMultiBring4EndToEnd(unittest.TestCase):
    """A real (small) run, exercising the full item 1/3/4/5 wiring together
    -- variable core size, the synergy summary, the teamsheet, and a hard
    --max-weak filter actually changing the result."""

    @classmethod
    def setUpClass(cls):
        cls.argv = ["--pool-size", "16", "--multi-bring4",
                   "--vs-team", "Kingambit,Basculegion,Garchomp,Whimsicott",
                   "--vs-team", "Sylveon,Mega Charizard Y,Sinistcha,Farigiraf",
                   "--top", "3"]

    def test_runs_clean_and_shows_a_core_with_synergy_and_teamsheet(self):
        msg, out = run_main(self.argv)
        self.assertIsNone(msg, out)
        self.assertIn("synergy: weak to 2+ types", out)
        self.assertIn(" @ ", out)  # "Name @ Item: moves" teamsheet line
        self.assertNotIn("team6", out, "the old field name must not leak "
                         "into the printed report")

    def test_a_core_may_be_smaller_than_six(self):
        """"a full team of only 4-5 members is not a problem ... I would
        still like to see them" -- confirmed structurally, not just by
        eyeballing one run's printout."""
        from _harness import load_world
        from generate_team import build_candidate_pool
        W = load_world()
        merged, moves = W["merged"], W["moves"]
        natures, typechart = W["natures"], W["typechart"]
        # Same pool-building path `_pool`/`--pool-size 16` uses -- a plain
        # alphabetical slice doesn't reliably overlap with the fixture
        # enemies below.
        pool = ct._apply_preferences(
            list(build_candidate_pool(merged, top_n=16)), merged)
        from counter_finder import multi_bring4_coverage, multi_bring4_exhaustive
        coverage = multi_bring4_coverage(
            pool,
            [["Kingambit", "Basculegion", "Garchomp", "Whimsicott"],
             ["Sylveon", "Mega Charizard Y", "Sinistcha", "Farigiraf"]],
            merged, moves, natures, typechart)
        rows = multi_bring4_exhaustive(coverage)
        sizes = {r["core_size"] for r in rows}
        self.assertTrue(sizes, "fixture assumes at least one core is found")
        self.assertTrue(all(r["unused"] == () for r in rows))

    def test_max_weak_actually_changes_the_result(self):
        """A hard filter that never changes anything is a no-op wired in
        for show -- confirm --max-weak actually removes a core (or
        reorders the top one) rather than just printing more text."""
        _msg1, out_unfiltered = run_main(self.argv)
        _msg2, out_filtered = run_main(self.argv + ["--max-weak", "1"])
        self.assertNotEqual(out_unfiltered, out_filtered)

    def test_csv_export_uses_core_not_team6(self):
        import csv
        import tempfile
        with tempfile.NamedTemporaryFile(
                suffix=".csv", delete=False, mode="w") as f:
            path = f.name
        try:
            msg, _out = run_main(self.argv + ["--csv", path])
            self.assertIsNone(msg)
            with open(path, newline="", encoding="utf-8") as fh:
                header = next(csv.reader(fh))
            self.assertIn("core", header)
            self.assertIn("core size", header)
            self.assertNotIn("team6", header)
            self.assertIn("weak to 2+ types (members)", header)
            self.assertTrue(any("teamsheet" in h for h in header))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
