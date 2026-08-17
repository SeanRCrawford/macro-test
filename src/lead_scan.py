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

from damage import hits_ally, is_spread_move
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
                 "Wins", "Patched", "Unheld", "Ties", "Of their leads",
                 "Score", "Their leads we cannot hold"])
    for i, r in enumerate(rows, start=1):
        worst = r["worst"]
        ws.append([i, r["lead"][0], r["lead"][1], r["back"][0], r["back"][1],
                   worst.opponent, worst.wins, len(worst.patched),
                   len(worst.losses), len(worst.ties), len(worst.results),
                   round(r["score"], 3),
                   "; ".join(" + ".join(x.enemy_lead) for x in worst.losses)])
        ws.cell(ws.max_row, 12).fill = good if r["score"] > 0 else bad
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 22
    ws.auto_filter.ref = ws.dimensions

    det = wb.create_sheet("Their lead pairs")
    _header(det, ["Our lead", "Our back", "Opponent", "Their lead pair",
                  "Result", "Their best plan", "Speed tie", "We lose",
                  "They lose", "Margin", "Patch: switch", "Patch: to",
                  "Patch margin"])
    for r in rows:
        for rep in r["reports"]:
            for n, x in enumerate(rep.results, start=1):
                det.append([" + ".join(r["lead"]) if n == 1 else None,
                            " + ".join(r["back"]) if n == 1 else None,
                            rep.opponent if n == 1 else None,
                            " + ".join(x.enemy_lead), x.verdict, x.plan,
                            "yes" if x.tie else "", x.our_dead, x.their_dead,
                            round(x.margin, 3),
                            x.patch[0] if x.patch else None,
                            x.patch[1] if x.patch else None,
                            round(x.patch[2], 3) if x.patch else None])
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
        [],
        ["Speed ties"],
        ["", "A tie is a coin flip and is NEVER counted as a win. The race "
             "resolves every tie AGAINST us, and a tied opening is sent to the "
             "patch search exactly as a loss is: a plan that needs to win a "
             "flip is a plan with a hole."],
        [],
        ["The patch (nested)"],
        ["", "An opening we do not win is not automatically a hole. If a BACK "
             "Pokemon converts it -- switch in turn 1 eating the damage, deal "
             "nothing, then attack from turn 2 -- the opening is HELD and the "
             "back slot has a job. That is the same two-turn pin question one "
             "level down, which is what makes the method recursive."],
        ["Unheld", "Openings we neither win nor patch. These are the holes, and "
                   "any one of them zeroes the score."],
        [],
        ["Their plan"],
        ["", "Their side may split: one attacks while the other Protects, "
             "chipping us while keeping a Pokemon whole. Every result takes "
             "THEIR BEST plan of {both attack, left protects, right protects}, "
             "so a claim that survives only the obliging column is not counted."],
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
            # Sort key: priority first, then speed, then SIDE -- and the side
            # tiebreak resolves against us on purpose.
            #
            #     "Ideally your strategy does not rely on winning speed ties."
            #
            # A raw (priority, speed) sort leaves ties to list order, which put
            # our Pokemon first and quietly handed us every coin flip. Sorting
            # "them" ahead of "us" at equal priority and speed means a plan is
            # only credited for what it wins without the flip. 0 sorts before 1.
            t = matrix.threat(c, target) if target is not None else None
            order.append((t.priority if t else 0, c.stats.get("spe", 0),
                          0 if side == "them" else 1, side, c, target))
    order.sort(key=lambda row: (-row[0], -row[1], row[2]))

    for _prio, _spe, _tie, _side, actor, target in order:
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
    plan: str = "both attack"       # THEIR best plan, Protect split included
    tie: bool = False               # the opening hinges on a speed tie
    # The nested answer: if we do not win outright, which back converts it.
    # (leaving, arriving, margin) or None.
    patch: tuple | None = None
    log: list = field(default_factory=list)   # the turn-by-turn, when asked for

    @property
    def held(self) -> bool:
        """We win it outright, or a back Pokemon converts it.

        The distinction the whole approach turns on: *"see if way to patch up
        losing case, which turns into a nested 2 turn pin question."* An opening
        we lose but can switch out of is not a hole -- it is a back slot with a
        job.
        """
        return self.verdict != LOSS or self.patch is not None


