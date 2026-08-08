"""The threat matrix: who threatens who.

This is the formal counterpart of what the VGC literature calls **pressure**.
Aaron Traylor's definition is pairwise and directional -- "the threat of
proactive action is how I define pressure; i.e., Pikachu pressures Gyarados" --
so the object is an EDGE between two Pokemon, not a score for a position.

It is computed once per matchup and then read many times. Three consumers:

  1. Evaluation. Answer preservation (max-weight bipartite matching over
     `handles_score`) needs an edge weight for "how well does ours handle
     theirs".
  2. Action generation. Reactive actions -- Protect, switching, healing -- are
     only well defined against a specific threat ("reactive actions are ALWAYS
     in response to the threat of a specific proactive action"), so a live edge
     here is what justifies generating one.
  3. Opponent modelling. Pressure is the guide to what the opponent is likely
     to do, which is the principled way to seed a double-oracle restricted
     action set and to prior their columns.

Deliberately NOT a search: every entry is a damage calculation against the
current field, so building the whole matrix costs roughly one `run_turn`.

Joint pressure is first class. A per-pair matrix cannot express focus fire --
two attackers that individually 2HKO but together OHKO -- and "can both enemy
Pokemon work together to secure a knockout?" is one of the questions the
literature says to ask first. `joint_kos()` answers it.
"""
import math
from dataclasses import dataclass

from damage import damage_roll, effective_stat
from engine import effective_speed
from matching import max_weight_matching


@dataclass(frozen=True)
class Threat:
    """What `attacker` can do to `defender` right now, with its best move."""
    move: str | None
    dmg_avg: float          # fraction of the defender's MAX hp
    dmg_min: float
    dmg_max: float
    priority: int
    outspeeds: bool
    # Against the defender's CURRENT hp, which is what actually matters in play.
    ohko: bool              # kills even on the worst roll -- guaranteed
    ohko_possible: bool     # kills on the best roll -- a real risk, not a plan
    nhko: int               # hits needed at the average roll (99 if it cannot)

    @property
    def threatens(self) -> bool:
        """Applies pressure: a KO is on the table, now or next turn."""
        return self.ohko_possible or self.nhko <= 2


NO_THREAT = Threat(move=None, dmg_avg=0.0, dmg_min=0.0, dmg_max=0.0, priority=0,
                   outspeeds=False, ohko=False, ohko_possible=False, nhko=99)


def _best_attack(attacker, defender, movesets, typechart, field, battle):
    """The attacker's hardest-hitting known move against this defender."""
    best = None
    entries = movesets.get(attacker.name) or []
    for move, _usage in entries:
        if move.power == 0 or move.category == "Status":
            continue
        atk_key = "atk" if move.category == "Physical" else "spa"
        def_key = "def" if move.category == "Physical" else "spd"
        atk_stat = effective_stat(attacker.stats[atk_key], attacker.stages[atk_key])
        if attacker.item == "Choice Band" and atk_key == "atk":
            atk_stat *= 1.5
        if attacker.item == "Choice Specs" and atk_key == "spa":
            atk_stat *= 1.5
        def_stat = effective_stat(defender.stats[def_key], defender.stages[def_key])
        lo, hi, avg, _eff = damage_roll(50, move.power, atk_stat, def_stat,
                                        attacker, defender, move, typechart,
                                        weather=field.weather)
        if best is None or avg > best[3]:
            best = (move, lo, hi, avg)
    return best


def threat_between(attacker, defender, movesets, typechart, field, battle):
    """One edge of the matrix. Cheap enough to call in a loop."""
    if attacker.fainted or defender.fainted:
        return NO_THREAT
    best = _best_attack(attacker, defender, movesets, typechart, field, battle)
    if best is None:
        return NO_THREAT
    move, lo, hi, avg = best

    max_hp = defender.max_hp() or 1
    cur_hp = defender.current_hp
    atk_side = "p1" if battle.side_of(attacker) is battle.p1 else "p2"
    def_side = "p1" if battle.side_of(defender) is battle.p1 else "p2"

    return Threat(
        move=move.name,
        dmg_avg=avg / max_hp,
        dmg_min=lo / max_hp,
        dmg_max=hi / max_hp,
        priority=move.priority,
        outspeeds=(effective_speed(attacker, field, atk_side)
                   > effective_speed(defender, field, def_side)),
        ohko=(lo >= cur_hp),
        ohko_possible=(hi >= cur_hp),
        nhko=(math.ceil(cur_hp / avg) if avg > 0 else 99),
    )


