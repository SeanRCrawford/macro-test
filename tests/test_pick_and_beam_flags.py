"""The flags the funnel actually needs, and the ones that only looked like they
existed.

Both came out of a recipe I wrote for screening ~1000 teams, in which two of
the flags were wrong:

  * `--beam-width` was documented in generate_overnight but never parsed by
    overnight.bat, so the batch file rejected the command outright.
  * `--pick "1-40"` was not a thing. The parser took comma-separated numbers
    only, which is unusable for a funnel whose whole point is "screen 1000,
    deep-search the top 40".

Both are fixed. The tests below also pin the thing that makes the recipe work
WITHOUT `--beam-width` at all: `--candidates` raises the beam width to match,
because you cannot rate more teams than the beam emits.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from search_teams import parse_pick  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
ORDER = [f"gen{i:02d}" for i in range(1, 51)]


class TestPickNumbers(unittest.TestCase):

    def test_a_list_of_numbers(self):
        self.assertEqual(parse_pick("4,10,12", ORDER),
                         ["gen04", "gen10", "gen12"])

    def test_spaces_are_ignored(self):
        self.assertEqual(parse_pick(" 4 , 10 ", ORDER), ["gen04", "gen10"])

    def test_empty_means_everything(self):
        self.assertIsNone(parse_pick("", ORDER))


class TestPickRanges(unittest.TestCase):

    def test_a_range_is_inclusive_at_both_ends(self):
        self.assertEqual(parse_pick("1-5", ORDER),
                         ["gen01", "gen02", "gen03", "gen04", "gen05"])

    def test_a_range_of_one(self):
        self.assertEqual(parse_pick("3-3", ORDER), ["gen03"])

    def test_ranges_and_singles_mix(self):
        self.assertEqual(parse_pick("1-3,10", ORDER),
                         ["gen01", "gen02", "gen03", "gen10"])

    def test_an_overlap_is_not_searched_twice(self):
        """Each duplicate is minutes of deep search, silently spent."""
        self.assertEqual(parse_pick("1-10,5", ORDER),
                         [f"gen{i:02d}" for i in range(1, 11)])

    def test_order_follows_the_spec_not_the_ranking(self):
        self.assertEqual(parse_pick("10,1-2", ORDER),
                         ["gen10", "gen01", "gen02"])


class TestPickRefusesNonsenseLoudly(unittest.TestCase):
    """Every one of these used to be either accepted or reported as something
    else. A funnel command that quietly picks the wrong teams wastes a night."""

    def bad(self, spec):
        with self.assertRaises(SystemExit) as caught:
            parse_pick(spec, ORDER)
        return str(caught.exception)

    def test_a_backwards_range(self):
        self.assertIn("backwards", self.bad("5-1"))

    def test_a_non_numeric_range(self):
        self.assertIn("N-M", self.bad("a-3"))

    def test_zero_is_out_of_range(self):
        """The ranking is 1-based; --pick 0 is a silent off-by-one otherwise."""
        self.assertIn("out of range", self.bad("0-3"))

    def test_past_the_end_names_the_limit(self):
        message = self.bad("1-999")
        self.assertIn("out of range", message)
        self.assertIn("1-50", message)

    def test_a_bare_word(self):
        self.assertIn("rank NUMBERS", self.bad("x"))


class TestTheBatchFileAcceptsWhatTheDocsPromise(unittest.TestCase):
    """It errors on an unknown argument rather than ignoring it, so a flag that
    is documented but not parsed fails the whole run."""

    @classmethod
    def setUpClass(cls):
        cls.bat = open(os.path.join(ROOT, "overnight.bat"),
                       encoding="utf-8").read()

    def parsed_flags(self):
        import re
        # Digits matter: --stage2-only. A class without them silently
        # captures "--stage" and reports a defect that is not there.
        return set(re.findall(r'if /i "%~1"=="(--[a-z0-9-]+)"', self.bat))

    def test_beam_width_is_parsed(self):
        self.assertIn("--beam-width", self.parsed_flags())

    def test_screen_vs_is_parsed(self):
        self.assertIn("--screen-vs", self.parsed_flags())

    def test_every_flag_the_header_documents_is_parsed(self):
        """The header is what a user reads before typing. A flag documented
        there and missing from the parser is a command that cannot run."""
        import re
        header = self.bat[:self.bat.index(":parse")]
        documented = set(re.findall(r"^rem   (--[a-z0-9-]+)", header, re.M))
        missing = sorted(documented - self.parsed_flags())
        self.assertEqual(missing, [], f"documented but not parsed: {missing}")

    def test_the_new_flags_reach_stage_1(self):
        stage1 = self.bat[self.bat.index("generate_overnight.py"):]
        for var in ("%BEAMW%", "%SCREENVS%", "%PILOT%"):
            self.assertIn(var, stage1[:900], var)


class TestCandidatesAlreadyWidensTheBeam(unittest.TestCase):
    """Why the 1000-team recipe needs no --beam-width: you cannot rate more
    teams than the beam emits, so --candidates raises it."""

    def test_the_beam_is_asked_for_at_least_candidates_teams(self):
        src = open(os.path.join(ROOT, "tools", "generate_overnight.py"),
                   encoding="utf-8").read()
        call = src[src.index("beam_search_teams("):][:300]
        self.assertIn("max(args.beam_width, args.candidates)", call)


if __name__ == "__main__":
    unittest.main()
