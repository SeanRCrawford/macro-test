"""`--top-leads N`: audit N leads and every back behind them.

Asked for: "a stage 2 command to run viable leads/backs rather than all 90, so
I don't miss the best one, whether there are 12 to check or 24."

`--brings N` cannot express that, because it is a flat count over a ranking
that is not flat. The screener reads the LEAD pair, so the six configurations
sharing a lead score IDENTICALLY, and the tie groups measured across three
pairings are 6, 6, 6, 6 -- one per lead. A count of 3 therefore audits ONE lead
with an arbitrary three of its six backs, and a count of 12 audits two leads.

Counting LEADS instead lands on a boundary every time: 6, 12, 18, 24
configurations for N = 1, 2, 3, 4 -- exactly the "12 or 24" granularity asked
for, chosen by the data rather than by guessing a count.

It also fixes the miss. On the reference pairing exactly one of the 90
configurations beats all three of their hardest leads, and it sits at screen
rank 17: `--brings 12` misses it, three leads (18 configurations) contains it.

WHAT WAS TRIED AND REJECTED, so neither is retried:

  * A VIABILITY BAND on the screen score. The distribution is bimodal -- one
    tie group at +107 then a dense cluster from -212 to -240 -- and the winner
    is in the lower mode. The band needed to include it is 322 points wide,
    which admits 18 of 90; the same 18 that three leads gives, but only
    knowable after the fact. Counting leads gets there without circularity.
  * RANKING BY THE OPENING MAXIMIN instead of the screen margin, which was the
    promising-looking lever recorded in WORKFLOW 0h. Measured: it puts the
    winner at rank 60, against 17 for the screen margin, and misses at every
    setting tested. The opening maximin is the right statistic for "is this
    team hopeless" and the wrong one for "which of our brings should be
    audited".
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

ROOT = os.path.join(os.path.dirname(__file__), "..")


class TestTheSelectionRule(unittest.TestCase):
    """The rule itself, on a synthetic ranking -- no battles, so it pins the
    LOGIC rather than the dataset."""

    def select(self, scored, top_leads):
        """Mirrors search_robust_composition's block."""
        order, seen = [], []
        for d in scored:
            lead = tuple(d["our_bring4"][:2])
            if lead not in seen:
                if len(seen) >= top_leads:
                    break
                seen.append(lead)
            order.append(d)
        return order

    def fake(self):
        """Three leads, six backs each, already in screen order."""
        out = []
        for lead in ("A", "B", "C"):
            for back in range(6):
                out.append({"our_bring4": [lead, "x", f"b{back}", "y"]})
        return out

    def test_one_lead_takes_all_six_of_its_backs(self):
        self.assertEqual(len(self.select(self.fake(), 1)), 6)

    def test_three_leads_take_eighteen(self):
        self.assertEqual(len(self.select(self.fake(), 3)), 18)

    def test_it_stops_on_a_lead_boundary(self):
        for n in (1, 2, 3):
            picked = self.select(self.fake(), n)
            self.assertEqual(len({tuple(d["our_bring4"][:2]) for d in picked}), n)

    def test_asking_for_more_leads_than_exist_is_not_an_error(self):
        self.assertEqual(len(self.select(self.fake(), 99)), 18)

    def test_the_order_within_a_lead_is_preserved(self):
        """The back tie-break already sorted these; the lead cut must not
        reshuffle them."""
        picked = self.select(self.fake(), 1)
        self.assertEqual([d["our_bring4"][2] for d in picked],
                         [f"b{i}" for i in range(6)])


class TestItIsWiredUp(unittest.TestCase):

    def test_the_search_takes_the_argument(self):
        import inspect

        import matchup_search
        self.assertIn("top_leads", inspect.signature(
            matchup_search.search_robust_composition).parameters)

    def test_it_never_narrows_below_the_tier(self):
        """It exists to WIDEN the audit. A roster with few distinct leads must
        not end up auditing less than --brings asked for."""
        import inspect

        import matchup_search
        src = inspect.getsource(matchup_search.search_robust_composition)
        self.assertIn("verify_top = max(verify_top, len(order))", src)

    def test_the_cli_exposes_it(self):
        src = open(os.path.join(ROOT, "tools", "search_teams.py"),
                   encoding="utf-8").read()
        self.assertIn('"--top-leads"', src)

    def test_it_is_part_of_the_cache_key(self):
        """It changes the answer, so a run with it must not be served results
        computed without it."""
        src = open(os.path.join(ROOT, "tools", "search_teams.py"),
                   encoding="utf-8").read()
        key = src[src.index("ResultCache.key("):]
        self.assertIn("args.top_leads", key[:400])

    def test_the_batch_file_parses_and_forwards_it(self):
        import re
        bat = open(os.path.join(ROOT, "overnight.bat"), encoding="utf-8").read()
        parsed = set(re.findall(r'if /i "%~1"=="(--[a-z0-9-]+)"', bat))
        self.assertIn("--top-leads", parsed)
        stage2 = bat[bat.index("search_teams.py"):][:400]
        self.assertIn("%TOPLEADS%", stage2)

    def test_the_batch_header_documents_it(self):
        bat = open(os.path.join(ROOT, "overnight.bat"), encoding="utf-8").read()
        header = bat[:bat.index(":parse")]
        self.assertIn("--top-leads", header)


class TestTheCost(unittest.TestCase):
    """18 configurations is affordable, which is the whole argument for
    preferring it to `--brings 90`."""

    def test_three_leads_at_standard_is_a_few_minutes(self):
        import time_estimate as te
        self.assertLess(te.tier_seconds("standard", brings=18), 300)

    def test_it_is_much_cheaper_than_all_ninety(self):
        import time_estimate as te
        self.assertLess(te.tier_seconds("thorough", brings=18),
                        te.tier_seconds("thorough", brings=90) / 3)


if __name__ == "__main__":
    unittest.main()
