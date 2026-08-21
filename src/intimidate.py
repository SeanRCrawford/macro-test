"""What an Intimidate is actually WORTH, measured by playing the game twice.

    "Intimidate is not scored as a resource, nor is an enemy back bringing its
     own."

Everything in this repo already RESOLVES Intimidate correctly -- `engine.
on_switch_in` fires it on every send-out, `damage.apply_intimidate` knows its
four outcomes (drop / Defiant boost / Competitive boost / immune), and
`battle.py` puts it after replacements so it never lands on a corpse. What was
missing is a number: nothing anywhere said how much the ability was worth in a
given matchup, so it could not be traded off against anything else. A lead
screen that cannot price Intimidate cannot answer "is Arcanine-Hisui better as
Intimidate or Rock Head here", which is a question that was asked directly.

HOW IT IS PRICED, AND WHY THERE IS NO FORMULA HERE. An Attack drop is worth
whatever it changes about the game, which depends on whether their attackers are
physical at all, whether the drop turns a 2HKO into a 3HKO, whether the target
has Defiant (in which case it is worth NEGATIVE), and whether the holder even
survives to apply it. None of that is expressible as a term. So this module
calculates nothing: it builds the position through `lead_sim.build_position`,
races it through `lead_sim.race` (i.e. `Battle.run_turn`), then builds the SAME
position with the ability made inert and races it again. The difference in
margin is the answer, in the same Pokemon-equivalent units the rest of the lead
screen reports.

That is why a Defiant foe needs no special case: the counterfactual prices it
automatically, because the simulator applied the boost. It does not follow that
the number goes negative -- measured against NAIC's Aerodactyl / Kingambit /
Garchomp / Sylveon, Intimidate is still worth +0.13 to us, because it drops the
other three and only Kingambit gains. That is exactly the trade a formula would
have had to guess at.

THE COUNTERFACTUAL ABILITY. `INERT` is Illuminate -- chosen because it appears
in no table anywhere in `src/` (checked), so the engine does nothing with it.
Swapping to the holder's OTHER legal ability would have been wrong: Incineroar's
alternative is Blaze, which has its own effect on the game, so the delta would
have measured "Intimidate minus Blaze" and been reported as the worth of
Intimidate.

MEGA PICKS. `combatants.make_combatant` applies an `ability` override to the
BASE form and takes the Mega's own ability from the sheet, which is exactly the
mechanic: Mega Gyarados Intimidates on send-out with base Gyarados's ability and
is Mold Breaker thereafter. So neutralising the base ability here removes
precisely the one Intimidate a Mega pick ever gets, and leaves its Mega ability
alone.

A NEGATIVE NUMBER IS A READING, NOT A VERDICT. Measured: our Gyarados's
Intimidate against Perish Trap's Incineroar / Mega Gengar / Sinistcha / Kommo-o
prices at -0.25 over a two-turn window and +0.07 over three, at both search
breadths -- so the sign is not strategy-set noise, it is what a fixed window
says. Dropping their Attack changes which line we play and how much damage each
side has taken when the window closes, and `lead_sim.outcome`'s margin counts
exactly that. So read a worth as "what this ability did to the position by turn
N", with N stated, rather than as "this ability is bad". Nothing here averages
over horizons on the caller's behalf; the horizon is theirs to choose and the
console prints it.

THE BACKS. Their Intimidate user is usually NOT their lead -- of the three
library teams that hold one, King's Incineroar is its sixth name. A two-turn
opening race never sees it, so `worth` reports a holder that has not come in as
`brought=False` with a worth of 0.0 rather than silently omitting it: "their
back has an Intimidate you have not paid for yet" is the useful output, and it
is a different statement from "their Intimidate is worth nothing here".
"""
from dataclasses import dataclass

INTIMIDATE = "Intimidate"

# See the module docstring: an ability the engine has no rule for, used as the
# "this Pokemon has no ability" counterfactual.
INERT = "Illuminate"

# Reproduced from `damage.apply_intimidate`, for REPORTING only -- so the
# console can name why an Intimidate came out at zero or negative rather than
# leaving the reader to guess. Nothing in this module branches on it; the
# measurement is always the played-out difference.
BACKFIRES = ("Defiant", "Competitive")
BLOCKS = ("Clear Body", "Full Metal Body", "White Smoke", "Hyper Cutter",
          "Inner Focus", "Own Tempo", "Oblivious", "Scrappy")


