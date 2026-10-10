//! Fixed baseline policies for evaluation (DESIGN.md 3, phase 3 exit test).

use crate::battle::choice::{SideChoice, SideRequest, SlotChoice};
use crate::battle::Battle;
use crate::dex::{Dex, MoveId};

/// Greedy damage: each active Pokemon picks the move and target with the
/// highest expected damage (`Battle::estimate_damage`), Mega Evolving when
/// it can. It switches only when it must. Team preview brings the first
/// four.
pub fn greedy(battle: &Battle, side: usize) -> Option<SideChoice> {
    let legal = battle.legal_choices(side);
    if legal.is_empty() {
        return None;
    }
    if matches!(battle.requests[side], SideRequest::TeamPreview) {
        return legal
            .iter()
            .find(|c| matches!(c, SideChoice::Team([0, 1, 2, 3])))
            .or(legal.first())
            .cloned();
    }
    // Score each slot's options once.
    let mut cache: [Vec<(SlotChoice, f64)>; 2] = [Vec::new(), Vec::new()];
    let mut best: Option<(f64, &SideChoice)> = None;
    for c in &legal {
        let SideChoice::Slots(slots) = c else { continue };
        let mut score = 0.0;
        for (pos, &sc) in slots.iter().enumerate() {
            let s = match cache[pos].iter().find(|(c, _)| *c == sc) {
                Some(&(_, s)) => s,
                None => {
                    let s = slot_score(battle, side, pos, sc);
                    cache[pos].push((sc, s));
                    s
                }
            };
            score += s;
        }
        if best.is_none_or(|(b, _)| score > b) {
            best = Some((score, c));
        }
    }
    best.map(|(_, c)| c.clone()).or(legal.into_iter().next())
}

fn slot_score(battle: &Battle, side: usize, pos: usize, c: SlotChoice) -> f64 {
    match c {
        SlotChoice::Move { slot, target, mega } => {
            let Some(user) = battle.occupant(side, pos) else {
                return 0.0;
            };
            let Some(id) = request_move(battle, side, pos, slot) else {
                return 0.0;
            };
            battle.estimate_damage(user, id, target) + if mega { 0.01 } else { 0.0 }
        }
        // Only when forced (no move scores below zero... unless it hits an
        // ally, which a switch beats).
        SlotChoice::Switch { .. } => -0.5,
        SlotChoice::Pass => 0.0,
    }
}

/// The move a request's slot stands for (Struggle when it's all that's left).
fn request_move(battle: &Battle, side: usize, pos: usize, slot: u8) -> Option<MoveId> {
    let SideRequest::Move(reqs) = &battle.requests[side] else {
        return None;
    };
    let req = reqs[pos].as_ref()?;
    if req.struggle {
        return Dex::get().move_id("struggle");
    }
    req.moves.iter().find(|m| m.slot == slot).map(|m| m.id)
}
