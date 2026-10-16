//! Requests (what a player must decide) and choices (what they decide), in a
//! form that converts to and from Showdown's choice strings.

use super::state::ACTIVE_PER_SIDE;
use super::Battle;
use crate::dex::{Dex, MoveId, MoveTarget};

/// One active slot's decision.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SlotChoice {
    /// `slot` is the move slot (0-3). `target` is Showdown's target location:
    /// 1 or 2 for a foe's slot, -1 or -2 for one of ours, 0 for none.
    Move {
        slot: u8,
        target: i8,
        mega: bool,
    },
    /// Switch to the Pokemon at this index of the side's list.
    Switch {
        index: u8,
    },
    Pass,
}

/// A side's whole decision for one request.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum SideChoice {
    /// Indices into the six, in order; the first two lead.
    Team([u8; 4]),
    Slots([SlotChoice; ACTIVE_PER_SIDE]),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MoveOption {
    pub slot: u8,
    pub id: MoveId,
    pub pp: u8,
    pub disabled: bool,
    /// Shown as usable but refused (Imprison on the last active Pokemon),
    /// unless no other move is usable, when choosing it means Struggle.
    pub hidden: bool,
    pub target: MoveTarget,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SlotRequest {
    /// Usable moves; a single Struggle when nothing else is.
    pub moves: Vec<MoveOption>,
    pub struggle: bool,
    pub can_mega: bool,
    pub trapped: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SideRequest {
    TeamPreview,
    /// Per active slot; None for an empty or fainted slot (it passes).
    Move([Option<SlotRequest>; ACTIVE_PER_SIDE]),
    /// Which active slots must switch.
    Switch([bool; ACTIVE_PER_SIDE]),
    /// Nothing to decide; waiting on the other side.
    Wait,
}

/// Showdown's target location of `target` (a slot on `target_side`) as seen
/// from `source_side`.
pub fn loc_of(source_side: usize, target_side: usize, target_pos: usize) -> i8 {
    let p = target_pos as i8 + 1;
    if source_side == target_side {
        -p
    } else {
        p
    }
}

/// Showdown's `validTargetLoc` for doubles, where every position is adjacent
/// to every other except across... (in doubles all foes and the ally are
/// adjacent; a Pokemon is never adjacent to itself).
pub fn valid_target_loc(loc: i8, source_pos: usize, target: MoveTarget) -> bool {
    if loc == 0 {
        return true;
    }
    if loc.unsigned_abs() as usize > ACTIVE_PER_SIDE {
        return false;
    }
    let source_loc = -(source_pos as i8 + 1);
    let is_self = loc == source_loc;
    let is_foe = loc > 0;
    let adjacent = if is_foe {
        true
    } else {
        (loc - source_loc).abs() == 1
    };
    match target {
        MoveTarget::RandomNormal | MoveTarget::Scripted | MoveTarget::Normal => adjacent,
        MoveTarget::AdjacentAlly => adjacent && !is_foe,
        MoveTarget::AdjacentAllyOrSelf => (adjacent && !is_foe) || is_self,
        MoveTarget::AdjacentFoe => adjacent && is_foe,
        MoveTarget::Any => !is_self,
        _ => false,
    }
}

/// Showdown's `targetTypeChoices`: move targets that need a chosen location.
pub fn needs_target(target: MoveTarget) -> bool {
    matches!(
        target,
        MoveTarget::Normal
            | MoveTarget::Any
            | MoveTarget::AdjacentAlly
            | MoveTarget::AdjacentAllyOrSelf
            | MoveTarget::AdjacentFoe
    )
}

impl SlotChoice {
    pub fn to_showdown(self, request: &SideRequest, slot: usize) -> String {
        match self {
            SlotChoice::Move {
                slot: m,
                target,
                mega,
            } => {
                let mut s = format!("move {}", m + 1);
                if let SideRequest::Move(slots) = request {
                    if slots[slot].as_ref().is_some_and(|r| r.struggle) {
                        return "move 1".into();
                    }
                }
                if target != 0 {
                    s += &format!(" {target}");
                }
                if mega {
                    s += " mega";
                }
                s
            }
            SlotChoice::Switch { index } => format!("switch {}", index + 1),
            SlotChoice::Pass => "pass".into(),
        }
    }

    /// Parse one slot's part of a Showdown choice ("move 2 1 mega", "switch 3", "pass").
    pub fn from_showdown(text: &str) -> Result<Self, String> {
        let words: Vec<&str> = text.split_whitespace().collect();
        match words.first().copied() {
            Some("move") => {
                let mut slot = None;
                let mut target = 0i8;
                let mut mega = false;
                for w in &words[1..] {
                    if *w == "mega" {
                        mega = true;
                    } else if let Ok(n) = w.parse::<i8>() {
                        if slot.is_none() {
                            slot = Some(n);
                        } else {
                            target = n;
                        }
                    } else {
                        return Err(format!("unsupported choice word {w:?} in {text:?}"));
                    }
                }
                let slot = slot.ok_or_else(|| format!("no move in {text:?}"))?;
                Ok(SlotChoice::Move {
                    slot: (slot - 1) as u8,
                    target,
                    mega,
                })
            }
            Some("switch") => {
                let n: u8 = words
                    .get(1)
                    .and_then(|w| w.parse().ok())
                    .ok_or_else(|| format!("bad switch {text:?}"))?;
                Ok(SlotChoice::Switch { index: n - 1 })
            }
            Some("pass") => Ok(SlotChoice::Pass),
            _ => Err(format!("unknown choice {text:?}")),
        }
    }
}

impl SideChoice {
    pub fn to_showdown(&self, request: &SideRequest) -> String {
        match self {
            SideChoice::Team(order) => format!(
                "team {}",
                order
                    .iter()
                    .map(|i| (i + 1).to_string())
                    .collect::<String>()
            ),
            SideChoice::Slots(slots) => slots
                .iter()
                .enumerate()
                .map(|(i, c)| c.to_showdown(request, i))
                .collect::<Vec<_>>()
                .join(", "),
        }
    }

    pub fn from_showdown(text: &str) -> Result<Self, String> {
        let text = text.trim();
        if let Some(order) = text.strip_prefix("team ") {
            let digits: Vec<u8> = order.trim().bytes().map(|b| b.wrapping_sub(b'1')).collect();
            if digits.len() != 4 || digits.iter().any(|&d| d > 5) {
                return Err(format!("bad team order {text:?}"));
            }
            return Ok(SideChoice::Team([
                digits[0], digits[1], digits[2], digits[3],
            ]));
        }
        let parts: Vec<&str> = text.split(',').map(str::trim).collect();
        if parts.len() != ACTIVE_PER_SIDE {
            return Err(format!(
                "expected {ACTIVE_PER_SIDE} slot choices in {text:?}"
            ));
        }
        Ok(SideChoice::Slots([
            SlotChoice::from_showdown(parts[0])?,
            SlotChoice::from_showdown(parts[1])?,
        ]))
    }
}

impl Battle {
    /// Every choice for one active slot, ignoring joint constraints.
    pub fn slot_options(&self, side: usize, slot: usize) -> Vec<SlotChoice> {
        let mut out = Vec::new();
        match &self.requests[side] {
            SideRequest::Move(slots) => {
                let Some(req) = &slots[slot] else {
                    out.push(SlotChoice::Pass);
                    return out;
                };
                let real_exists = req.moves.iter().any(|m| !m.disabled && !m.hidden);
                for m in &req.moves {
                    if m.disabled || (real_exists && m.hidden) {
                        continue;
                    }
                    let targets: Vec<i8> = if needs_target(m.target) && !req.struggle {
                        [1, 2, -1, -2]
                            .into_iter()
                            .filter(|&l| valid_target_loc(l, slot, m.target))
                            .collect()
                    } else {
                        vec![0]
                    };
                    for t in targets {
                        out.push(SlotChoice::Move {
                            slot: m.slot,
                            target: t,
                            mega: false,
                        });
                        if req.can_mega {
                            out.push(SlotChoice::Move {
                                slot: m.slot,
                                target: t,
                                mega: true,
                            });
                        }
                    }
                }
                if !req.trapped {
                    for i in self.switchable(side) {
                        out.push(SlotChoice::Switch { index: i as u8 });
                    }
                }
            }
            SideRequest::Switch(force) => {
                if force[slot] && self.sides[side].revival_blessing[slot] {
                    // Revival Blessing: any fainted party member.
                    for (i, m) in self.sides[side].pokemon.iter().enumerate() {
                        if m.fainted {
                            out.push(SlotChoice::Switch { index: i as u8 });
                        }
                    }
                } else if force[slot] {
                    for i in self.switchable(side) {
                        out.push(SlotChoice::Switch { index: i as u8 });
                    }
                }
                out.push(SlotChoice::Pass);
            }
            _ => {}
        }
        out
    }

    /// Bench Pokemon that can come in.
    pub fn switchable(&self, side: usize) -> Vec<usize> {
        self.sides[side]
            .pokemon
            .iter()
            .enumerate()
            .filter(|(i, m)| *i >= ACTIVE_PER_SIDE && !m.fainted)
            .map(|(i, _)| i)
            .collect()
    }

    /// Every legal choice for a side under its current request.
    pub fn legal_choices(&self, side: usize) -> Vec<SideChoice> {
        match &self.requests[side] {
            SideRequest::TeamPreview => crate::team::preview_choices()
                .into_iter()
                .filter(|o| {
                    o.iter()
                        .all(|&i| (i as usize) < self.sides[side].pokemon.len())
                })
                .map(SideChoice::Team)
                .collect(),
            SideRequest::Wait => Vec::new(),
            req => {
                let a = self.slot_options(side, 0);
                let b = self.slot_options(side, 1);
                let mut out = Vec::new();
                for &x in &a {
                    for &y in &b {
                        let c = [x, y];
                        if self.joint_ok(side, req, &c) {
                            out.push(SideChoice::Slots(c));
                        }
                    }
                }
                out
            }
        }
    }

    /// Constraints between the two slots: no two switches to the same
    /// Pokemon, one Mega per side, and in a switch request exactly as many
    /// passes as there are switch slots that can't be filled.
    fn joint_ok(&self, side: usize, req: &SideRequest, c: &[SlotChoice; 2]) -> bool {
        if let (SlotChoice::Switch { index: a }, SlotChoice::Switch { index: b }) = (c[0], c[1]) {
            if a == b {
                return false;
            }
        }
        if matches!(c[0], SlotChoice::Move { mega: true, .. })
            && matches!(c[1], SlotChoice::Move { mega: true, .. })
        {
            return false;
        }
        if let SideRequest::Switch(force) = req {
            // clearChoice's forced switches and passes, used up slot by
            // slot (a revive takes a switch if one is left).
            let need = force.iter().filter(|&&f| f).count();
            let mut switches = need.min(self.switchable(side).len());
            let mut passes = need - switches;
            for i in 0..2 {
                match c[i] {
                    _ if !force[i] => {
                        if c[i] != SlotChoice::Pass {
                            return false;
                        }
                    }
                    SlotChoice::Pass => {
                        if passes == 0 {
                            return false;
                        }
                        passes -= 1;
                    }
                    _ if self.sides[side].revival_blessing[i] => {
                        switches = switches.saturating_sub(1)
                    }
                    _ => {
                        if switches == 0 {
                            return false;
                        }
                        switches -= 1;
                    }
                }
            }
            return switches == 0;
        }
        true
    }

    pub fn move_name(id: MoveId) -> &'static str {
        &Dex::get().move_data(id).name
    }
}