@dataclass
class RaceReport:
    """Our one lead pair against EVERY lead pair they could show."""
    lead: tuple
    back: tuple
    opponent: str
    results: list = field(default_factory=list)

    @property
    def losses(self):
        """Openings we neither win nor patch. A patched loss is not a hole."""
        return [r for r in self.results if not r.held]

    @property
    def patched(self):
        return [r for r in self.results if r.verdict == LOSS and r.patch]

    @property
    def ties(self):
        """Openings that hinge on a coin flip. Never counted as wins."""
        return [r for r in self.results if r.tie]

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


def race_bring(our4, enemy_roster, world, opponent_name="", turns=2,
               want_logs=False):
    """Our bring-4, lead first, against all 15 of their lead pairs.

    On COMMITTED MOVES (`move_race`), so a spread move hits both of theirs and
    an `allAdjacent` one hits our own partner. The threat matrix is still built,
    but only for the questions that genuinely are per-pair: speed ties, and the
    HP budget behind them.
    """
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
        # SCORED on the threat-matrix race, which is calibrated (31 of 275 pass).
        # NOT scored on `move_race`, even though its model is the better one --
        # see the warning on `_best_joint`: its move choice will Earthquake its
        # own partner for half its HP to gain 23% net on a foe, and a scorer that
        # plays like that is not a scorer. `move_race` supplies the LINES, which
        # is what it is trustworthy for.
        verdict, od, td, margin, plan = race_robust(matrix, lead, pair,
                                                   turns=turns)
        log = []
        if want_logs:
            _v, _o, _t, _m, _p, log = move_race(
                ms, b.typechart, b.field, b, lead, list(pair), turns=turns,
                want_log=True)
            log = log or []
        tie = bool(speed_ties(matrix, lead, pair))
        patch = None
        if verdict == LOSS or tie:
            # The nested question. A tie is patched for the same reason a loss
            # is: a plan that needs to win a coin flip is a plan with a hole.
            got = salvage(matrix, lead, back, pair)
            if got is not None:
                patch = (got[0].name, got[1].name, got[3])
        report.results.append(PairResult(
            enemy_lead=tuple(c.name for c in pair), verdict=verdict,
            our_dead=od, their_dead=td, margin=margin, plan=plan, tie=tie,
            patch=patch, log=log or []))
    report.results.sort(key=lambda r: r.margin)
    return report


def move_salvage(ms, typechart, field, battle, lead, back, theirs, turns=3):
    """`salvage`, on committed moves. (leaving, arriving, margin) or None."""
    best = None
    for leaving in lead:
        stayer = [c for c in lead if c is not leaving]
        for arriving in back:
            ours = stayer + [arriving]
            verdict, _od, _td, margin, _plan, _log = move_race(
                ms, typechart, field, battle, ours, theirs, turns=turns,
                not_acting_turn1=(arriving,))
            if verdict == WIN and (best is None or margin > best[2]):
                best = (leaving, arriving, margin)
    return best


def describe_race(report, limit=6):
    lines = [f"{' + '.join(report.lead)}  (back: {', '.join(report.back)})"
             f"  vs {report.opponent}",
             f"  {report.wins} wins, {len(report.patched)} patched, "
             f"{len(report.losses)} unheld of {len(report.results)} of their "
             f"lead pairs   score {report.score:+.2f}"]
    if report.ties:
        lines.append(f"  speed ties (never counted as wins): "
                     + "; ".join(" + ".join(r.enemy_lead) for r in report.ties))
    for r in report.results[:limit]:
        bits = [f"    {r.verdict.upper():5s} {' + '.join(r.enemy_lead):40s} "
                f"margin {r.margin:+.2f}"]
        if r.plan != "both attack":
            bits.append(f"[{r.plan}]")
        if r.tie:
            bits.append("TIE")
        if r.patch:
            bits.append(f"-> switch {r.patch[0]} to {r.patch[1]} "
                        f"({r.patch[2]:+.2f})")
        lines.append("  ".join(bits))
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