class ThreatMatrix:
    """Pairwise and joint pressure between two rosters, in both directions."""

    def __init__(self, battle, movesets):
        self.battle = battle
        self.ours = [c for c in battle.p1.roster if not c.fainted]
        self.theirs = [c for c in battle.p2.roster if not c.fainted]
        self._pair = {}

        field, typechart = battle.field, battle.typechart
        for a in self.ours:
            for b in self.theirs:
                self._pair[(id(a), id(b))] = threat_between(
                    a, b, movesets, typechart, field, battle)
                self._pair[(id(b), id(a))] = threat_between(
                    b, a, movesets, typechart, field, battle)

    def threat(self, attacker, defender) -> Threat:
        return self._pair.get((id(attacker), id(defender)), NO_THREAT)

    def joint_kos(self, a1, a2, target, guaranteed: bool = False) -> bool:
        """Can `a1` and `a2` together remove `target` this turn?

        The focus-fire question a pairwise matrix cannot answer. `guaranteed`
        uses the worst roll for both attackers rather than the average.
        """
        if a1 is a2:
            return False
        t1, t2 = self.threat(a1, target), self.threat(a2, target)
        share = (t1.dmg_min + t2.dmg_min) if guaranteed else (t1.dmg_avg + t2.dmg_avg)
        needed = target.current_hp / (target.max_hp() or 1)
        return share >= needed

    def handles_score(self, ours, theirs) -> float:
        """How well does `ours` handle `theirs`? The bipartite matching weight.

        Positive means ours wins the interaction. Built from the two directed
        edges rather than damage alone, because "handles" is a comparison: the
        question is whether we remove them before they remove us.
        """
        out = self.threat(ours, theirs)
        inc = self.threat(theirs, ours)

        score = 100.0 * (out.dmg_avg - inc.dmg_avg)

        # Removing them outright is worth much more than chip damage, but only
        # if we actually get to move first -- an OHKO we never fire is worth
        # nothing, which is why speed enters here rather than as a separate term.
        if out.ohko:
            score += 120.0 if (out.outspeeds or out.priority > inc.priority) else 55.0
        elif out.ohko_possible:
            score += 45.0 if out.outspeeds else 20.0

        # Being OHKOd is the mirror image and is weighted slightly harder: losing
        # the piece forfeits every future turn it would have acted on.
        if inc.ohko:
            score -= 130.0 if (inc.outspeeds or inc.priority > out.priority) else 60.0
        elif inc.ohko_possible:
            score -= 50.0 if inc.outspeeds else 22.0

        if out.nhko < inc.nhko:
            score += 25.0
        elif inc.nhko < out.nhko:
            score -= 25.0
        return score

    def pressured_by(self, defender):
        """Everything on the other side that currently threatens `defender`."""
        pool = self.theirs if defender in self.ours else self.ours
        return [a for a in pool if self.threat(a, defender).threatens]

    def answers_to(self, enemy):
        """Our Pokemon that handle `enemy`, best first -- the answer map."""
        scored = [(self.handles_score(o, enemy), o) for o in self.ours]
        scored.sort(key=lambda x: -x[0])
        return [(mon, score) for score, mon in scored]

    def summary_rows(self):
        """Flat rows for display/inspection: (ours, theirs, score, our edge)."""
        rows = []
        for o in self.ours:
            for t in self.theirs:
                rows.append((o.name, t.name, round(self.handles_score(o, t), 1),
                             self.threat(o, t)))
        return rows


def build_threat_matrix(battle, movesets) -> ThreatMatrix:
    return ThreatMatrix(battle, movesets)


# ---------------------------------------------------------------------------
# Answer preservation (design doc section 4b)
# ---------------------------------------------------------------------------
# "What Pokemon can you absolutely not afford to lose? If you lose a Pokemon,
# what Pokemon on their team are FREED from its pressure?" -- Aaron Traylor,
# vgcguide team-preview. That is a matching edge being deleted, which is the
# exact intuition this term exists to price.
#
# Material evaluation cannot express it: trading our Gallade for their Gallade
# is even on HP and KO count, but if Gallade was the only answer to their
# Archaludon then the trade collapses our coverage and the position is losing.

# What an unanswered threat costs. Deliberately worse than the worst realistic
# real answer (being outsped and OHKOd scores about -130 in handles_score), so
# that leaving a threat uncovered is never preferable to covering it badly.
#
# Without this the term is not merely imprecise, it is BACKWARDS. A matching can
# only pair min(threats, answerers) times, so losing a Pokemon lets the matching
# quietly drop the threat it handled worst and report a HIGHER coverage than
# before -- i.e. losing a piece would look like an improvement. Caught by
# tests/test_coverage_term.py.
NO_ANSWER_PENALTY = -160.0


def coverage_for(matrix, defenders, answerers) -> float:
    """Best assignment of `answerers` to the threats in `defenders`.

    Each threat is answered by at most one of ours, and each answerer covers at
    most one threat -- that exclusivity is the whole point. One Pokemon that
    happens to beat three of theirs cannot be in three places at once, and a
    simple "sum of best answers" would double-count it exactly where the
    position is most fragile.

    Threats with no answerer left take NO_ANSWER_PENALTY. Surplus answerers are
    simply unused, which is correct: bench depth beyond the number of threats is
    not worth anything by itself.

    Returns the MEAN per threat, not the sum. The sum scales with how many
    threats there are, so subtracting one side's sum from the other's (see
    coverage_differential) confounds "our answers got worse" with "there are
    simply fewer Pokemon left to answer". Losing a Pokemon is already priced by
    the KO and HP terms; this term exists to measure answer STRUCTURE, and a
    per-threat mean is the version of it that does not move when the count does.
    """
    if not defenders:
        return 0.0
    if not answerers:
        return NO_ANSWER_PENALTY

    weights = [[matrix.handles_score(a, d) for a in answerers] for d in defenders]
    shortfall = len(defenders) - len(answerers)
    if shortfall > 0:
        for row in weights:
            row.extend([NO_ANSWER_PENALTY] * shortfall)
    total, _pairs = max_weight_matching(weights)
    return total / len(defenders)


def coverage_differential(matrix) -> float:
    """Antisymmetric coverage term, from p1's point of view.

    Both halves are computed the same way from the same directed edges, so
    evaluating from p2 gives exactly the negation -- required for the leaf value
    to stay zero-sum (see solver.leaf_value).
    """
    ours = coverage_for(matrix, matrix.theirs, matrix.ours)
    theirs = coverage_for(matrix, matrix.ours, matrix.theirs)
    return ours - theirs
