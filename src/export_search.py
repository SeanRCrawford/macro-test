"""Turn a team-search cache into a workbook you can actually read.

The search prints a ranked table and stores its results as JSON. Neither is a
good way to answer the question a player has after an overnight run, which is
never "what is the number" but "WHY is it that number, and what do I change".
Answering that needs the losing turns themselves, so this exports four sheets
at descending zoom:

  Teams      one row per team: mean exploitability across its matchups
  Matchups   one row per (team vs opponent): the bring that rated best
  Candidates every bring that was audited, not just the winner -- the second
             place bring is the one you switch to, so it has to be visible
  Turns      every audited turn: our play, their punish, and the gap

The Turns sheet is the transparency piece and the reason the search records
per-turn detail at all: a rating you cannot trace back to a specific turn where
a specific play gets answered by a specific move is a number to take on faith.

Reads the cache dict written by tools/search_teams.py. Rows from a `quick`-tier
run carry no audit, and are exported with blank rating columns rather than
being dropped -- "this pairing was searched but not rated" is information.
"""
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill(start_color="2C3E50", end_color="2C3E50", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)
GOOD_FILL = PatternFill(start_color="D5F5E3", end_color="D5F5E3", fill_type="solid")
BAD_FILL = PatternFill(start_color="FADBD8", end_color="FADBD8", fill_type="solid")
WARN_FILL = PatternFill(start_color="FCF3CF", end_color="FCF3CF", fill_type="solid")

# Matches robustness.SEVERE: a best response gaining more than this is a play
# worth reconsidering. Imported rather than restated where possible.
try:
    from robustness import SEVERE
except ImportError:                                   # pragma: no cover
    SEVERE = 60.0

MILD = SEVERE / 2.0


def _style_header(ws):
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"


def _autosize(ws):
    for i, col in enumerate(ws.columns, start=1):
        longest = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[get_column_letter(i)].width = min(max(longest + 2, 10), 55)


def _shade(cell, value):
    """Green / amber / red by how much a good player gains. Lower is better."""
    if value is None:
        return
    cell.fill = GOOD_FILL if value <= MILD else (WARN_FILL if value <= SEVERE else BAD_FILL)


def rows_of(cache_data):
    """The pairing records in a cache dict, oldest key order preserved."""
    return [v for v in cache_data.values()
            if isinstance(v, dict) and v.get("ours")]


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _teams_sheet(wb, rows):
    ws = wb.active
    ws.title = "Teams"
    ws.append(["Team", "Mean exploitability", "Worst matchup", "Worst value",
               "Severe turns", "Matchups rated", "Matchups searched"])
    _style_header(ws)

    by_team = {}
    for r in rows:
        by_team.setdefault(r["ours"], []).append(r)

    ranked = sorted(by_team.items(),
                    key=lambda kv: (_mean([x.get("exploitability") for x in kv[1]])
                                    if _mean([x.get("exploitability") for x in kv[1]])
                                    is not None else float("inf")))
    for name, rs in ranked:
        rated = [x for x in rs if x.get("exploitability") is not None]
        mean = _mean([x["exploitability"] for x in rated])
        worst = max(rated, key=lambda x: x["exploitability"]) if rated else None
        ws.append([name,
                   round(mean, 1) if mean is not None else None,
                   worst["theirs"] if worst else None,
                   round(worst["exploitability"], 1) if worst else None,
                   sum(x.get("severe_turns") or 0 for x in rs),
                   len(rated), len(rs)])
        _shade(ws.cell(ws.max_row, 2), mean)
        _shade(ws.cell(ws.max_row, 4), worst["exploitability"] if worst else None)
    _autosize(ws)


def _matchups_sheet(wb, rows):
    ws = wb.create_sheet("Matchups")
    ws.append(["Team", "Opponent", "Best bring (lead first)", "Exploitability",
               "Severe turns", "Their hardest lead", "Worst turn",
               "Our play", "Punished by", "Games won", "Games played"])
    _style_header(ws)
    for r in sorted(rows, key=lambda x: (x["ours"],
                                         -(x.get("exploitability") or -1))):
        wt = r.get("worst_turn") or {}
        ws.append([
            r["ours"], r["theirs"],
            " / ".join(r["bring"]) if r.get("bring") else None,
            round(r["exploitability"], 1) if r.get("exploitability") is not None else None,
            r.get("severe_turns"),
            " / ".join(r.get("hardest_lead") or []) or None,
            f"T{wt['turn']}" if wt.get("turn") else None,
            wt.get("our_play"), wt.get("punished_by"),
            r.get("solver_wins"), r.get("solver_total"),
        ])
        _shade(ws.cell(ws.max_row, 4), r.get("exploitability"))
    _autosize(ws)


