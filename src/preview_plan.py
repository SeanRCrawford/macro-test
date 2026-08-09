"""Team preview: their six are visible, their four are not. What do I bring?

This is the decision the whole pipeline exists to support, and it is the one
moment you actually get to make it. You see their six at preview, you commit to
four of yours and a lead pair, and only then do you learn what they brought.

`plan_against` runs exactly the analysis the overnight search runs for one
pairing, at whatever effort is asked for, and returns the COMMITTED choice:
one bring-4, lead first, plus its record across their configurations and the
brings of theirs that beat it.

Live in the app rather than only in a batch file because the batch run answers
this for the teams you generated last night, and the question you have at
preview is about the team in front of you now.
"""
from matchup_search import search_robust_composition
from search_effort import tier


def plan_against(our6, their6, merged, moves_db, natures, typechart,
                 effort="standard", our_sets=None, enemy_sets=None,
                 max_turns=None, audit_all=None):
    """The committed plan against `their6`, and what beats it.

    `audit_all` defaults to whatever the tier says. Forcing it on makes the
    record a true X-of-90; leaving it off samples their likeliest leads and
    assumes their backs, which is faster and less trustworthy -- the returned
    `of` says which you got, so the caller never has to guess.
    """
    settings = tier(effort)
    all_configs = (settings.get("all_configs", False)
                   if audit_all is None else bool(audit_all))
    results = search_robust_composition(
        list(our6), list(their6), merged, moves_db, natures, typechart,
        max_turns=max_turns or settings["turns"] or 10,
        our_sets=our_sets, enemy_sets=enemy_sets,
        verify_top=settings["verify_top"],
        rate_robustness=settings["robustness"],
        robustness_leads=settings["leads"] or 1,
        robustness_turns=settings["turns"] or 1,
        audit_all_configs=all_configs)
    return committed(results)


def committed(results):
    """Pick the one bring to commit to, from a search's candidate list.

    Most wins across their audited configurations, ties broken on lower punish
    -- the same rule the workbook's Plan sheet applies. That sheet reads cached
    JSON while this reads live search results, so the logic cannot simply be
    shared; it is kept short and identical instead, and both are tested.
    """
    best = None
    for rec in results or []:
        lines = rec.get("audit") or []
        if not lines:
            continue
        wins = sum(1 for ln in lines
                   if (ln.get("outcome") or "").lower() == "win")
        punish = sum(ln.get("mean_exploitability") or 0.0
                     for ln in lines) / len(lines)
        rank = (-wins, punish)
        if best is None or rank < best[0]:
            best = (rank, rec, wins, len(lines), punish)
    if best is None:
        # Nothing was audited (quick tier). The screen still ranked the brings,
        # so report its pick rather than nothing -- flagged as unaudited.
        top = (results or [None])[0]
        if not top:
            return None
        return {"bring": top.get("our_bring4"), "wins": None, "of": None,
                "punish": None, "losing_to": [], "audited": False,
                "solver_wins": top.get("solver_wins"),
                "solver_total": top.get("solver_total")}

    _rank, rec, wins, total, punish = best
    lines = rec.get("audit") or []
    return {
        "bring": rec.get("our_bring4"),
        "wins": wins,
        "of": total,
        "punish": punish,
        "severe_turns": rec.get("severe_turns"),
        "adjusted": sum(ln.get("adjusted_value") or 0.0 for ln in lines),
        "losing_to": [" / ".join(ln.get("enemy_bring") or ln.get("lead") or [])
                      for ln in lines
                      if (ln.get("outcome") or "").lower() != "win"],
        "lines": lines,
        "audited": True,
        "solver_wins": rec.get("solver_wins"),
        "solver_total": rec.get("solver_total"),
    }
