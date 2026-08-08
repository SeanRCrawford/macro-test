"""Generated teams must be searchable by name, and must not collide.

The pipeline hands generated rosters from generate_overnight.py to
search_teams.py through a JSON file. Two things can go wrong quietly:

  1. the shortlist shape changes and the reader silently yields nothing, so the
     deep search runs on library teams and reports a result that looks fine;
  2. two different rosters share a generated name ("gen01") across runs and the
     cache serves one for the other -- the exact failure that makes a cache
     worse than no cache.

Both are cheap to pin and expensive to discover by hand at 3am.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from search_effort import ResultCache  # noqa: E402


def _write(blob):
    path = os.path.join(tempfile.mkdtemp(), "shortlist.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(blob, fh)
    return path


class TestRosterChaining(unittest.TestCase):

    def setUp(self):
        # Imported lazily: search_teams pulls in the dataset-facing modules, so
        # import failures should surface as a test error, not a collection error.
        import search_teams
        self.load_rosters = search_teams.load_rosters

    def test_reads_the_plain_name_to_members_mapping(self):
        path = _write({"gen01": ["A", "B", "C", "D", "E", "F"]})
        self.assertEqual(self.load_rosters(path),
                         {"gen01": ["A", "B", "C", "D", "E", "F"]})

    def test_reads_the_record_list_shape_too(self):
        """generate_overnight's cache rows, so the two chain without conversion."""
        path = _write([{"name": "gen01", "team": ["A", "B"], "exploitability": 5},
                       {"name": "gen02", "team": ["C", "D"]}])
        self.assertEqual(self.load_rosters(path),
                         {"gen01": ["A", "B"], "gen02": ["C", "D"]})

    def test_reads_a_wrapped_teams_key(self):
        path = _write({"teams": {"gen01": ["A", "B"]}, "generated_at": "today"})
        self.assertEqual(self.load_rosters(path), {"gen01": ["A", "B"]})

    def test_records_without_a_team_are_skipped_not_crashed_on(self):
        path = _write([{"name": "gen01", "team": ["A"]}, {"name": "gen02"}])
        self.assertEqual(self.load_rosters(path), {"gen01": ["A"]})

    def test_same_name_different_roster_gets_a_different_cache_key(self):
        """The collision that would silently serve one team's result for another."""
        a = ResultCache.key("bring", 2, "gen01", "Rain", "thorough", 10, None,
                            ["A", "B", "C", "D", "E", "F"], None)
        b = ResultCache.key("bring", 2, "gen01", "Rain", "thorough", 10, None,
                            ["Z", "B", "C", "D", "E", "F"], None)
        self.assertNotEqual(a, b)

    def test_same_name_same_roster_resumes(self):
        roster = ["A", "B", "C", "D", "E", "F"]
        a = ResultCache.key("bring", 2, "gen01", "Rain", "thorough", 10, None,
                            roster, None)
        b = ResultCache.key("bring", 2, "gen01", "Rain", "thorough", 10, None,
                            list(roster), None)
        self.assertEqual(a, b)

    def test_a_library_team_key_is_unaffected_by_the_new_fields(self):
        """Library teams pass None for both rosters; the key must stay stable."""
        self.assertEqual(
            ResultCache.key("bring", 2, "NAIC", "Rain", "thorough", 10, None,
                            None, None),
            ResultCache.key("bring", 2, "NAIC", "Rain", "thorough", 10, None,
                            None, None))


if __name__ == "__main__":
    unittest.main()
