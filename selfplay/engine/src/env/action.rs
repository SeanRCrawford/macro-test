//! Fixed action spaces for training (DESIGN.md 5, action heads).
//!
//! Team preview: one of the 180 ordered picks of 4 from 6 (the first two
//! lead), indexed as in `team::preview_choices`.
//!
//! A move or switch request: one action per active slot, 47 each, and the
//! side's joint action `slot0 * 47 + slot1` (2,209). Per slot:
//! - 0..40: move `m` (0-3), target `t` (0-4), Mega `g` (0-1) at
//!   `(m * 5 + t) * 2 + g`. Targets are Showdown's locations 0 (none),
//!   1 and 2 (the foes' slots), -1 and -2 (ours).
//! - 40..46: switch to the Pokemon at party position 0-5 (Revival Blessing
//!   picks a fainted one; otherwise positions 2-5 are the bench).
//! - 46: pass.

use crate::battle::choice::{SideChoice, SideRequest, SlotChoice};
use crate::battle::Battle;
use crate::team::preview_choices;
use std::sync::OnceLock;

pub const SLOT_ACTIONS: usize = 47;
pub const JOINT_ACTIONS: usize = SLOT_ACTIONS * SLOT_ACTIONS;
pub const PREVIEW_ACTIONS: usize = 180;
/// Mask length: room for either kind of decision.
pub const MASK_LEN: usize = JOINT_ACTIONS;

const TARGETS: [i8; 5] = [0, 1, 2, -1, -2];
const SWITCH: usize = 40;
const PASS: usize = 46;

/// What a side has to decide now.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum Decision {
    /// Nothing (waiting on the other side, or the game is over).
    None = 0,
    Preview = 1,
    /// Moves and switches (also forced switches).
    Slots = 2,
}

pub fn decision(battle: &Battle, side: usize) -> Decision {
    if battle.is_over() {
        return Decision::None;
    }
    match battle.requests[side] {
        SideRequest::Wait => Decision::None,
        SideRequest::TeamPreview => Decision::Preview,
        _ => Decision::Slots,
    }
}

pub fn slot_index(c: SlotChoice) -> usize {
    match c {
        SlotChoice::Move { slot, target, mega } => {
            let t = TARGETS
                .iter()
                .position(|&x| x == target)
                .expect("target location");
            (slot as usize * 5 + t) * 2 + mega as usize
        }
        SlotChoice::Switch { index } => SWITCH + index as usize,
        SlotChoice::Pass => PASS,
    }
}

pub fn slot_choice(i: usize) -> SlotChoice {
    match i {
        0..SWITCH => SlotChoice::Move {
            slot: (i / 10) as u8,
            target: TARGETS[(i / 2) % 5],
            mega: i % 2 == 1,
        },
        SWITCH..PASS => SlotChoice::Switch {
            index: (i - SWITCH) as u8,
        },
        _ => SlotChoice::Pass,
    }
}

fn preview_list() -> &'static [[u8; 4]] {
    static LIST: OnceLock<Vec<[u8; 4]>> = OnceLock::new();
    LIST.get_or_init(preview_choices)
}

/// The action index of a choice.
pub fn index(choice: &SideChoice) -> usize {
    match choice {
        SideChoice::Team(order) => preview_list()
            .iter()
            .position(|o| o == order)
            .expect("a preview order"),
        SideChoice::Slots([a, b]) => slot_index(*a) * SLOT_ACTIONS + slot_index(*b),
    }
}

/// The choice an action index stands for, under `decision`.
pub fn choice(decision: Decision, i: usize) -> Option<SideChoice> {
    match decision {
        Decision::None => None,
        Decision::Preview => preview_list().get(i).map(|&o| SideChoice::Team(o)),
        Decision::Slots if i < JOINT_ACTIONS => Some(SideChoice::Slots([
            slot_choice(i / SLOT_ACTIONS),
            slot_choice(i % SLOT_ACTIONS),
        ])),
        Decision::Slots => None,
    }
}

/// Mark the legal actions in `mask` (MASK_LEN long, 1 = legal).
pub fn legal_mask(battle: &Battle, side: usize, mask: &mut [u8]) -> Decision {
    mask.fill(0);
    let d = decision(battle, side);
    if d != Decision::None {
        for c in battle.legal_choices(side) {
            mask[index(&c)] = 1;
        }
    }
    d
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slot_indices_round_trip() {
        for i in 0..SLOT_ACTIONS {
            assert_eq!(slot_index(slot_choice(i)), i);
        }
        assert_eq!(preview_list().len(), PREVIEW_ACTIONS);
    }
}