# --- speed ties, the nested salvage, and the HP budget -----------------------
#
#     "Ideally your strategy does not rely on winning speed ties. Such as,
#      Garchomp+Ninetales-Alola is good because Ninetales-Alola beats enemy
#      Garchomp first to avoid speed tie, and if the enemy Mega Floette is led and
#      speed ties, you can switch Garchomp into the back pokemon *specifically
#      prepared for that special case to make that rare tied/losing lead a win*,
#      who resists floette's attacks, and beats floette even after that damage
#      ... ninetales-alola blizzards twice t1/t2, scizor is switched in t1, then
#      t2 bullet punch threatens kill on them; sequence starting t2 is even if
#      they attack they are pinned, as nothing else can come in to survive and
#      then win (not enough HP after switch damage)"
#
# Three separate mechanisms in that paragraph, and the recursion is the point:
#
#   1. A tie is not a win. `speed_ties` names the openings that hinge on a flip
#      so they can be treated as losses and PATCHED rather than counted.
#   2. `salvage` is the patch: for an opening we do not win outright, is there a
#      back Pokemon that converts it -- switch in turn 1 eating the damage,
#      attack from turn 2? A back slot spent on exactly one bad opening is a
#      deliberate, cheap insurance policy.
#   3. Which is itself the same two-turn pin question one level down, and
#      `hp_budget` is the form it takes: for each of THEIR four possible
#      switch-ins, how much HP can it afford to lose and still win? If none can
#      afford the switch-in damage, the sequence is a pin. That is the "not
#      enough HP after switch damage" clause, made into a number.

# Their side may split: one attacks, one Protects, chipping us while keeping a
# Pokemon in reserve. A claim that survives only the both-attack column is not a
# claim -- "is this robust to one enemy attacking and one protecting to whittle
# down".
ENEMY_PLANS = ("both attack", "left protects", "right protects")


def speed_ties(matrix, ours, theirs):
    """(ours, theirs) pairs at EXACTLY equal effective speed. A coin flip.

    Read off the matrix rather than the raw stat, so Tailwind, Choice Scarf,
    paralysis and a Mega's post-transformation speed are all included: two
    Pokemon "tie" when neither `outspeeds` the other and neither has priority.
    """
    out = []
    for o in ours:
        for e in theirs:
            a, b = matrix.threat(o, e), matrix.threat(e, o)
            if a.priority == b.priority and not a.outspeeds and not b.outspeeds:
                out.append((o, e))
    return out


def _turn_with(matrix, ours, theirs, hp, plan="both attack", not_acting=()):
    """One turn, with an enemy plan and a set of Pokemon that cannot attack.

    `not_acting` is how a switch is modelled: the slot spent its turn coming in,
    so it takes damage and deals none. `plan` lets one of theirs Protect, which
    means it neither takes damage nor deals it.
    """
    live_ours = [c for c in ours if hp[id(c)] > 0]
    live_theirs = [c for c in theirs if hp[id(c)] > 0]
    if not live_ours or not live_theirs:
        return
    protecting = set()
    if plan == "left protects" and live_theirs:
        protecting.add(id(live_theirs[0]))
    elif plan == "right protects" and len(live_theirs) > 1:
        protecting.add(id(live_theirs[1]))

    attackable = [c for c in live_theirs if id(c) not in protecting]
    our_target = _focus_target(matrix, live_ours, attackable or live_theirs)
    their_target = _focus_target(matrix, live_theirs, live_ours)

    order = []
    for side, mine, target in (("us", live_ours, our_target),
                               ("them", live_theirs, their_target)):
        for c in mine:
            t = matrix.threat(c, target) if target is not None else None
            order.append((t.priority if t else 0, c.stats.get("spe", 0),
                          0 if side == "them" else 1, side, c, target))
    order.sort(key=lambda row: (-row[0], -row[1], row[2]))

    for _p, _s, _tie, side, actor, target in order:
        if hp[id(actor)] <= 0 or target is None:
            continue
        if any(actor is x for x in not_acting):
            continue                       # switched in this turn
        if id(actor) in protecting:
            continue                       # spent its turn Protecting
        if side == "us" and id(target) in protecting:
            continue                       # the attack is denied
        if hp[id(target)] <= 0:
            continue
        hp[id(target)] = max(0.0, hp[id(target)]
                             - matrix.threat(actor, target).dmg_min
                             * mega_bulk_factor(target))


