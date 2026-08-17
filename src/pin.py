"""The Pin: a threat that takes an option away rather than a Pokemon.

The concept, as it was put to me:

    "Charizard threatens Solar Beam OHKO onto Swampert and outspeeds. Swampert
     threatens OHKO with Ice Punch to Garchomp, but if Charizard attacks it will
     not move and faint, so it cannot stay in. In a 2v2 there is speed order
     1234 -- Garchomp, Zard, Swampert, Pelipper -- which in this case is reduced
     to 124, because Swampert must switch or protect if Zard pins it."

That last sentence is the whole idea, and it is a statement about the ACTION
SET, not about damage. A pinned Pokemon still has its moves; what it no longer
has is the option to use one and expect it to resolve. Its attack is not merely
worse, it is not going to happen, so the turn is played out among the Pokemon
that are still moving.

Why it needs a name of its own when the equilibrium already finds it. Two
reasons, both practical:

  * The search is capped. `turn_game._enumerate` truncates each side's joint
    action list to a fixed prefix on nested solves, and that list is built by
    `itertools.product` over movesets -- so the ordering is an accident of move
    order, and the row that fires the pin can be cut before the matrix is ever
    built. Ordering by pressure puts it in front.
  * A pin is the principled seed for the double oracle. Restricted-set methods
    converge in the number of iterations it takes to FIND the important action;
    handing them "the move that removes their Pokemon" and "the reply that
    survives it" is exactly the pressure-driven seeding threat.py was written
    for.

The companion idea is the safe play, and it is the reason this module exists at
all rather than the pin alone:

    "if someone outspeeds and OHKOs one of your guys but its partner isn't
     threatened to be fainted this turn, is slower but OHKOs the enemy, it is a
     safe play to protect the threatened guy + attack with other"

Protect has two completely different meanings that a payoff matrix does not
distinguish by itself. Protecting because you GUESSED they would attack that
slot is a prediction, and it loses whenever the guess is wrong. Protecting the
one Pokemon they are guaranteed to kill, while the partner they are not
threatening attacks anyway, is not a guess -- it collects value against every
column. `safe_plays()` names the second kind so a line can be reported honestly
as "safe" or "a read", and so the two are never confused when a line is
explained.

Everything here reads an already-built `ThreatMatrix` and allocates nothing
beyond small tuples: no damage calculation, no simulation.
"""
from dataclasses import dataclass

from engine import effective_speed
from projection import mega_view, projected_field


@dataclass(frozen=True)
class Pin:
    """`attacker` removes `defender`'s option to stay in and attack."""
    attacker: object
    defender: object
    move: str | None
    first: bool          # attacker moves first, so the KO actually lands
    secure: bool         # nothing on the defender's side pre-empts the pinner
    escape: object = None  # a benched Pokemon the defender can switch to and win
    # Bench Pokemon that DO threaten a KO back, and still die before firing it:
    # they eat both our attacks on the way in and both again next turn. Names,
    # not objects, so a Pin stays cheap to carry around and to print.
    through: tuple = ()

    @property
    def real(self) -> bool:
        """A pin the defender must actually respect."""
        return self.first and self.secure and self.escape is None

    @property
    def complete(self) -> bool:
        """Nothing in the back answers this, and their best try was TESTED.

        The distinction is worth having. A pin over a side whose bench simply
        cannot threaten the pinner is uncontested; a pin that survives a
        Pokemon switching in specifically to answer it, because that Pokemon
        dies across the two turns before it acts, is a stronger claim and the
        one worth committing to.
        """
        return self.real and bool(self.through)


def _moves_first(matrix, attacker, defender) -> bool:
    """Does `attacker` act before `defender`?

    Priority beats speed, which is why this is not just `outspeeds`: a Sucker
    Punch or Aqua Jet pins from the bottom of the speed order.
    """
    out = matrix.threat(attacker, defender)
    inc = matrix.threat(defender, attacker)
    if out.priority != inc.priority:
        return out.priority > inc.priority
    return out.outspeeds


def _kills_outright(matrix, attacker, defender) -> bool:
    """A KO on the WORST roll. A pin that needs a high roll is not a pin --
    the defender is entitled to stay in and take the 1-in-16.
    """
    return matrix.threat(attacker, defender).ohko


