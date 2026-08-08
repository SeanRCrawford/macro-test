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