def _outcome(hp, ours, theirs):
    our_dead = sum(1 for c in ours if hp[id(c)] <= 0)
    their_dead = sum(1 for c in theirs if hp[id(c)] <= 0)
    margin = (sum(1.0 - hp[id(c)] for c in theirs)
              - sum(1.0 - hp[id(c)] for c in ours))
    if their_dead > our_dead or (their_dead == our_dead and margin > 0.25):
        return WIN, our_dead, their_dead, margin
    if our_dead > their_dead or margin < -0.25:
        return LOSS, our_dead, their_dead, margin
    return EVEN, our_dead, their_dead, margin


def race_robust(matrix, ours, theirs, turns=2):
    """The race, taking their BEST plan against us -- including a Protect split.

    Returns (verdict, our_dead, their_dead, margin, plan) for their best plan.
    """
    worst = None
    for plan in ENEMY_PLANS:
        hp = {id(c): 1.0 for c in list(ours) + list(theirs)}
        for _ in range(max(1, turns)):
            _turn_with(matrix, ours, theirs, hp, plan=plan)
        verdict, od, td, margin = _outcome(hp, ours, theirs)
        if worst is None or margin < worst[3]:
            worst = (verdict, od, td, margin, plan)
    return worst


def salvage(matrix, lead, back, theirs, turns=3):
    """Can a BACK Pokemon convert an opening we do not win? The nested question.

    For each (which of our leads leaves, which back replaces it): the switch-in
    eats turn 1's damage and deals none, the stayer attacks, and from turn 2 both
    attack. Three turns, because the switch spends one -- *"ninetales-alola
    blizzards twice t1/t2, scizor is switched in t1, then t2 bullet punch
    threatens kill"*.

    Returns (leaving, arriving, verdict, margin, plan) for the best patch found,
    or None. Their best plan is used throughout, Protect split included.
    """
    best = None
    for leaving in lead:
        stayer = [c for c in lead if c is not leaving]
        for arriving in back:
            ours = stayer + [arriving]
            worst = None
            for plan in ENEMY_PLANS:
                hp = {id(c): 1.0 for c in ours + list(theirs)}
                for i in range(max(1, turns)):
                    _turn_with(matrix, ours, theirs, hp, plan=plan,
                               not_acting=(arriving,) if i == 0 else ())
                verdict, _od, _td, margin = _outcome(hp, ours, theirs)
                if worst is None or margin < worst[1]:
                    worst = (verdict, margin, plan)
            verdict, margin, plan = worst
            if verdict == WIN and (best is None or margin > best[3]):
                best = (leaving, arriving, verdict, margin, plan)
    return best