def _bench_of(battle, combatant):
    """The Pokemon on `combatant`'s side that are not currently out."""
    for side in (battle.p1, battle.p2):
        if any(c is combatant for c in side.roster):
            active = [c for c in side.active if c is not None]
            return [c for c in side.roster
                    if not c.fainted and not any(c is a for a in active)]
    return []


def _incoming(matrix, attackers, target, only_before_it_acts=False) -> float:
    """Fraction of `target`'s MAX hp our side strips in one turn, worst rolls.

    A sum over both actives, because a switch-in eats whatever we aim at that
    slot -- and focus fire is always available to us. Worst rolls throughout:
    this decides whether a pin holds, and a pin that needs a high roll is not
    one.

    `only_before_it_acts` drops the attackers `target` moves ahead of, which is
    what makes the second turn of the arithmetic honest -- damage that lands
    after it has already fired does not stop it firing.
    """
    return sum(matrix.threat(a, target).dmg_min for a in attackers
               if not only_before_it_acts or _moves_first(matrix, a, target))


def escape_from(matrix, battle, attacker, defender):
    """A switch-in that turns this pin from a threat into a risk, or None.

    Also returns, as the second element, the names of bench Pokemon that DID
    threaten a KO back and still died before firing it.

    THIS TURN + NEXT TURN, and nothing else. In the user's words:

        "Really, this turn + next turn (very quick and simple arithmetic) is all
         that matters. ... there is even potentially a complete pin; if
         Charizard switches in, it will take bullet punch + blizzard, then next
         turn another blizzard (outsped) + bullet punch (priority) which might
         kill, so could be a genuine full pin."

    The first version of this function asked "does B survive the PINNER's hit",
    and that is the wrong sum. Measured on the reference position: Charizard Y
    takes Bullet Punch at 0.25x for 21.2% and survives it easily -- so the pin
    was called off -- but it switches into Bullet Punch AND Blizzard for 78.6%
    guaranteed, and then eats both again next turn, since Blizzard outspeeds it
    and Bullet Punch has priority. 157.1% over two turns: it dies without ever
    acting, and the pin was complete all along.

    So a benched B escapes only when all three hold:

      1. B survives the switch-in turn -- OUR WHOLE SIDE's guaranteed damage,
         not the pinner's alone. It switched, so everything we aim there lands.
      2. B then threatens a guaranteed KO back on the pinner. Without this the
         switch merely absorbs a hit and the pin stands again next turn.
      3. B lives long enough to fire it: turn 1's damage plus everything that
         resolves before it acts on turn 2 still leaves it alive. If it
         outspeeds both of ours that second term is zero and it simply fires;
         if it is slower than both, it eats a second full round first.

    Note what is NOT an escape: Protect. A null turn leaves the board exactly
    as it was and the pin re-applies next turn, so a stall is only worth
    something when the stalled turns themselves pay -- Fake Out, or Tailwind /
    Trick Room / weather / screens ticking down. That is a reason to price
    Protect properly at the horizon, not a reason to call the pin off.

    KNOWN APPROXIMATION. Their other active is still on the field while this
    plays out, and if it removes one of ours on turn 1 the second round is
    smaller than the sum above assumes. `secure` already covers the case where
    something pre-empts and kills the PINNER; a partner traded off mid-sequence
    is not modelled, so a two-turn pin is slightly optimistic when their other
    slot threatens our second attacker.
    """
    ours = [c for c in (matrix.ours if defender in matrix.theirs else matrix.theirs)
            if not c.fainted and _on_field(battle, c)]
    through = []
    for b in _bench_of(battle, defender):
        if _incoming(matrix, ours, b) >= 1.0:
            continue                          # (1) dies on the way in
        if not _kills_outright(matrix, b, attacker):
            continue                          # (2) no answer back
        two_turns = (_incoming(matrix, ours, b)
                     + _incoming(matrix, ours, b, only_before_it_acts=True))
        if two_turns >= 1.0:
            through.append(b.name)            # dead before it fires: still pinned
            continue
        return b, tuple(through)              # (3) it lives, and it answers
    return None, tuple(through)