@dataclass(frozen=True)
class Holder:
    """One Pokemon that will Intimidate when it is sent out.

    `names` is a tuple because a side's holders are also priced TOGETHER (see
    `worth`): the joint row is a Holder over all of them, so one dataclass
    serves both and the console needs no second shape.
    """
    names: tuple
    side: str        # "ours" or "theirs"
    lead: bool       # on the field at turn 1, rather than on the bench

    @property
    def name(self):
        return " + ".join(self.names)

    @property
    def where(self):
        return "lead" if self.lead else "back"


@dataclass(frozen=True)
class Worth:
    """What one holder's Intimidate did to the margin, played out both ways.

    `worth` is signed from the OWNER's point of view: positive means the ability
    earned its holder something. So a positive `worth` on a `theirs` row is
    margin we lose to it.
    """
    holder: Holder
    margin_with: float
    margin_without: float
    verdict_with: str
    verdict_without: str
    brought: bool          # it actually reached the field inside the window
    note: str = ""         # e.g. "turns into a boost for Kingambit (Defiant)"
    joint: bool = False    # every Intimidate on this side turned off at once
    # Free text a caller can attach so a row says WHICH position produced it --
    # `tools/lead_sweep.py` prices a holder against every bring-4 they could be
    # holding and reports the worst, which is only readable if the row names it.
    context: str = ""

    @property
    def worth(self) -> float:
        if self.holder.side == "ours":
            return self.margin_with - self.margin_without
        return self.margin_without - self.margin_with

    @property
    def flips(self) -> bool:
        """It does not merely move the margin, it changes who wins."""
        return self.verdict_with != self.verdict_without


def holders(battle) -> list[Holder]:
    """Who on this board Intimidates, read off the built Combatants.

    Read from the battle rather than from the dataset on purpose: the Combatant
    already reflects the set override, the usage default and (for a Mega pick)
    the base-form ability that is the one that actually fires. Guessing any of
    that from `merged[name]["legal_abilities"]` would answer a different
    question -- "could this species have Intimidate" rather than "does this one".
    """
    out = []
    for side, party in (("ours", battle.p1), ("theirs", battle.p2)):
        active = [c for c in party.active if c is not None]
        for c in party.roster:
            if c.ability == INTIMIDATE:
                out.append(Holder(names=(c.name,), side=side,
                                  lead=any(c is a for a in active)))
    return out


def _note_for(holder, battle):
    """Why an Intimidate might be worth nothing, in words, for the report."""
    foes = battle.p2 if holder.side == "ours" else battle.p1
    bits = []
    for label, abilities in (("turns into a boost for", BACKFIRES),
                             ("blocked by", BLOCKS)):
        named = [f"{c.name} ({c.ability})" for c in foes.roster
                 if c.ability in abilities]
        if named:
            bits.append(f"{label} " + ", ".join(named))
    return "; ".join(bits)


def _merge(sets, names, ability):
    out = {k: dict(v) for k, v in (sets or {}).items()}
    for name in names:
        out.setdefault(name, {})["ability"] = ability
    return out


def _reached_field(log, names):
    """Did these Pokemon all get to Intimidate inside the window we played?

    A lead has, by definition -- it was sent out before turn 1 and the engine
    fired the ability there, above the window the log covers. A bench Pokemon
    only has if the engine wrote a send-out line for it.
    """
    text = "\n".join(log or [])
    return all(f"sends in {n}" in text for n in names)