def hp_budget(matrix, ours, theirs_bench, turns=2):
    """For each of their possible switch-ins: how much HP can it afford to lose?

    *"it may be helpful to think in terms of how much HP can each given potential
    back pokemon out of 4 afford to lose and still win vs yours, i.e., can they
    switch in, take damage and win, if not then it is a pin."*

    Returns [(name, afford, verdict)] where `afford` is the fraction of its max
    HP it can arrive already missing and still not lose the exchange. A negative
    number means it cannot switch in at all: even at full health our two turns
    remove it, which is the "not enough HP after switch damage" pin.
    """
    rows = []
    for b in theirs_bench:
        # What we strip while it comes in and before it can answer.
        cost = (_incoming(matrix, ours, b)
                + _incoming(matrix, ours, b, only_before_it_acts=True))
        answers = _kills_outright(matrix, b, ours[0]) if ours else False
        for o in ours[1:]:
            answers = answers or _kills_outright(matrix, b, o)
        afford = 1.0 - cost
        if afford <= 0:
            verdict = "cannot switch in"
        elif not answers:
            verdict = "survives but cannot win"
        else:
            verdict = "can switch in and win"
        rows.append((b.name, afford, verdict))
    rows.sort(key=lambda r: -r[1])
    return rows


def enemy_hp_budget(our4, enemy_roster, world, turns=2):
    """`hp_budget` for a whole bring: their bench against OUR back two.

    The back two, not the lead, because the question this answers arrives after
    the patch has been made -- Scizor is in, and now what of theirs can come in
    against it. Convenience wrapper so a CLI does not have to build a battle.
    """
    from _harness import setup_battle
    from threat import build_threat_matrix
    b, ms = setup_battle(list(our4), list(enemy_roster), world)
    matrix = build_threat_matrix(b, ms)
    ours = [c for c in b.p1.roster if not c.fainted]
    established = [ours[0], ours[2]] if len(ours) > 2 else ours
    bench = [c for c in b.p2.roster if not c.fainted][2:]
    return hp_budget(matrix, established, bench, turns=turns)


# --- committing to a MOVE ----------------------------------------------------
#
#     "the idea was ideally exerting spread damage (e.g. Garchomp Rock Slide +
#      Ninetales-Alola Blizzard) which is the ideal way to maximise our damage
#      output (damageslop) and pin; if we focus one of theirs we can be punished
#      and it's no longer a solved lead. I don't know if this is robust to the
#      simulator; I don't see how Garchomp+Dragonite can beat Whimsicott+Mega
#      Floette."
#
# That doubt was correct, and the flaw was deeper than the verdict. Everything
# above reads `matrix.threat(a, d)`, which is "a's BEST MOVE against d", computed
# independently per target. So a single attacker could be credited with Earthquake
# on one foe and Rock Slide on the other IN THE SAME TURN, and a spread move --
# the whole point -- could never hit two Pokemon, because the matrix has no
# concept of a move at all, only of a per-target damage number.
#
# Measured on exactly the position that was doubted, Garchomp against
# Whimsicott + Mega Floette:
#
#     Rock Slide   SPREAD   Whimsicott  35.8%   Floette  58.0%
#     Earthquake   SPREAD   Whimsicott  35.5%   Floette 115.3%
#
# One committed move removes Mega Floette before it acts and chips Whimsicott by
# a third. And Earthquake is `allAdjacent`, so it hits our own partner -- except
# Dragonite is Dragon/Flying and immune, which is the "flying/levitate pokemon in
# the back to ignore garchomp partner earthquake dmg" principle, priced. The old
# model reached the right verdict for the wrong reason: its focus heuristic had
# Garchomp using Rock Slide on Whimsicott, so Floette lived and Light of Ruin
# killed Garchomp.

