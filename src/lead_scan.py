"""Find the overwhelming lead pair, one enemy at a time, with arithmetic only.

The worked example this is built from, and the acceptance test for it:

    "vs big 6, you only bring 4. There is one lead pair which has an
     overwhelming advantage: (focus sash) ninetales-alola + (life orb) garchomp
     with mega scizor and a flying/levitate pokemon in the back to ignore
     garchomp partner earthquake dmg. ... There is nothing which can really
     afford to switch in, so its a general pin given the spread damage output
     and speed tiers.

     1. Mega Charizard Y: outsped, Garchomp KOs with rock slide.
     2. Garchomp: outsped, Ninetales KOs with Blizzard.
     3. Kingambit, Basculegion: Garchomp earthquake + Ninetales-Alola Blizzard
        or Freeze Dry is very threatening, and will then collapse into a win by
        turn 2.
     4. Mega Floette, Whimsicott: either Garchomp earthquake + Ninetales
        blizzard to KO, or ... Garchomp switches to Scizor (or a specially
        defensive fairy resist who can outspeed/ohko in return, which Scizor can
        even in tailwind) while Ninetales-Alola uses Blizzard to bring Whimsicott
        to sash."

Read that list again and notice its SHAPE. It is six independent questions, each
answered by one of four verdicts, and none of them needs a game to be played:

    OUTSPED     we move first and one of ours guarantees the KO       (a pin)
    FOCUSED     both of ours together guarantee it this turn
    TWO TURNS   it dies across this turn and next before acting twice
    SWITCH      the back has something that walls it and out-trades
    PROBLEM     none of the above

That is exactly the arithmetic `pin.py` already does, applied across their whole
roster instead of one position -- and it costs a ThreatMatrix rather than a
search. The expensive pipeline plays games; this one adds numbers, so it can
sweep thousands of lead pairs in the time the audit spends on one pairing.

WHAT IT IS FOR. Narrowing. The full search rates a bring against 90 enemy
configurations by playing them out; before that is affordable you need to know
which few hundred of the hundreds of thousands of lead pairs are worth the
night. This answers "does this pair leave anything unanswered", across several
opponents at once, because:

    "There just needs to be an overlap across the different teams (bring 4 to
     each), with goodish type synergy, stats, damage output."

so the ranking is by the WORST opponent, not the mean. A pair that dismantles
five teams and has no answer to the sixth is not the pair.

WHICH OF THEIR POKEMON HOLDS THE MEGA SLOT CHANGES THE ANSWER, and this is the
sharpest known limitation. One Mega per team, chosen at preview, and the dataset
assigns it -- so a scan silently answers the question for ONE of their possible
choices. Measured on the reference case: base Mega Floette is speed 111 and its
Mega is 166, exactly tying Garchomp. On Big 6 as loaded, Charizard Y holds the
slot, Floette stays at 111 and Garchomp outspeeds it cleanly; if they Mega
Floette instead it is a coin flip, which is precisely the caution in the worked
example -- *"mega floette OHKOs garchomp if it wins speed tie"*. `mega_slots()`
enumerates their choices so a pair can be scanned against each, and `worst_of`
ranks on the worst, because they pick after seeing your team.

WHAT IT DELIBERATELY DOES NOT DO. It does not play the game, so it cannot see
Protect stalling, redirection, Wide Guard, a Substitute, setup, or anything
whose value is in a sequence rather than a sum. It reads the usage-default set
for both sides. It assumes we get to focus both attacks where we like, which the
enemy's own attacks may deny. Every verdict here is a HYPOTHESIS the audit
should be pointed at -- which is the division of labour, not a shortcoming: the
cheap thing proposes, the expensive thing decides.
"""
from dataclasses import dataclass, field

from pin import _incoming, _kills_outright, _moves_first, mega_bulk_factor
from species_data import base_form_name

# Verdicts, best first. Order matters: `scan` reports the FIRST that applies, so
# "we outspeed and OHKO it" is preferred over "we can grind it down", even
# though both are true, because the report should name the cleanest answer.
OUTSPED = "outsped"
FOCUSED = "focused"
TWO_TURNS = "two turns"
SWITCH = "switch"
PROBLEM = "PROBLEM"

ANSWERED = (OUTSPED, FOCUSED, TWO_TURNS, SWITCH)

