"""`team_sheet.encode_teamsheet`/`decode_teamsheet` -- a single-cell,
copy-pasteable stand-in for a whole team.json, for "export any of the
teamsheets generated in the counter_table.py .xlsx to the streamlit app,
maybe with a base64 encoded pokepaste in an excel cell."
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestTeamsheetTokenRoundTrips(unittest.TestCase):
    def test_pool_and_sets_round_trip_exactly(self):
        from team_sheet import decode_teamsheet, encode_teamsheet
        pool = ["Incineroar", "Gallade"]
        sets = {"Gallade": {"item": "Life Orb", "moves": ["Psycho Cut"]}}
        token = encode_teamsheet(pool, sets)
        out_pool, out_sets = decode_teamsheet(token)
        self.assertEqual(out_pool, pool)
        self.assertEqual(out_sets, sets)

    def test_token_carries_the_documented_prefix(self):
        from team_sheet import TEAMSHEET_TOKEN_PREFIX, encode_teamsheet
        token = encode_teamsheet(["Incineroar"], {})
        self.assertTrue(token.startswith(TEAMSHEET_TOKEN_PREFIX))

    def test_token_is_a_single_line_safe_for_one_cell(self):
        from team_sheet import encode_teamsheet
        token = encode_teamsheet(
            ["Incineroar", "Gallade", "Whimsicott"],
            {"Gallade": {"item": "Life Orb",
                        "moves": ["Psycho Cut", "Sacred Sword"]}})
        self.assertNotIn("\n", token)

    def test_no_sets_defaults_to_empty_dict(self):
        from team_sheet import decode_teamsheet, encode_teamsheet
        token = encode_teamsheet(["Incineroar"])
        _pool, sets = decode_teamsheet(token)
        self.assertEqual(sets, {})

    def test_missing_prefix_is_rejected(self):
        from team_sheet import decode_teamsheet
        with self.assertRaises(ValueError):
            decode_teamsheet("not a token at all")

    def test_malformed_base64_after_the_prefix_is_rejected(self):
        from team_sheet import TEAMSHEET_TOKEN_PREFIX, decode_teamsheet
        with self.assertRaises(ValueError):
            decode_teamsheet(TEAMSHEET_TOKEN_PREFIX + "!!!not base64!!!")

    def test_valid_base64_that_isnt_a_pool_dict_is_rejected(self):
        import base64
        from team_sheet import TEAMSHEET_TOKEN_PREFIX, decode_teamsheet
        garbage = TEAMSHEET_TOKEN_PREFIX + base64.urlsafe_b64encode(
            b'{"not_pool": []}').decode("ascii")
        with self.assertRaises(ValueError):
            decode_teamsheet(garbage)


if __name__ == "__main__":
    unittest.main()