def move_plans(actor, moveset, foes, allies, typechart, field, battle):
    """Every damaging move `actor` has, with what it does to EVERYONE it hits.

    Returns [(move, {id: damage_fraction}, priority)] where the dict covers both
    foes for a spread move and the ALLY too for an `allAdjacent` one. Worst-roll
    damage, as a fraction of each target's max HP.
    """
    from damage import damage_roll, defensive_stat, effective_stat

    def dmg(move, defender, n_targets):
        physical = move.category == "Physical"
        ak, dk = ("atk", "def") if physical else ("spa", "spd")
        atk = effective_stat(actor.stats[ak], actor.stages[ak])
        if actor.item == "Choice Band" and physical:
            atk *= 1.5
        if actor.item == "Choice Specs" and not physical:
            atk *= 1.5
        lo, _hi, _avg, _eff = damage_roll(
            50, move.power, atk, defensive_stat(defender, dk, move), actor,
            defender, move, typechart, weather=field.weather,
            num_targets_hit=n_targets)
        return (lo / (defender.max_hp() or 1)) * mega_bulk_factor(defender)

    out = []
    for move, _usage in moveset:
        if move.category == "Status" or not move.power:
            continue
        spread = is_spread_move(move.target)
        live_foes = [f for f in foes if not f.fainted]
        if spread:
            targets = list(live_foes)
            if hits_ally(move.target):
                targets += [a for a in allies if a is not actor and not a.fainted]
            n = max(1, len(targets))
            hits = {id(t): dmg(move, t, n) for t in targets}
            out.append((move, hits, move.priority, True))
        else:
            # A single-target move is a CHOICE of target, so each aim is its own
            # plan -- which is exactly the punishable thing a spread move is not.
            for t in live_foes:
                out.append((move, {id(t): dmg(move, t, 1)}, move.priority, False))
    return out


def _best_joint(plans_by_actor, hp, foe_ids, ally_ids):
    """Greedily pick one plan per actor, maximising removal minus self-harm.

    KNOWN BAD, AND THIS IS WHY `move_race` DOES NOT SCORE ANYTHING. Measured on
    Ninetales-Alola + Garchomp against their Garchomp + Kingambit: it chooses
    Garchomp Earthquake, which hits Kingambit for 71% AND OUR OWN NINETALES FOR
    48%, because the net is +23% and Rock Slide's Rock-into-Dark/Steel is worth
    less than that. Kingambit then removes the Ninetales it damaged. A net-HP
    objective cannot see that crossing a KO THRESHOLD on your own side is
    categorically worse than the HP it costs, and until it can, these choices
    are not playable lines -- they are an upper bound on damage with no sense of
    self-preservation.

    The mechanism around it is right: committed moves, real spread targeting,
    ally hits priced at all, priority order, ties against us, no double Protect.
    That is why `move_race` is used for the TURN-BY-TURN and not for the verdict.

    Value counts damage only up to what a target has LEFT -- overkill is not
    output -- and subtracts damage dealt to our own side at full weight, which is
    what makes an Earthquake beside a Flying partner score differently from an
    Earthquake beside a Ground one.
    """
    import itertools
    actors = list(plans_by_actor)
    best = None
    for combo in itertools.product(*(plans_by_actor[a] for a in actors)):
        gain = 0.0
        pool = dict(hp)
        for _move, hits, _prio, _spread in combo:
            for tid, d in hits.items():
                take = min(d, pool.get(tid, 0.0))
                pool[tid] = pool.get(tid, 0.0) - take
                gain += take if tid in foe_ids else -take
        if best is None or gain > best[0]:
            best = (gain, combo)
    return dict(zip(actors, best[1])) if best else {}