def pin_between(matrix, attacker, defender, battle=None) -> Pin | None:
    """The pin `attacker` holds over `defender`, if there is one.

    `battle` is optional only so the pairwise callers that do not have one keep
    working; without it the escape check is skipped and the pin is reported on
    the board position alone.
    """
    if attacker.fainted or defender.fainted:
        return None
    if not _kills_outright(matrix, attacker, defender):
        return None
    threat = matrix.threat(attacker, defender)
    first = _moves_first(matrix, attacker, defender)
    # The pinner has to survive to fire. If something on the far side both
    # moves before it and kills it outright, the "pin" is a bluff -- and worse,
    # believing it would talk us into an attack that never resolves.
    allies_of_defender = matrix.theirs if defender in matrix.theirs else matrix.ours
    secure = not any(_kills_outright(matrix, e, attacker)
                     and _moves_first(matrix, e, attacker)
                     for e in allies_of_defender if not e.fainted)
    escape, through = ((None, ()) if battle is None
                       else escape_from(matrix, battle, attacker, defender))
    return Pin(attacker=attacker, defender=defender, move=threat.move,
               first=first, secure=secure, escape=escape, through=through)


def pins_on(matrix, defender, battle=None):
    """Every pin held over `defender`, hardest first."""
    pool = matrix.theirs if defender in matrix.ours else matrix.ours
    found = []
    for a in pool:
        if a.fainted:
            continue
        p = pin_between(matrix, a, defender, battle)
        if p is not None and p.real:
            found.append(p)
    return found


def is_pinned(matrix, defender, battle=None) -> bool:
    return bool(pins_on(matrix, defender, battle))


def all_pins(matrix, battle):
    """Every live pin on the board, both directions."""
    out = []
    for side_a, side_b in ((matrix.ours, matrix.theirs), (matrix.theirs, matrix.ours)):
        for a in side_a:
            if a.fainted or not _on_field(battle, a):
                continue
            for d in side_b:
                if d.fainted or not _on_field(battle, d):
                    continue
                p = pin_between(matrix, a, d, battle)
                if p is not None and p.real:
                    out.append(p)
    return out


def _on_field(battle, c) -> bool:
    return any(x is c for x in battle.p1.active + battle.p2.active)


def acting_order(battle, matrix):
    """Speed order with the pinned Pokemon removed -- the user's "1234 -> 124".

    Returns (order, dropped): both lists of Combatants, `order` fastest first.
    This is a description of the turn, not a rule the engine enforces -- a
    pinned Pokemon may still attack, it just should not expect to resolve.
    """
    field = projected_field(battle)
    live = [(side, c) for side, s in (("p1", battle.p1), ("p2", battle.p2))
            for c in s.active if c is not None and not c.fainted]
    ranked = sorted(live,
                    key=lambda pair: -effective_speed(mega_view(battle, pair[1]),
                                                      field, pair[0]))
    order, dropped = [], []
    for _side, c in ranked:
        (dropped if is_pinned(matrix, c, battle) else order).append(c)
    return order, dropped


@dataclass(frozen=True)
class SafePlay:
    """Protect the Pokemon they are guaranteed to kill; attack with the other.

    Value that does not depend on reading them: `protect` cannot be hit whatever
    they choose, and `attacker` is not under a guaranteed KO, so its move
    resolves either way.
    """
    protect: object
    attacker: object
    pin: Pin


def safe_plays(matrix, battle, side_name="p1"):
    """Every safe protect-and-attack available to `side_name` right now.

    Deliberately narrow. It requires the partner to be under NO guaranteed KO at
    all -- not merely a smaller threat -- because the moment the partner can also
    be removed, protecting one of the two is a choice about which to save, and
    that is a read again.
    """
    ours = [c for c in (battle.p1 if side_name == "p1" else battle.p2).active
            if c is not None and not c.fainted]
    if len(ours) < 2:
        return []
    out = []
    for threatened in ours:
        pins = pins_on(matrix, threatened, battle)
        if not pins:
            continue
        for partner in ours:
            if partner is threatened or is_pinned(matrix, partner, battle):
                continue
            if partner.protected_last_turn:
                continue
            out.append(SafePlay(protect=threatened, attacker=partner, pin=pins[0]))
    return out


