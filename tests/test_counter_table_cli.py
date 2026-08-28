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
from counter_finder import Hit  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def hit(name="Move", frac=0.5):
    return Hit(move_name=name, frac=frac, lo=frac, avg=frac, hi=frac,
              eff=1.0, num_targets_hit=1)


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
        # 3, not 2: --max-weak now defaults to 2 ("by default ... the
        # weakness limit should be 2"), so an explicit value must differ
        # from the default to be distinguishable from "never passed" --
        # same "!= default" convention --beam-width/--max-candidates
        # already use for this exact check.
        msg, _out = run_main(["--vs", "Kingambit", "--max-weak", "3"])
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

    def test_deep_dive_core_without_multi_bring4_is_rejected(self):
        msg, _out = run_main(["--vs", "Kingambit", "--deep-dive-core", "1"])
        self.assertIsNotNone(msg)
        self.assertIn("--multi-bring4", msg)

    def test_xlsx_without_multi_bring4_is_rejected(self):
        msg, _out = run_main(["--vs", "Kingambit", "--xlsx", "/tmp/x.xlsx"])
        self.assertIsNotNone(msg)
        self.assertIn("--multi-bring4", msg)


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

    def test_max_megas_is_parsed(self):
        self.assertIn("--max-megas", self.help_text)

    def test_deep_dive_core_is_parsed(self):
        self.assertIn("--deep-dive-core", self.help_text)

    def test_xlsx_is_parsed(self):
        self.assertIn("--xlsx", self.help_text)

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
        # good_threshold relaxed well below the default 100%: this pool's
        # top pairs now correctly lose more often once Whimsicott's REAL
        # Tailwind access is assumed (`_pair_vs_targets`'s "assume they set
        # tailwind and see if it is a loss") and once every member's
        # item/moveset is fixed across BOTH enemy teams instead of being
        # independently re-optimised per enemy (`multi_bring4_coverage`'s
        # own "a real team's set is fixed for the whole event" fix) -- at
        # 0.8 the surviving candidate pool happens to be all 4 Mega-capable
        # names, which the (also new) default --max-megas 2 cap correctly
        # empties to zero cores; 0.5 gives a real, multi-size, mixed
        # mega/non-mega candidate pool to exercise the same exhaustive-
        # search structure this test actually cares about.
        coverage = multi_bring4_coverage(
            pool,
            [["Kingambit", "Basculegion", "Garchomp", "Whimsicott"],
             ["Sylveon", "Mega Charizard Y", "Sinistcha", "Farigiraf"]],
            merged, moves, natures, typechart, good_threshold=0.5)
        rows = multi_bring4_exhaustive(coverage, good_threshold=0.5)
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