def _candidates_sheet(wb, rows):
    """Every audited bring, so the runner-up is visible next to the winner."""
    ws = wb.create_sheet("Candidates")
    ws.append(["Team", "Opponent", "Rank", "Bring (lead first)", "Exploitability",
               "Severe turns", "Turns rated", "Screen worst margin",
               "Games won", "Games played"])
    _style_header(ws)
    any_rows = False
    for r in sorted(rows, key=lambda x: (x["ours"], x["theirs"])):
        for i, cand in enumerate(r.get("candidates") or [], start=1):
            any_rows = True
            ws.append([
                r["ours"], r["theirs"], i,
                " / ".join(cand.get("bring") or []) or None,
                round(cand["exploitability"], 1)
                if cand.get("exploitability") is not None else None,
                cand.get("severe_turns"), cand.get("rated_turns"),
                round(cand["worst_margin"], 1)
                if cand.get("worst_margin") is not None else None,
                cand.get("solver_wins"), cand.get("solver_total"),
            ])
            _shade(ws.cell(ws.max_row, 5), cand.get("exploitability"))
    if not any_rows:
        ws.append(["No per-candidate detail in this cache "
                   "(quick tier, or a run from before candidates were recorded)."])
    _autosize(ws)


def _turns_sheet(wb, rows):
    """Every audited turn. The evidence behind every number in the other sheets."""
    ws = wb.create_sheet("Turns")
    ws.append(["Team", "Opponent", "Bring (lead first)", "Their lead",
               "Lead likelihood", "Turn", "Exploitability", "Regret",
               "Equilibrium", "Our worst case", "Our play", "Their best answer"])
    _style_header(ws)
    any_rows = False
    for r in sorted(rows, key=lambda x: (x["ours"], x["theirs"])):
        for cand in r.get("candidates") or []:
            for lead in cand.get("audit") or []:
                for t in lead.get("turns") or []:
                    any_rows = True
                    ws.append([
                        r["ours"], r["theirs"],
                        " / ".join(cand.get("bring") or []) or None,
                        " / ".join(lead.get("lead") or []) or None,
                        round(lead["probability"], 3)
                        if lead.get("probability") is not None else None,
                        t.get("turn"),
                        round(t["exploitability"], 1)
                        if t.get("exploitability") is not None else None,
                        round(t["regret"], 1) if t.get("regret") is not None else None,
                        round(t["equilibrium"], 1)
                        if t.get("equilibrium") is not None else None,
                        round(t["worst_case"], 1)
                        if t.get("worst_case") is not None else None,
                        t.get("our_play"), t.get("punished_by"),
                    ])
                    _shade(ws.cell(ws.max_row, 7), t.get("exploitability"))
    if not any_rows:
        ws.append(["No per-turn detail in this cache "
                   "(the quick tier does not audit lines)."])
    _autosize(ws)


def _legend_sheet(wb):
    ws = wb.create_sheet("How to read this")
    ws.append(["Column", "What it means"])
    _style_header(ws)
    for row in [
        ("Exploitability",
         "Equilibrium value of the turn minus the worst case of the play we "
         "actually made. It is what a good player GAINS by answering us "
         "correctly. 0 = unpunishable. Lower is better. This is the ranking "
         "number, and it is not a win rate."),
        ("Regret",
         "How much better the safest available play would have been. "
         "Exploitability compares against equilibrium (which may require "
         "mixing); regret compares against the best single play, so it is the "
         "part that was avoidable by choosing differently."),
        ("Equilibrium",
         "Value of the turn if both sides play optimally. Points, not "
         "probability: a KO is worth roughly 180."),
        ("Our worst case",
         "Value of the play we chose against their best reply. Equilibrium "
         "minus this is the exploitability."),
        ("Severe turns",
         f"Turns where a best-responding opponent gains more than {SEVERE:g} "
         "points -- about a third of a Pokemon. These are the plays to change."),
        ("Lead likelihood",
         "How plausible that opening is for them, from the preview screen. "
         "Ratings are weighted by it so a lead no good player brings cannot "
         "drag a team down."),
        ("Games won / played",
         "The older win-count verification against our own scripted opponent. "
         "Kept for context only -- it is a biased measure of team strength, "
         "which is why the ranking does not use it."),
        ("Colours",
         f"Green up to {MILD:g}, amber up to {SEVERE:g}, red above."),
    ]:
        ws.append(list(row))
        ws.cell(ws.max_row, 2).alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 95
    return ws


def build_workbook(cache_data, out_path):
    """Write the workbook. Returns the number of pairing rows exported."""
    rows = rows_of(cache_data)
    wb = Workbook()
    _teams_sheet(wb, rows)
    _matchups_sheet(wb, rows)
    _candidates_sheet(wb, rows)
    _turns_sheet(wb, rows)
    _legend_sheet(wb)
    wb.save(out_path)
    return len(rows)