def move_turn(ms, typechart, field, battle, ours, theirs, hp, plan="both attack",
              not_acting=(), log=None):
    """One turn on COMMITTED MOVES. Mutates `hp`; appends lines to `log`.

    The order of business, and every clause of it is load-bearing:
      * each side picks ONE move per Pokemon, jointly, by `_best_joint`
      * a spread move hits both foes -- and our own partner when it is
        `allAdjacent`, which is what makes a Flying or Levitate partner worth
        something next to Earthquake
      * order is by move PRIORITY then speed, with ties broken against us
      * a Pokemon whose HP has reached zero does not act: the pin
      * a Protecting Pokemon neither takes damage nor deals it
    """
    live_ours = [c for c in ours if hp[id(c)] > 0]
    live_theirs = [c for c in theirs if hp[id(c)] > 0]
    if not live_ours or not live_theirs:
        return
    protecting = set()
    if plan == "left protects" and live_theirs:
        protecting.add(id(live_theirs[0]))
    elif plan == "right protects" and len(live_theirs) > 1:
        protecting.add(id(live_theirs[1]))

    our_ids = {id(c) for c in ours}
    their_ids = {id(c) for c in theirs}

    our_plans = {c: move_plans(c, ms.get(c.name) or [], live_theirs, live_ours,
                               typechart, field, battle)
                 for c in live_ours if not any(c is x for x in not_acting)}
    our_plans = {c: p for c, p in our_plans.items() if p}
    their_plans = {c: move_plans(c, ms.get(c.name) or [], live_ours, live_theirs,
                                 typechart, field, battle)
                   for c in live_theirs if id(c) not in protecting}
    their_plans = {c: p for c, p in their_plans.items() if p}

    chosen = {}
    if our_plans:
        chosen.update(_best_joint(our_plans, hp, their_ids, our_ids))
    if their_plans:
        chosen.update(_best_joint(their_plans, hp, our_ids, their_ids))

    order = sorted(chosen.items(),
                   key=lambda kv: (-kv[1][2], -kv[0].stats.get("spe", 0),
                                   0 if id(kv[0]) in their_ids else 1))
    for actor, (move, hits, _prio, spread) in order:
        if hp[id(actor)] <= 0:
            if log is not None:
                log.append(f"    {actor.name} was removed before it acted "
                           f"(it would have used {move.name})")
            continue
        landed = []
        for tid, d in hits.items():
            if tid in protecting:
                landed.append("blocked by Protect")
                continue
            if hp.get(tid, 0.0) <= 0:
                continue
            before = hp[tid]
            hp[tid] = max(0.0, before - d)
            name = next((c.name for c in list(ours) + list(theirs)
                         if id(c) == tid), "?")
            landed.append(f"{name} {before * 100:.0f}->{hp[tid] * 100:.0f}%"
                          + (" [FAINTS]" if hp[tid] <= 0 else ""))
        if log is not None:
            log.append(f"    {actor.name} {move.name}"
                       f"{' (spread)' if spread else ''}: "
                       + ", ".join(landed or ["nothing to hit"]))
    if log is not None:
        for c in live_theirs:
            if id(c) in protecting:
                log.append(f"    {c.name} Protects")


def move_race(ms, typechart, field, battle, ours, theirs, turns=2,
              not_acting_turn1=(), want_log=False):
    """`race`, on committed moves. Returns (verdict, od, td, margin, plan, log)
    for THEIR best plan."""
    worst = None
    # A PLAN PER TURN, not one plan for the whole race. The first version fixed
    # the plan across both turns, so "right protects" had Mega Floette Protecting
    # twice -- which is illegal, and it never ate the Earthquake that was supposed
    # to remove it. Enumerating (turn 1 plan, turn 2 plan) and forbidding the same
    # Pokemon Protecting twice in a row is the fix; it also lets them Protect on
    # the turn that actually suits them rather than only on the first.
    schedules = [(a, b) for a in ENEMY_PLANS for b in ENEMY_PLANS
                 if a == "both attack" or b == "both attack" or a != b]
    for schedule in schedules:
        hp = {id(c): 1.0 for c in list(ours) + list(theirs)}
        log = [] if want_log else None
        for i in range(max(1, turns)):
            plan = schedule[min(i, len(schedule) - 1)]
            if log is not None:
                log.append(f"  Turn {i + 1}  (their plan: {plan})")
            move_turn(ms, typechart, field, battle, ours, theirs, hp, plan=plan,
                      not_acting=not_acting_turn1 if i == 0 else (), log=log)
        verdict, od, td, margin = _outcome(hp, ours, theirs)
        if worst is None or margin < worst[3]:
            worst = (verdict, od, td, margin, " then ".join(schedule), log)
    return worst