def worth(our4, enemy4, world, our_sets=None, enemy_sets=None, turns=2,
          breadth="cheap", enemy_mega=None, optimise=False):
    """Price every Intimidate on this board. Returns (baseline, [Worth, ...]).

    `baseline` is (verdict, margin) for the position as it stands, so a caller
    can show what the deltas are deltas FROM.

    EACH ROW IS A MARGINAL, AND THEY DO NOT ADD UP. Turning off one of a side's
    two Intimidates still leaves the other one dropping Attack, so two holders
    can each measure +0.00 while the pair is worth a Pokemon between them. That
    is not an artefact to be corrected -- "what do I lose by running Rock Head
    on this one" genuinely is the marginal -- but it is the wrong number for
    "how much of this matchup is Intimidate", so when a side brings more than
    one holder a JOINT row (`joint=True`) prices them all off at once as well.

    ITEMS AND MOVES ARE HELD FIXED across the counterfactual. `build_position`
    with `optimise=True` re-runs `optimize_sets.best_item`/`best_moveset`, and
    an ability change can move which item that search likes -- so re-optimising
    the counterfactual would fold "and it would also hold a different item" into
    a number reported as the worth of an ability. The baseline is built with
    `optimise` as asked, and every counterfactual re-uses the sets it resolved.
    """
    import lead_sim as sim

    base_battle, base_ms, resolved = sim.build_position(
        our4, enemy4, world, our_sets=our_sets, enemy_sets=enemy_sets,
        optimise=optimise, enemy_mega=enemy_mega)
    v0, _od, _td, m0, _desc, base_log = sim.race(
        base_battle, base_ms, turns=turns, breadth=breadth, want_log=True)

    def priced(holder, joint=False, brought=None):
        if holder.side == "ours":
            ours2, theirs2 = _merge(resolved, holder.names, INERT), enemy_sets
        else:
            ours2, theirs2 = resolved, _merge(enemy_sets, holder.names, INERT)
        b2, ms2, _r2 = sim.build_position(
            our4, enemy4, world, our_sets=ours2, enemy_sets=theirs2,
            optimise=False, enemy_mega=enemy_mega)
        v1, _o, _t, m1, _d, _log = sim.race(b2, ms2, turns=turns,
                                            breadth=breadth, want_log=False)
        if brought is None:
            brought = holder.lead or _reached_field(base_log, holder.names)
        return Worth(holder=holder, margin_with=m0, margin_without=m1,
                     verdict_with=v0, verdict_without=v1, brought=brought,
                     note=_note_for(holder, base_battle), joint=joint)

    found = holders(base_battle)
    out = [priced(h) for h in found]
    for side in ("ours", "theirs"):
        mine = [(h, w) for h, w in zip(found, out) if h.side == side]
        if len(mine) > 1:
            # A joint of a lead and a bench Pokemon that never came in has
            # still had ONE of its Intimidates applied, so `brought` is any,
            # not all -- otherwise the row reads "never reached the field"
            # while sitting on a real measured difference.
            out.append(priced(
                Holder(names=tuple(h.names[0] for h, _w in mine), side=side,
                       lead=all(h.lead for h, _w in mine)),
                joint=True, brought=any(w.brought for _h, w in mine)))
    out.sort(key=lambda x: (x.joint, -abs(x.worth)))
    return (v0, m0), out


def describe(values, indent="  ", turns=None):
    """The console block. Empty when neither side brought an Intimidate.

    Each row carries its OWN baseline (`margin_with`) rather than sharing a
    header figure, because a caller may well be pricing the same holder across
    several positions -- `tools/lead_sweep.py` prices it against every bring-4
    their lead is consistent with -- and one baseline printed above rows drawn
    from different positions would belong to none of them.
    """
    if not values:
        return []
    window = f" over {turns} turns" if turns else ""
    lines = [f"{indent}Intimidate, priced by playing the opening twice"
             f"{window} (once with the ability made inert):"]
    for x in values:
        h = x.holder
        who = "ours " if h.side == "ours" else "their"
        label = (("all of theirs" if h.side == "theirs" else "all of ours")
                 if x.joint else f"{h.name} ({h.where})")
        if not x.brought:
            tail = "never reached the field in this window -- unpaid for"
        else:
            tail = (f"worth {x.worth:+.2f} to "
                    f"{'us' if h.side == 'ours' else 'them'}"
                    f"   {x.verdict_with} {x.margin_with:+.2f} -> "
                    f"{x.verdict_without} {x.margin_without:+.2f}"
                    + ("  [FLIPS THE OPENING]" if x.flips else ""))
        line = f"{indent}  {who} {label:34s}  {tail}"
        extra = "; ".join(bit for bit in (x.note, x.context) if bit)
        if extra:
            line += f"   -- {extra}"
        lines.append(line)
    return lines
