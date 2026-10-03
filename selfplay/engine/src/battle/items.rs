//! Held items with effects outside the damage formula: Focus Sash, Sitrus
//! Berry, Life Orb's recoil, Leftovers and Choice Scarf's lock (data/items.ts).
//! Damage modifiers (Life Orb's boost, type items...) live in `damage`.

use super::conditions::HitRes;
use super::state::{Volatile, VolatileId};
use super::{Battle, MonRef};
use crate::dex::{Dex, MoveId};

impl Battle {
    /// The held item's id, if any.
    pub(super) fn item_of(&self, r: MonRef) -> Option<&'static str> {
        self.mon(r).item.map(|i| Dex::get().item(i).id.as_str())
    }

    /// `useItem` / `eatItem` once the item's own condition passed.
    fn consume_item(&mut self, r: MonRef) -> bool {
        let m = self.mon_mut(r);
        if m.hp == 0 || !m.is_active || m.item.is_none() {
            return false;
        }
        m.item = None;
        true
    }

    /// `eachEvent('Update')`: actives in Speed order; Sitrus Berry is the only
    /// supported Update handler.
    pub(super) fn each_update(&mut self) {
        let mut actives = self.all_active();
        let speeds: Vec<i32> = actives.iter().map(|&r| self.mon(r).speed).collect();
        let mut keyed: Vec<(MonRef, i32)> = actives.drain(..).zip(speeds).collect();
        self.speed_sort(&mut keyed, |a, b| b.1.cmp(&a.1));
        for (r, _) in keyed {
            if self.item_of(r) == Some("sitrusberry") {
                let m = self.mon(r);
                if m.hp > 0 && m.hp as u32 * 2 <= m.max_hp() as u32 {
                    let amount = (m.max_hp() / 4) as u32;
                    // TryEatItem: TryHeal with nothing to block it, so only a
                    // zero amount fails.
                    if amount > 0 && self.consume_item(r) {
                        self.heal(r, amount);
                    }
                }
            }
        }
    }

    /// The Damage event for move damage: Focus Sash.
    pub(super) fn on_move_damage(&mut self, t: MonRef, damage: u32) -> u32 {
        if self.item_of(t) == Some("focussash") {
            let m = self.mon(t);
            if m.hp == m.max_hp() && damage >= m.hp as u32 {
                let hp = m.hp as u32;
                if self.consume_item(t) {
                    return hp - 1;
                }
            }
        }
        damage
    }

    /// AfterMoveSecondarySelf: Life Orb's recoil after an attack.
    pub(super) fn after_move_secondary_self(&mut self, user: MonRef, target: MonRef, is_status: bool) {
        if self.item_of(user) == Some("lifeorb") && user != target && !is_status && !self.mon(user).force_switch_flag {
            let amount = (self.mon(user).max_hp() / 10) as u32;
            self.effect_damage(user, amount);
        }
    }

    /// Choice Scarf's onModifyMove: lock into the move being used.
    pub(super) fn choice_lock(&mut self, user: MonRef, move_id: MoveId) {
        if self.item_of(user) != Some("choicescarf") {
            return;
        }
        let m = self.mon_mut(user);
        if m.hp == 0 || m.volatiles.has(VolatileId::ChoiceLock) {
            return;
        }
        m.volatiles.0.push(Volatile { id: VolatileId::ChoiceLock, duration: None, counter: 0, move_id: Some(move_id) });
    }

    /// choicelock's onBeforeMove. False: the move is blocked.
    pub(super) fn choice_lock_allows(&mut self, user: MonRef, move_id: MoveId) -> bool {
        let Some(locked) = self.mon(user).volatiles.0.iter().find(|v| v.id == VolatileId::ChoiceLock).and_then(|v| v.move_id) else {
            return true;
        };
        if self.item_of(user) != Some("choicescarf") {
            self.mon_mut(user).volatiles.remove(VolatileId::ChoiceLock);
            return true;
        }
        move_id == locked || Dex::get().move_data(move_id).id == "struggle"
    }

    /// choicelock's onDisableMove. Returns the locked move, if it holds.
    pub(super) fn choice_locked_move(&mut self, user: MonRef) -> Option<MoveId> {
        let locked = self.mon(user).volatiles.0.iter().find(|v| v.id == VolatileId::ChoiceLock)?.move_id?;
        if self.item_of(user) != Some("choicescarf") || self.mon(user).move_slot(locked).is_none() {
            self.mon_mut(user).volatiles.remove(VolatileId::ChoiceLock);
            return None;
        }
        Some(locked)
    }

    /// Choice Scarf's onModifySpe, as a modifier (4096 = none).
    pub(super) fn speed_modifier(&self, r: MonRef) -> u32 {
        if self.item_of(r) == Some("choicescarf") {
            6144
        } else {
            4096
        }
    }

    /// Leftovers' residual heal.
    pub(super) fn leftovers(&mut self, r: MonRef) -> HitRes {
        let amount = (self.mon(r).max_hp() / 16).max(1) as u32;
        self.heal(r, amount)
    }
}