# How good each verdict is, for the score. An outspeed-and-KO is worth more than
# a two-turn grind because it costs nothing and cannot be interrupted; a switch
# is worth least of the answers because it spends a turn and concedes momentum.
WEIGHT = {OUTSPED: 1.0, FOCUSED: 0.9, TWO_TURNS: 0.6, SWITCH: 0.45, PROBLEM: 0.0}

# What a verdict is worth when the enemy PRE-EMPTS: it moves before both of our
# leads and guarantees the KO on one of them. We still remove it, but we paid a
# Pokemon for the privilege, so it is a trade rather than a pin.
#
# This factor is why the score discriminates at all. Without it, 239 of 276
# swept pairs "answered every Pokemon" -- because focus-firing two attacks into
# one target while the other target politely does nothing answers almost
# anything. A screen that passes 87% of candidates is not narrowing.
PREEMPT_FACTOR = 0.35


@dataclass
class EnemyVerdict:
    """What happens to one of their Pokemon if it is on the field turn 1."""
    enemy: str
    verdict: str
    by: str = ""             # which of ours, and how
    incoming: float = 0.0    # what it does back, as a fraction of our HP
    note: str = ""
    # It moves before BOTH our leads and guarantees a KO on one of them. We may
    # still answer it, but not before it has taken a Pokemon.
    preempts: bool = False

    @property
    def answered(self) -> bool:
        return self.verdict in ANSWERED

    @property
    def weight(self) -> float:
        w = WEIGHT[self.verdict]
        return w * PREEMPT_FACTOR if self.preempts else w


@dataclass
class LeadReport:
    lead: tuple
    back: tuple
    opponent: str
    verdicts: list = field(default_factory=list)

    @property
    def problems(self):
        return [v for v in self.verdicts if not v.answered]

    @property
    def covered(self) -> int:
        return sum(1 for v in self.verdicts if v.answered)

    @property
    def score(self) -> float:
        """Mean verdict weight, zero if anything is unanswered.

        HARD zero, not a penalty. The question being asked is "can this lead
        hold up against ANY of theirs", and a pair with one hole does not answer
        it -- averaging the hole away is how a lead that loses to one Pokemon
        ends up recommended.
        """
        if not self.verdicts or self.problems:
            return 0.0
        return sum(v.weight for v in self.verdicts) / len(self.verdicts)


def _answers_from_back(matrix, back, enemy, ours_on_field):
    """A back Pokemon that walls `enemy` and out-trades it, or None.

    The role the user described, generalised: *"Scizor is a standin for a fairy
    resist who can switch in (bulky enough) and out-trade and/or outspeed
    floette and a partner."* So the test is not "resists the type" -- it is
    survives the hit, and then wins the exchange.
    """
    for b in back:
        # It has to live the switch-in. Mega bulk counts, since it evolves as it
        # lands.
        taken = matrix.threat(enemy, b).dmg_max * mega_bulk_factor(b)
        if taken >= 1.0:
            continue
        if not _kills_outright(matrix, b, enemy):
            continue
        # And it has to get to fire: either it is faster, or it survives a second
        # hit while it does.
        if _moves_first(matrix, b, enemy) or taken * 2 < 1.0:
            return b
    return None