def joint_action_score(matrix, battle, joint) -> float:
    """How much this joint action engages the pins on the board.

    Only ever used to ORDER candidates, never to delete them. That distinction
    matters: "stay in and attack while pinned" is not dominated in the game-
    theoretic sense -- it is excellent on the columns where they attack the
    other slot -- so pruning it would change the equilibrium. Sorting it to the
    back does not, and is enough to keep it out of the truncated prefix that a
    nested subgame actually solves.
    """
    if not joint:
        return 0.0
    score = 0.0
    for a in joint:
        actor = a.combatant
        if actor is None or actor.fainted:
            continue
        pinned_here = is_pinned(matrix, actor, battle)

        if a.kind == "move" and a.move is not None and a.targets:
            # Firing a pin: the Pokemon that holds the guaranteed KO uses it.
            for t in a.targets:
                p = pin_between(matrix, actor, t, battle)
                if p is not None and p.real and p.move == a.move.name:
                    score += 3.0
            if pinned_here:
                # Attacking into a guaranteed KO from something faster. Legal,
                # sometimes right, but it is the branch to try last.
                score -= 2.0
        elif a.kind == "protect" and pinned_here:
            score += 2.0     # deny the KO outright
        elif a.kind == "switch" and pinned_here:
            score += 1.5     # get the piece out instead

    # The safe play as a pattern, not as two separate actions: one slot answers
    # a pin, the other attacks free of one. Scored on top of the parts because
    # it is the combination that carries the guarantee.
    protects_pinned = any(a.kind in ("protect", "switch") and a.combatant is not None
                          and is_pinned(matrix, a.combatant, battle)
                          for a in joint)
    free_attacker = any(a.kind == "move" and a.move is not None
                        and a.move.category != "Status"
                        and a.combatant is not None and not a.combatant.fainted
                        and not is_pinned(matrix, a.combatant, battle)
                        for a in joint)
    if protects_pinned and free_attacker:
        score += 2.0
    return score


def rank_joint_actions(matrix, battle, joints):
    """`joints` reordered, most pin-relevant first. Stable, so equal-scoring
    actions keep the order the generator produced them in."""
    order = sorted(range(len(joints)),
                   key=lambda i: (-joint_action_score(matrix, battle, joints[i]), i))
    return [joints[i] for i in order]


def describe(matrix, battle, side_name="p1") -> list[str]:
    """Plain sentences, for the line explanation and the app."""
    lines = []
    for p in all_pins(matrix, battle):
        text = (f"{p.attacker.name} pins {p.defender.name} with {p.move} "
                f"(guaranteed OHKO, moves first) -- {p.defender.name} has "
                f"no switch that answers it, so Protect only defers")
        if p.complete:
            # Name the Pokemon that TRIED. "Nothing in the back answers this"
            # is much stronger when their answer was played out and lost, and
            # this is the line worth committing a lead to.
            text += (f". Complete pin: "
                     + ", ".join(p.through)
                     + " threatens the KO back and still dies before firing it "
                       "(both our attacks on the way in, both again next turn)")
        lines.append(text)
    # A near-pin is worth saying out loud: it is the same threat, and the piece
    # in the back is the entire reason it is not one.
    for side_a, side_b in ((matrix.ours, matrix.theirs), (matrix.theirs, matrix.ours)):
        for a in side_a:
            if a.fainted or not _on_field(battle, a):
                continue
            for d in side_b:
                if d.fainted or not _on_field(battle, d):
                    continue
                p = pin_between(matrix, a, d, battle)
                if p is not None and p.first and p.secure and p.escape is not None:
                    lines.append(
                        f"{a.name} would pin {d.name} with {p.move}, but "
                        f"{p.escape.name} switches in, lives it and KOs back "
                        f"-- a risk, not a threat")
    order, dropped = acting_order(battle, matrix)
    if dropped:
        lines.append("Speed order "
                     + " > ".join(c.name for c in order)
                     + " (pinned, so not counted: "
                     + ", ".join(c.name for c in dropped) + ")")
    for s in safe_plays(matrix, battle, side_name):
        lines.append(f"Safe play: Protect {s.protect.name} (it is under a "
                     f"guaranteed KO from {s.pin.attacker.name}) and attack with "
                     f"{s.attacker.name}, which nothing is guaranteed to KO")
    return lines