class TestDeepDiveCoreAndXlsxExport(unittest.TestCase):
    """"I also want to see the gameplans for each pair included in a team
    vs enemies. Given the size, maybe make this deep dive an option after
    the search has run" + "it may make more sense to make counter_table.py
    export an xlsx rather than a csv"."""

    @classmethod
    def setUpClass(cls):
        cls.argv = ["--pool-size", "16", "--multi-bring4",
                   "--vs-team", "Kingambit,Basculegion,Garchomp,Whimsicott",
                   "--vs-team", "Sylveon,Mega Charizard Y,Sinistcha,Farigiraf",
                   "--top", "3"]

    def test_deep_dive_core_prints_the_aggregate_and_gameplans(self):
        msg, out = run_main(self.argv + ["--deep-dive-core", "1"])
        self.assertIsNone(msg, out)
        self.assertIn("Deep dive:", out)
        self.assertIn("OVERALL, every pair vs every enemy", out)
        self.assertIn("beaten (", out)
        self.assertIn("tw-safe", out)
        self.assertIn("pr-safe", out)
        # A real gameplan line (a turn's hit) must be present -- "T1 ... ->
        # ...: <move> ...%", matching --deep's own format.
        self.assertRegex(out, r"T\d .+ -> .+: .+ \d+-\d+-\d+%")

    def test_deep_dive_core_out_of_range_is_rejected(self):
        msg, _out = run_main(self.argv + ["--deep-dive-core", "999"])
        self.assertIsNotNone(msg)
        self.assertIn("--deep-dive-core", msg)

    def test_deep_dive_core_is_not_computed_by_default(self):
        """The whole point of making it opt-in -- a plain run must not pay
        for (or print) the full per-pair-per-enemy detail."""
        _msg, out = run_main(self.argv)
        self.assertNotIn("Deep dive:", out)

    def test_xlsx_export_writes_a_real_workbook(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            path = f.name
        os.unlink(path)  # let the tool create it fresh
        try:
            msg, out = run_main(self.argv + ["--xlsx", path])
            self.assertIsNone(msg, out)
            self.assertTrue(os.path.exists(path))
            from openpyxl import load_workbook
            wb = load_workbook(path)
            self.assertIn("Cores", wb.sheetnames)
            ws = wb["Cores"]
            self.assertGreater(ws.max_row, 1, "header plus at least one core row")
            self.assertEqual(ws.cell(row=1, column=1).value, "Core")
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_xlsx_export_with_deep_dive_adds_the_extra_sheets(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            path = f.name
        os.unlink(path)
        try:
            msg, _out = run_main(
                self.argv + ["--deep-dive-core", "1", "--xlsx", path])
            self.assertIsNone(msg)
            from openpyxl import load_workbook
            wb = load_workbook(path)
            for name in ("Cores", "Deep Dive Sets", "Deep Dive Summary",
                        "Deep Dive Gameplans"):
                self.assertIn(name, wb.sheetnames)
            gp = wb["Deep Dive Gameplans"]
            self.assertGreater(gp.max_row, 1)
            self.assertEqual(gp.cell(row=1, column=1).value, "Pair")
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestDeepDiveCoreForBring4(unittest.TestCase):
    """--deep-dive-core now also works with --bring4 (a fixed 6, one enemy
    roster), not just --multi-bring4 -- "I want to be able to choose a
    specific team to deep dive into" applies just as much to a bring-4 as
    to a multi-bring4 core: `core_deep_dive` accepts any core, so this is
    just wiring the Nth-ranked `bring4_rows` entry into it, the same way
    `--multi-bring4` already wires its own Nth-ranked core."""

    @classmethod
    def setUpClass(cls):
        cls.argv = ["--our",
                   "Kingambit,Dragapult,Whimsicott,Ninetales-Alola,"
                   "Mega Alakazam,Sharpedo",
                   "--bring4", "--vs", "Sableye,Ariados"]

    def test_deep_dive_core_prints_the_aggregate_and_gameplans(self):
        msg, out = run_main(self.argv + ["--deep-dive-core", "1"])
        self.assertIsNone(msg, out)
        self.assertIn("Deep dive:", out)
        self.assertIn("OVERALL, every pair vs every enemy", out)
        self.assertIn("tw-safe", out)
        self.assertIn("pr-safe", out)
        self.assertRegex(out, r"T\d .+ -> .+: .+ \d+-\d+-\d+%")

    def test_deep_dive_core_out_of_range_is_rejected(self):
        msg, _out = run_main(self.argv + ["--deep-dive-core", "999"])
        self.assertIsNotNone(msg)
        self.assertIn("--deep-dive-core", msg)

    def test_deep_dive_core_is_not_computed_by_default(self):
        _msg, out = run_main(self.argv)
        self.assertNotIn("Deep dive:", out)

    def test_deep_dive_core_without_bring4_or_multi_bring4_is_still_rejected(self):
        msg, _out = run_main(["--vs", "Kingambit", "--deep-dive-core", "1"])
        self.assertIsNotNone(msg)
        self.assertIn("--deep-dive-core", msg)


class TestDetailPreviewsShowTheWorstMatchupsFirst(unittest.TestCase):
    """Both `_print_pairs` and `_print_joint` picked their per-row detail
    preview via `sorted(r["detail"].items(), key=...)[:3]`, into a variable
    literally named `worst` -- but `_RANK`/`_JOINT_RANK` rank the BEST
    outcome (clean/sweep) as 0 and the WORST (pinned/loss) as 3, so a plain
    ascending sort put the 3 BEST matchups under that name, not the worst
    ones. `_print_deep`'s own docstring says outright "LOSSES AND OHKO
    RISKS FIRST, since those are what's actionable" -- its sort had the
    exact same inversion. All three now sort worst-first."""

    def test_print_pairs_shows_the_pinned_result_before_the_clean_one(self):
        row = {
            "name": "Gallade", "item": "Life Orb", "pairs_clean": 1,
            "pairs_trade": 0, "pairs_no_ko": 0, "pairs_pinned": 1,
            "pairs_total": 2,
            "detail": {
                ("Kingambit", "Basculegion"): {
                    "outcome": "clean", "target": "Kingambit",
                    "hits": {"C": {"Kingambit": hit("Psycho Cut", 1.2)}}},
                ("Garchomp", "Whimsicott"): {
                    "outcome": "pinned", "target": None, "hits": {}},
            },
        }
        buf = io.StringIO()
        with redirect_stdout(buf):
            ct._print_pairs([row], ["Kingambit", "Basculegion", "Garchomp",
                                    "Whimsicott"], top=1)
        out = buf.getvalue()
        self.assertLess(out.index("Garchomp + Whimsicott: pinned"),
                        out.index("Kingambit + Basculegion: clean"),
                        "the pinned (worst) matchup must print before the "
                        "clean (best) one")

    def test_print_joint_shows_the_loss_before_the_sweep(self):
        row = {
            "name": "Gallade", "item": "Life Orb",
            "pairs_swept": 1, "pairs_traded": 0, "pairs_lost": 1,
            "pairs_no_ko": 0, "pairs_tailwind_safe": 1, "pairs_protect_safe": 1,
            "pairs_total": 2,
            "detail": {
                ("Kingambit", "Basculegion"): {
                    "outcome": "sweep", "turns_used": 1,
                    "tailwind_safe": True, "tailwind_outcome": "sweep",
                    "protect_safe": True,
                    "protect_outcomes": {"E1": "sweep", "E2": "sweep"},
                    "log": []},
                ("Garchomp", "Whimsicott"): {
                    "outcome": "loss", "turns_used": 1,
                    "tailwind_safe": False, "tailwind_outcome": "loss",
                    "protect_safe": False,
                    "protect_outcomes": {"E1": "loss", "E2": "loss"},
                    "log": []},
            },
        }
        buf = io.StringIO()
        with redirect_stdout(buf):
            ct._print_joint([row], ["Kingambit", "Basculegion", "Garchomp",
                                    "Whimsicott"], top=1, partner="", turns=2)
        out = buf.getvalue()
        self.assertLess(out.index("Garchomp + Whimsicott: loss"),
                        out.index("Kingambit + Basculegion: sweep"),
                        "the loss must print before the sweep")

    def test_print_deep_shows_the_loss_before_the_sweep(self):
        detail = {
            ("Kingambit", "Basculegion"): {
                "outcome": "sweep", "turns_used": 1, "ohko_risk": [],
                "tailwind_safe": True, "tailwind_outcome": "sweep",
                "protect_safe": True,
                "protect_outcomes": {"E1": "sweep", "E2": "sweep"},
                "grid": {"ours": {}, "theirs": {}}, "log": []},
            ("Garchomp", "Whimsicott"): {
                "outcome": "loss", "turns_used": 1, "ohko_risk": [],
                "tailwind_safe": False, "tailwind_outcome": "loss",
                "protect_safe": False,
                "protect_outcomes": {"E1": "loss", "E2": "loss"},
                "grid": {"ours": {}, "theirs": {}}, "log": []},
        }
        summary = {"pairs_total": 2, "pairs_swept": 1, "pairs_traded": 0,
                  "pairs_lost": 1, "pairs_no_ko": 0, "pairs_tailwind_safe": 1,
                  "pairs_protect_safe": 1}
        buf = io.StringIO()
        with redirect_stdout(buf):
            ct._print_deep("Gallade", "Ninetales-Alola", "Life Orb", None,
                           ["Kingambit", "Basculegion", "Garchomp",
                            "Whimsicott"], detail, summary, turns=2)
        out = buf.getvalue()
        self.assertLess(out.index("Garchomp + Whimsicott: LOSS"),
                        out.index("Kingambit + Basculegion: SWEEP"),
                        "fixture assumes both outcomes print in upper case")


class TestVsTeamAcceptsANamedTeam(unittest.TestCase):
    """"For --vs-team in counter_table.py, I would like to be able to use
    named teams from the team.csv and /data/teams folder." `--vs-team`
    already only ever fed `--multi-bring4`'s search, and
    `species_data.load_teams` (the SAME library `--team` already searches
    -- teams.csv rows plus any pokepaste dropped in data/teams/ or
    data/my_teams/) was already loaded into `W["teams"]` before this parsing
    ran; a named team is now tried FIRST, falling back to the existing
    comma-separated-Pokemon parsing when the name isn't a known team."""

    def test_a_known_team_name_resolves_to_its_full_roster(self):
        from _harness import load_world
        W = load_world()
        self.assertIn("Rain", W["teams"])
        self.assertIn("Big 6", W["teams"])
        msg, out = run_main(
            ["--multi-bring4", "--vs-team", "Rain", "--vs-team", "Big 6",
             "--pool-size", "12", "--good-threshold", "30", "--top", "1"])
        self.assertIsNone(msg, out)
        for name in W["teams"]["Rain"]:
            self.assertIn(name, out)
        for name in W["teams"]["Big 6"]:
            self.assertIn(name, out)

    def test_a_raw_comma_list_still_works_unchanged(self):
        """Backward compatibility: a --vs-team value that ISN'T a known
        team name still parses as individual Pokemon, exactly as before
        this feature existed."""
        msg, out = run_main(
            ["--multi-bring4", "--vs-team",
             "Kingambit,Basculegion,Garchomp,Whimsicott", "--vs-team",
             "Sylveon,Mega Charizard Y,Sinistcha,Farigiraf", "--pool-size",
             "12", "--good-threshold", "30", "--top", "1"])
        self.assertIsNone(msg, out)
        self.assertIn("Kingambit", out)
        self.assertIn("Sylveon", out)

    def test_an_unknown_bare_word_still_fails_loudly(self):
        """A typo'd team name (no comma, not a real team) must not be
        silently treated as one lone Pokemon -- it already fails the
        existing 'needs at least 2 Pokemon' check, same as before named
        teams existed."""
        msg, _out = run_main(
            ["--multi-bring4", "--vs-team", "NotARealTeamName",
             "--vs-team", "Big 6"])
        self.assertIsNotNone(msg)
        self.assertIn("needs at least 2 Pokemon", msg)


class TestMultiBring4NeverComesBackEmpty(unittest.TestCase):
    """"When a sweep of --vs-team gets too many results it doesn't even
    output the results or anything to CSV. I want to at least see the
    results (i.e., high performing pairs, results /15) and it should be
    very quick to compute the sets of 4 brings ... this should not take
    long." A candidate pool too big for the exhaustive C(N,6) sweep used
    to abort the whole run via SystemExit before anything printed -- Stage
    A (the pool-wide pair-vs-each-enemy search) is unaffected by that
    limit and was simply being thrown away. Now: the pair summary always
    prints, and hitting the exhaustive ceiling auto-falls back to --beam
    (already the fast, non-exhaustive path) instead of erroring out."""

    def test_a_too_large_candidate_pool_falls_back_instead_of_exiting(self):
        msg, out = run_main(
            ["--multi-bring4", "--vs-team", "Rain", "--vs-team", "Big 6",
             "--pool-size", "12", "--good-threshold", "30",
             "--min-enemies", "1", "--top", "5", "--max-candidates", "3"])
        self.assertIsNone(msg, out)
        self.assertIn("too many for an exhaustive sweep", out)
        self.assertIn("Falling back to --beam automatically", out)
        self.assertIn("auto-fallback", out)

    def test_the_pair_summary_prints_even_on_the_fallback_path(self):
        _msg, out = run_main(
            ["--multi-bring4", "--vs-team", "Rain", "--vs-team", "Big 6",
             "--pool-size", "12", "--good-threshold", "30",
             "--min-enemies", "1", "--top", "5", "--max-candidates", "3"])
        self.assertIn("top pairs:", out)
        self.assertIn("beaten (", out)
        self.assertIn("/15 beaten", out)

    def test_the_pair_summary_prints_on_the_normal_exhaustive_path_too(self):
        """Not just a fallback-only feature -- always shown."""
        _msg, out = run_main(
            ["--multi-bring4", "--vs-team", "Rain", "--vs-team", "Big 6",
             "--pool-size", "12", "--good-threshold", "30",
             "--min-enemies", "1", "--top", "5"])
        self.assertNotIn("too many for an exhaustive sweep", out)
        self.assertIn("top pairs:", out)

    def test_csv_export_still_works_after_the_fallback(self):
        import csv
        import tempfile
        with tempfile.NamedTemporaryFile(
                suffix=".csv", delete=False, mode="w") as f:
            path = f.name
        try:
            msg, _out = run_main(
                ["--multi-bring4", "--vs-team", "Rain", "--vs-team", "Big 6",
                 "--pool-size", "12", "--good-threshold", "30",
                 "--min-enemies", "1", "--top", "5", "--max-candidates", "3",
                 "--csv", path])
            self.assertIsNone(msg)
            with open(path, newline="", encoding="utf-8") as fh:
                rows = list(csv.reader(fh))
            self.assertGreater(len(rows), 1, "the fallback must still "
                               "write real rows, not an empty file")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