def scan(matrix, battle, lead, back, opponent_name=""):
    """One lead pair against one enemy roster. Pure arithmetic, no simulation.

    `lead` and `back` are Combatants from `battle.p1`; the enemy roster is
    everything on `battle.p2`.
    """
    enemies = [c for c in matrix.theirs if not c.fainted]
    report = LeadReport(lead=tuple(c.name for c in lead),
                        back=tuple(c.name for c in back),
                        opponent=opponent_name)
    for e in enemies:
        worst_back = max((matrix.threat(e, o).dmg_max for o in lead),
                         default=0.0)
        # Does it take one of ours with it? Guaranteed KO, and ahead of BOTH of
        # our leads -- ahead of only one is not a pre-empt, since the other still
        # fires. This is the cost side the first version left out entirely.
        preempts = any(_kills_outright(matrix, e, o) and _moves_first(matrix, e, o)
                       for o in lead) and all(_moves_first(matrix, e, o)
                                              for o in lead)

        # 1. OUTSPED: one of ours moves first and guarantees the KO. The user's
        #    cases 1 and 2 -- "outsped, Garchomp KOs with rock slide".
        solo = [o for o in lead
                if _kills_outright(matrix, o, e) and _moves_first(matrix, o, e)]
        if solo:
            report.verdicts.append(EnemyVerdict(
                e.name, OUTSPED, by=f"{solo[0].name} {matrix.threat(solo[0], e).move}",
                incoming=worst_back, preempts=preempts))
            continue

        # 2. FOCUSED: both together remove it this turn. Case 3 -- "Garchomp
        #    earthquake + Ninetales-Alola Blizzard is very threatening".
        if len(lead) >= 2 and matrix.joint_kos(lead[0], lead[1], e,
                                               guaranteed=True):
            report.verdicts.append(EnemyVerdict(
                e.name, FOCUSED,
                by=f"{lead[0].name} + {lead[1].name}", incoming=worst_back,
                preempts=preempts))
            continue

        # 3. TWO TURNS: it dies across this turn and next, before acting twice.
        #    "will then collapse into a win by turn 2."
        t1 = _incoming(matrix, lead, e)
        t2 = _incoming(matrix, lead, e, only_before_it_acts=True)
        if t1 + t2 >= 1.0:
            report.verdicts.append(EnemyVerdict(
                e.name, TWO_TURNS,
                by=f"{lead[0].name} + {lead[1].name}", incoming=worst_back,
                preempts=preempts,
                note=f"{t1 * 100:.0f}% then {t2 * 100:.0f}%"))
            continue

        # 4. SWITCH: the back answers it. Case 4 -- "Garchomp switches to Scizor
        #    ... while Ninetales-Alola uses Blizzard".
        answer = _answers_from_back(matrix, back, e, lead)
        if answer is not None:
            report.verdicts.append(EnemyVerdict(
                e.name, SWITCH, by=answer.name, incoming=worst_back,
                preempts=preempts,
                note=f"walls it and answers with "
                     f"{matrix.threat(answer, e).move}"))
            continue

        report.verdicts.append(EnemyVerdict(
            e.name, PROBLEM, incoming=worst_back,
            note=f"nothing outspeeds it, focus fire leaves "
                 f"{max(0.0, 1.0 - t1) * 100:.0f}%, no back answers it"))
    return report


def scan_bring(our4, enemy_roster, world, lead_index=(0, 1), opponent_name=""):
    """Convenience wrapper: build the position, scan it, return the report.

    `our4` is the bring in lead-first order, so `lead_index` almost never needs
    changing.
    """
    from _harness import setup_battle
    from threat import build_threat_matrix
    b, ms = setup_battle(list(our4), list(enemy_roster), world)
    matrix = build_threat_matrix(b, ms)
    ours = [c for c in b.p1.roster if not c.fainted]
    lead = [ours[i] for i in lead_index]
    back = [c for c in ours if c not in lead]
    return scan(matrix, b, lead, back, opponent_name)


def describe(report):
    """The user's own breakdown format: one numbered line per enemy."""
    lines = [f"{' + '.join(report.lead)}  (back: {', '.join(report.back)})"
             f"  vs {report.opponent}",
             f"  {report.covered}/{len(report.verdicts)} answered, "
             f"score {report.score:.2f}"]
    for i, v in enumerate(report.verdicts, start=1):
        bits = [f"  {i}. {v.enemy}: {v.verdict.upper()}"]
        if v.by:
            bits.append(f"by {v.by}")
        if v.note:
            bits.append(f"({v.note})")
        lines.append("  ".join(bits))
    return lines


def mega_slots(enemy_roster, world):
    """Their possible Mega choices: the names on this roster that can Mega.

    One Mega per team, and they choose it at PREVIEW -- after seeing your four.
    So a lead pair that beats a team when Charizard holds the slot and loses when
    Floette does has not beaten the team, and `worst_of` is the honest reading.
    """
    merged = world["merged"]
    out = []
    for name in enemy_roster:
        rec = merged.get(name) or {}
        if rec.get("mega_stats") or rec.get("is_mega") or name.startswith("Mega "):
            out.append(name)
    return out


def worst_of(reports):
    """The report that matters: their best answer to our pair.

    They choose their Mega, their bring and their lead after seeing our four, so
    a pair is worth what its WORST case is worth, not its average.
    """
    return min(reports, key=lambda r: (r.score, -len(r.problems))) if reports else None


