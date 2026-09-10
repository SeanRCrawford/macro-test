"""`tools/_harness.py`'s `load_world()` caching.

    "Find a way to make the golden baseline much faster - it takes far too
    long, especially for small changes."

`build_merged_dataset()` (parses mbsmogon.xlsx/roster.csv) is the real cost
in `load_world()` -- `_dataset_only()` caches its result to disk, keyed by
a cheap `os.stat`-based fingerprint of those two files, so a repeat call (or
a repeat PROCESS, e.g. `golden_baseline.py` run again right after an
unrelated source-code edit) with unchanged data skips the parse entirely.

`load_teams()` is deliberately NOT part of this cache -- its own
data/teams/*.txt and data/my_teams/*.txt folders are legitimately mutable
DURING a run (the Streamlit app's own "Save to My Teams" button writes
there, and this repo's own tests exercise that same flow), so every
`load_world()` call re-derives `teams`/`meta` fresh regardless of the
dataset cache's own hit/miss state -- see the module-level comment in
`tools/_harness.py` for the concrete bug this avoided: a cache spanning
`load_teams()` too could reload a set of teams from BEFORE or AFTER a
transient file appeared/disappeared, silently disagreeing with a
freshly-computed comparison in the same test run.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import _harness  # noqa: E402


class TestDatasetCacheCorrectness(unittest.TestCase):
    """A cache hit must be byte-identical to a fresh (uncached) build -- the
    whole point is a repeat call SKIPS work, never returns something
    different."""

    def setUp(self):
        if os.path.exists(_harness._CACHE_PATH):
            os.unlink(_harness._CACHE_PATH)

    def tearDown(self):
        if os.path.exists(_harness._CACHE_PATH):
            os.unlink(_harness._CACHE_PATH)

    def test_a_cache_hit_matches_a_fresh_build(self):
        fresh = _harness.build_merged_dataset()
        self.assertFalse(os.path.exists(_harness._CACHE_PATH))
        cached_miss = _harness._dataset_only()  # writes the cache
        self.assertTrue(os.path.exists(_harness._CACHE_PATH))
        cached_hit = _harness._dataset_only()  # now a real cache hit
        self.assertEqual(fresh, cached_miss)
        self.assertEqual(fresh, cached_hit)

    def test_load_world_matches_a_from_scratch_call_on_a_cache_hit(self):
        merged, _usage, moves, natures, typechart = _harness.build_merged_dataset()
        teams, meta = _harness.load_teams(with_meta=True, merged=merged)
        expected = dict(merged=merged, moves=moves, natures=natures,
                        typechart=typechart, teams=teams, meta=meta)
        _harness.load_world()  # cache miss, writes the cache
        got = _harness.load_world()  # cache hit
        self.assertEqual(expected, got)

    def test_a_second_call_actually_skips_the_parse(self):
        """Not just "produces the same answer" -- confirms the cache hit
        path never calls `build_merged_dataset` again at all."""
        from unittest.mock import patch
        _harness._dataset_only()  # cache miss, writes the cache
        with patch.object(_harness, "build_merged_dataset") as spy:
            _harness._dataset_only()
        spy.assert_not_called()


class TestFingerprintInvalidation(unittest.TestCase):
    """The cache must never serve a stale answer once mbsmogon.xlsx/
    roster.csv actually change -- `_fingerprint()` is what notices."""

    def setUp(self):
        if os.path.exists(_harness._CACHE_PATH):
            os.unlink(_harness._CACHE_PATH)

    def tearDown(self):
        if os.path.exists(_harness._CACHE_PATH):
            os.unlink(_harness._CACHE_PATH)

    def test_fingerprint_covers_exactly_the_two_dataset_files(self):
        fp = _harness._fingerprint()
        paths = {os.path.basename(p) for p, *_ in fp}
        self.assertEqual(paths, {"mbsmogon.xlsx", "roster.csv"})

    def test_fingerprint_changes_are_detected_and_content_still_matches(self):
        w1 = _harness._dataset_only()
        fp1 = _harness._fingerprint()

        path = os.path.join(_harness._DATA_DIR, "roster.csv")
        st = os.stat(path)
        try:
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1))
            fp2 = _harness._fingerprint()
            self.assertNotEqual(fp1, fp2)
            w2 = _harness._dataset_only()  # must rebuild, not serve fp1's cache
            self.assertEqual(w1, w2, "content is unchanged, only mtime bumped "
                             "-- the rebuilt result must still match")
        finally:
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


class TestTeamsAlwaysFresh(unittest.TestCase):
    """The regression this module's own docstring documents: `load_teams`
    must run fresh on EVERY `load_world()` call, never served from the
    dataset cache -- a file appearing in data/my_teams/ between two calls
    must be visible on the very next call, cache hit or not."""

    def setUp(self):
        self.tmp_path = os.path.join(_harness._DATA_DIR, "my_teams",
                                     "harness_cache_probe.txt")
        if os.path.exists(self.tmp_path):
            os.unlink(self.tmp_path)

    def tearDown(self):
        if os.path.exists(self.tmp_path):
            os.unlink(self.tmp_path)

    def test_a_new_pasted_team_is_visible_on_the_very_next_call_even_on_a_cache_hit(self):
        w1 = _harness.load_world()
        self.assertNotIn("Harness Cache Probe", w1["teams"])

        with open(self.tmp_path, "w", encoding="utf-8") as fh:
            fh.write("Garchomp\n"
                    "Ability: Rough Skin\n"
                    "EVs: 252 Atk / 4 SpD / 252 Spe\n"
                    "Jolly Nature\n"
                    "- Earthquake\n"
                    "- Dragon Claw\n"
                    "- Stealth Rock\n"
                    "- Protect\n")
        # `_dataset_only()`'s own cache (mbsmogon.xlsx/roster.csv, untouched
        # here) is now warm from `w1` above -- this call is a cache HIT for
        # the dataset, but `load_teams` must still see the new file.
        w2 = _harness.load_world()
        self.assertIn("Harness Cache Probe", w2["teams"])

        os.unlink(self.tmp_path)
        w3 = _harness.load_world()
        self.assertNotIn("Harness Cache Probe", w3["teams"])


if __name__ == "__main__":
    unittest.main()
