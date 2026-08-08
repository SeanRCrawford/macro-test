"""Rate a bring by how punishable its best line is (design doc 2p, 6a, 3c).

`search_robust_composition` ranks by wins against `greedy_opponent_joint_action`
-- a policy this codebase wrote itself. That rewards exploiting a known-weak
opponent, which is the opposite of the question "would this team hold up against
a strong player". A bring can beat 90/90 of their configurations and still be
one a good player dismantles, because the simulated opponent never tries the
punish.

This rates a bring the way the aim implies:

    against the leads they would PLAUSIBLY bring (section 6a, usage-weighted
    rather than all 90 uniformly), play our best line and measure how much a
    BEST-RESPONDING opponent gains from it, turn by turn (section 3c).

Lower is better. The output is not a win count -- it is "how much does a good
player get for free against this team, and on which turn".

Cost note: exploitability needs a full payoff matrix per turn, so this belongs
on the verify stage for a handful of candidates, never on the 90-configuration
screen. `search_robust_composition` keeps its cheap screen; this refines the
survivors.
"""
from dataclasses import dataclass, field

from preview import DEFAULT_TAU, ranked_brings
from robustness import line_report


@dataclass
class BringRating:
    our_bring: list
    per_lead: list = field(default_factory=list)   # (lead, probability, LineReport)

    @property
    def weighted_exploitability(self):
        """Plausibility-weighted mean exploitability. The headline number.

        Weighted rather than averaged because a lead no good player would pick
        should not drag a team's rating down -- that was the flaw in
        worst-case-over-90 (section 6a).
        """
        total = sum(p for _lead, p, _rep in self.per_lead)
        if total <= 0:
            return 0.0
        return sum(p * rep.mean_exploitability
                   for _lead, p, rep in self.per_lead) / total

    @property
    def robust_win_rate(self):
        """P(win) against an opponent who punishes correctly EVERY turn.

        The number the aim actually asks for. Not the win count against our own
        bot, and not inferred from a points total: each audited line is played
        out against the opponent's EQUILIBRIUM reply at every turn, and this is
        the share of those lines we won, weighted by how plausible each of their
        leads is (section 6a).

        Their equilibrium reply, not the best response to our revealed move --
        see robustness.line_report for why the clairvoyant version is useless.

        Strict by construction: hitting the turn cap with both sides alive
        counts as NOT a win, because a line that cannot close is not a win.
        """
        total = sum(p for _lead, p, _rep in self.per_lead)
        if total <= 0:
            return 0.0
        return sum(p for _lead, p, rep in self.per_lead if rep.won) / total

    @property
    def adjusted_win_rate(self):
        """Wins, discounted by how punishable the winning line was.

        The headline number, and the one to rank on. A win on a line where a
        good player can take a whole Pokemon off you on some turn is much
        closer to a coin flip than a win on a line with nothing to answer, so
        counting both as "1 win" throws away the distinction the whole engine
        exists to measure.

            per line:  0                        if the line did not win
                       1 - mean / KO_WEIGHT     if it did, floored at 0

        where `mean` is the average exploitability across the line's turns, so
        a win that gives away a whole Pokemon per turn on average is discounted
        to nothing and an unpunishable win keeps its full weight. Averaged over
        their plausible leads, weighted by lead plausibility.

        MEAN rather than the worst single turn, which was tried first and is
        degenerate: worst turns routinely exceed 180 points, so every win
        discounted to exactly zero and the column carried no information. The
        mean is also the fairer question -- one unavoidable spike is not the
        same as bleeding ground every turn.

        The scale is an explicit assumption, not a measurement: KO_WEIGHT is
        the engine's own price of a Pokemon, which makes the discount
        interpretable ("how much of a Pokemon can they win back") rather than
        a tuned constant. Compare teams with it; do not read it as P(win).
        """
        from solver import KO_WEIGHT
        total = sum(p for _lead, p, _rep in self.per_lead)
        if total <= 0:
            return 0.0
        acc = 0.0
        for _lead, p, rep in self.per_lead:
            if not rep.won:
                continue
            penalty = max(0.0, rep.mean_exploitability) / KO_WEIGHT
            acc += p * max(0.0, 1.0 - penalty)
        return acc / total

    @property
    def reliable_wins(self):
        """Lines that win AND are hard to punish, as a share of plausible leads.

        A win on a line a good player can swing by half a Pokemon per turn is
        closer to a coin flip than a win. This counts only the wins whose worst
        turn stays under the severe threshold.
        """
        from robustness import SEVERE
        total = sum(p for _lead, p, _rep in self.per_lead)
        if total <= 0:
            return 0.0
        good = sum(p for _lead, p, rep in self.per_lead
                   if rep.won and rep.severe_count == 0)
        return good / total

    @property
    def outcomes(self):
        """{"win": w, "loss": l, "draw": d, "unresolved": u} over audited leads."""
        counts = {"win": 0, "loss": 0, "draw": 0, "unresolved": 0}
        for _lead, _p, rep in self.per_lead:
            counts[rep.outcome] = counts.get(rep.outcome, 0) + 1
        return counts

    @property
    def worst_lead(self):
        """The plausible lead this bring handles worst -- what to fix first."""
        if not self.per_lead:
            return None
        return max(self.per_lead, key=lambda t: t[2].mean_exploitability)

    @property
    def severe_turns(self):
        return sum(rep.severe_count for _lead, _p, rep in self.per_lead)

    @property
    def total_turns(self):
        return sum(len(rep.turns) for _lead, _p, rep in self.per_lead)


def plausible_leads(our_margins, labels, top_k=4, tau=DEFAULT_TAU):
    """The enemy leads worth auditing against, most likely first."""
    return ranked_brings(our_margins, labels, tau=tau)[:top_k]


def rate_bring(our4, leads, build_battle, choose, max_turns=6):
    """Audit `our4`'s line against each plausible enemy lead.

    `build_battle(our4, enemy4) -> (battle, movesets)` and
    `choose(battle) -> joint action` are injected so this module stays free of
    dataset and solver-configuration concerns, and so the same rating can audit
    any policy.
    """
    rating = BringRating(our_bring=list(our4))
    for lead, probability in leads:
        battle, movesets = build_battle(our4, lead)
        report = line_report(battle, movesets, choose, max_turns=max_turns)
        if report.turns:
            rating.per_lead.append((lead, probability, report))
    return rating


def rank_bringings(ratings):
    """Best-first: least punishable by a good player."""
    return sorted(ratings, key=lambda r: r.weighted_exploitability)