def write_workbook(rows, path, opponents=(), pool_size=0):
    """The sweep as an .xlsx: a ranking sheet, a per-enemy detail sheet, a legend.

    The detail sheet is the one that matters. A score says a pair works; only the
    enemy-by-enemy breakdown says WHY, and that is the form the idea was
    expressed in -- six numbered lines, one per Pokemon.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    head = PatternFill("solid", start_color="2C3E50", end_color="2C3E50")
    good = PatternFill("solid", start_color="D5F5E3", end_color="D5F5E3")
    warn = PatternFill("solid", start_color="FCF3CF", end_color="FCF3CF")
    bad = PatternFill("solid", start_color="FADBD8", end_color="FADBD8")
    fill_for = {WIN: good, EVEN: warn, LOSS: bad,
                OUTSPED: good, FOCUSED: good, TWO_TURNS: warn, SWITCH: warn,
                PROBLEM: bad}

    def _header(ws, cols, freeze="A2"):
        ws.append(cols)
        for cell in ws[1]:
            cell.fill = head
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(horizontal="center", wrap_text=True)
        ws.freeze_panes = freeze
        for idx, name in enumerate(cols, start=1):
            ws.column_dimensions[get_column_letter(idx)].width = max(12, len(name) + 2)

    wb = Workbook()
    ws = wb.active
    ws.title = "Lead pairs"
    _header(ws, ["#", "Lead A", "Lead B", "Back A", "Back B", "Worst opponent",
                 "Wins", "Losses", "Of their leads", "Score",
                 "Their leads that beat us"])
    for i, r in enumerate(rows, start=1):
        worst = r["worst"]
        ws.append([i, r["lead"][0], r["lead"][1], r["back"][0], r["back"][1],
                   worst.opponent, worst.wins, len(worst.losses),
                   len(worst.results), round(r["score"], 3),
                   "; ".join(" + ".join(x.enemy_lead) for x in worst.losses)])
        ws.cell(ws.max_row, 10).fill = good if r["score"] > 0 else bad
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 22
    ws.auto_filter.ref = ws.dimensions

    det = wb.create_sheet("Their lead pairs")
    _header(det, ["Our lead", "Our back", "Opponent", "Their lead pair",
                  "Result", "We lose", "They lose", "Margin"])
    for r in rows:
        for rep in r["reports"]:
            for n, x in enumerate(rep.results, start=1):
                det.append([" + ".join(r["lead"]) if n == 1 else None,
                            " + ".join(r["back"]) if n == 1 else None,
                            rep.opponent if n == 1 else None,
                            " + ".join(x.enemy_lead), x.verdict,
                            x.our_dead, x.their_dead, round(x.margin, 3)])
                det.cell(det.max_row, 5).fill = fill_for.get(x.verdict, warn)
    det.column_dimensions["A"].width = 36
    det.column_dimensions["B"].width = 36
    det.column_dimensions["D"].width = 36

    legend = wb.create_sheet("How to read this")
    for row in (
        ["What this is"],
        ["Lead pairs ranked by whether ANY of the opponent's Pokemon is left "
         "unanswered. No games are played -- every verdict is arithmetic on a "
         "threat matrix, so this sweeps thousands of pairs in the time the "
         "overnight audit spends on one pairing."],
        [],
        ["Opponents", ", ".join(opponents)],
        ["Lead pool size", pool_size],
        [],
        ["Result", "Means"],
        [WIN, "After this turn and next they are down more Pokemon than we are "
              "(or level on Pokemon and ahead on HP lost)."],
        [EVEN, "Level within a quarter of a Pokemon."],
        [LOSS, "We are behind. One of these zeroes the pair's score."],
        [],
        ["Margin", "(their HP lost) - (our HP lost), in Pokemon-equivalents, "
                   "over two turns of both sides focus-firing."],
        ["Score", "Mean margin across all 15 of their lead pairs, or a HARD "
                  "ZERO if any single one beats us."],
        ["Why the race", "An earlier version asked only whether we could remove "
                         "each of their Pokemon, and 235 of 275 swept pairs "
                         "passed -- two attackers focused on one target while "
                         "its partner does nothing answers almost anything. "
                         "Pricing the exchange took it to 31 of 275."],
        ["Species clause", "Four distinct BASE species, at most one Mega. The "
                           "sweep's top answer was once Garchomp + Mega "
                           "Garchomp. A screen that recommends an illegal team "
                           "is worse than no screen."],
        ["Ranked on the worst", "They choose their Mega, their four and their "
                               "lead AFTER seeing your four, so a pair is worth "
                               "what its worst case is worth."],
        [],
        ["Known limitations"],
        ["The Mega slot", "One Mega per team, chosen at preview. The dataset "
                          "assigns it, so a scan answers for ONE of their "
                          "choices. Reference case: base Mega Floette is speed "
                          "111 and its Mega is 166, exactly tying Garchomp -- "
                          "so which Pokemon holds their slot decides whether "
                          "that is a clean outspeed or a coin flip."],
        ["No sequences", "Protect stalling, redirection, Wide Guard, "
                         "Substitute and setup are all invisible to a sum."],
        ["Usage sets", "Both sides are read at their usage-default set."],
        ["Free focus fire", "It assumes we aim both attacks where we like, "
                            "which their own attacks may deny."],
        ["", "Every verdict is a HYPOTHESIS to point the audit at. The cheap "
             "thing proposes; the expensive thing decides."],
    ):
        legend.append(row)
    legend["A1"].font = Font(bold=True, size=13)
    for cell in ("A7", "A18"):
        legend[cell].font = Font(bold=True)
    legend.column_dimensions["A"].width = 20
    legend.column_dimensions["B"].width = 94
    for row in legend.iter_rows(min_col=2, max_col=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    wb.save(path)
    return len(rows)


# --- the race ---------------------------------------------------------------
# Everything above answers "can we remove this ONE Pokemon", which turned out
# not to discriminate: 235 of 275 swept pairs answered every Pokemon on every
# opponent, because two attackers focused on one target while its partner does
# nothing answers almost anything. The missing half is the cost. What decides a
# matchup is the 2v2 EXCHANGE -- over this turn and the next, who runs out of
# Pokemon first -- and that is still only arithmetic.

WIN, EVEN, LOSS = "win", "even", "loss"


def _focus_target(matrix, attackers, defenders):
    """Which of `defenders` this side focuses: the one it removes soonest.

    A declared heuristic, not an optimisation. Ranked by combined guaranteed
    damage as a fraction of CURRENT hp, so "the one already in range" beats "the
    one we do more raw damage to" -- which is the focus-fire logic the whole idea
    rests on: chip into a partner's kill.
    """
    best = None
    for d in defenders:
        share = sum(matrix.threat(a, d).dmg_min for a in attackers)
        need = d.current_hp / (d.max_hp() or 1)
        # Fraction of the way to a removal, capped so an overkill does not beat a
        # clean kill on a second target.
        progress = min(1.0, share / need) if need > 0 else 1.0
        key = (progress, share)
        if best is None or key > best[0]:
            best = (key, d)
    return best[1] if best else None


def _turn(matrix, ours, theirs, hp):
    """Resolve one turn as arithmetic. Mutates `hp` (fractions remaining).

    Speed-ordered, and a Pokemon whose HP has already reached zero this turn
    does not act -- which is the pin, expressed as the only thing it really is:
    an attack that never happens.
    """
    live_ours = [c for c in ours if hp[id(c)] > 0]
    live_theirs = [c for c in theirs if hp[id(c)] > 0]
    if not live_ours or not live_theirs:
        return
    our_target = _focus_target(matrix, live_ours, live_theirs)
    their_target = _focus_target(matrix, live_theirs, live_ours)

    order = []
    for side, mine, foes, target in (("us", live_ours, live_theirs, our_target),
                                     ("them", live_theirs, live_ours, their_target)):
        for c in mine:
            # Sort key: priority first, then speed. `_moves_first` is pairwise;
            # for a four-way order the underlying numbers are what is needed.
            t = matrix.threat(c, target) if target is not None else None
            order.append((t.priority if t else 0, c.stats.get("spe", 0), side,
                          c, target))
    order.sort(key=lambda row: (-row[0], -row[1]))

    for _prio, _spe, _side, actor, target in order:
        if hp[id(actor)] <= 0 or target is None:
            continue                      # removed before it acted: the pin
        if hp[id(target)] <= 0:
            continue                      # already gone; the attack is wasted
        dealt = matrix.threat(actor, target).dmg_min
        dealt *= mega_bulk_factor(target)
        hp[id(target)] = max(0.0, hp[id(target)] - dealt)


def race(matrix, ours, theirs, turns=2):
    """Who is ahead after `turns` of both sides focus-firing. Pure arithmetic.

    Returns (verdict, our_losses, their_losses, margin) where margin is
    (their HP lost) - (our HP lost), in Pokemon-equivalents.

    Two turns because that is the horizon the whole approach is built on:
    *"Really, this turn + next turn (very quick and simple arithmetic) is all
    that matters."*
    """
    hp = {id(c): 1.0 for c in list(ours) + list(theirs)}
    for _ in range(max(1, turns)):
        _turn(matrix, ours, theirs, hp)
    our_lost = sum(1.0 - hp[id(c)] for c in ours)
    their_lost = sum(1.0 - hp[id(c)] for c in theirs)
    our_dead = sum(1 for c in ours if hp[id(c)] <= 0)
    their_dead = sum(1 for c in theirs if hp[id(c)] <= 0)
    margin = their_lost - our_lost
    if their_dead > our_dead or (their_dead == our_dead and margin > 0.25):
        verdict = WIN
    elif our_dead > their_dead or margin < -0.25:
        verdict = LOSS
    else:
        verdict = EVEN
    return verdict, our_dead, their_dead, margin


@dataclass
class PairResult:
    enemy_lead: tuple
    verdict: str
    our_dead: int
    their_dead: int
    margin: float


@dataclass
class RaceReport:
    """Our one lead pair against EVERY lead pair they could show."""
    lead: tuple
    back: tuple
    opponent: str
    results: list = field(default_factory=list)

    @property
    def losses(self):
        return [r for r in self.results if r.verdict == LOSS]

    @property
    def wins(self):
        return sum(1 for r in self.results if r.verdict == WIN)

    @property
    def worst(self):
        return min(self.results, key=lambda r: r.margin) if self.results else None

    @property
    def score(self) -> float:
        """Mean margin, zeroed if ANY of their leads beats us.

        The question is the one that was asked for: *"a fixed lead vs each team
        that can withstand ANY of theirs"*. A pair that loses to one of their
        fifteen openings has not answered it.
        """
        if not self.results or self.losses:
            return 0.0
        return sum(r.margin for r in self.results) / len(self.results)


def race_bring(our4, enemy_roster, world, opponent_name="", turns=2):
    """Our bring-4, lead first, against all 15 of their lead pairs."""
    import itertools

    from _harness import setup_battle
    from threat import build_threat_matrix
    b, ms = setup_battle(list(our4), list(enemy_roster), world)
    matrix = build_threat_matrix(b, ms)
    ours = [c for c in b.p1.roster if not c.fainted]
    lead, back = ours[:2], ours[2:]
    theirs = [c for c in b.p2.roster if not c.fainted]
    report = RaceReport(lead=tuple(c.name for c in lead),
                        back=tuple(c.name for c in back),
                        opponent=opponent_name)
    for pair in itertools.combinations(theirs, 2):
        verdict, od, td, margin = race(matrix, lead, pair, turns=turns)
        report.results.append(PairResult(
            enemy_lead=tuple(c.name for c in pair), verdict=verdict,
            our_dead=od, their_dead=td, margin=margin))
    report.results.sort(key=lambda r: r.margin)
    return report


def describe_race(report, limit=6):
    lines = [f"{' + '.join(report.lead)}  (back: {', '.join(report.back)})"
             f"  vs {report.opponent}",
             f"  {report.wins} wins, {len(report.losses)} losses of "
             f"{len(report.results)} of their lead pairs   "
             f"score {report.score:+.2f}"]
    for r in report.results[:limit]:
        lines.append(f"    {r.verdict.upper():5s} {' + '.join(r.enemy_lead):40s} "
                     f"margin {r.margin:+.2f}  (we lose {r.our_dead}, "
                     f"they lose {r.their_dead})")
    return lines


def species_of(name: str) -> str:
    """The species-clause identity: "Mega Garchomp" and "Garchomp" are one entry.

    The sweep produced `Garchomp + Mega Garchomp` as its top pair, with
    `Dragonite + Mega Dragonite` and `Mega Garchomp / back: Garchomp` behind it.
    All illegal -- species clause counts the BASE form, and a Mega is the same
    Pokemon holding a stone. A screen that recommends an illegal team is worse
    than no screen, because its answer looks actionable.
    """
    return base_form_name(name) or name


def legal_bring(names) -> bool:
    """Four DISTINCT species. Also rejects two Megas, which no team can field."""
    species = [species_of(n) for n in names]
    if len(set(species)) != len(species):
        return False
    return sum(1 for n in names if n.startswith("Mega ")) <= 1
