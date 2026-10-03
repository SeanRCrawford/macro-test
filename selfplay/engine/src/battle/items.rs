//! Held items with effects outside the damage formula: Focus Sash, Sitrus
//! Berry, Life Orb's recoil, Leftovers and Choice Scarf's lock (data/items.ts).
//! Damage modifiers (Life Orb's boost, type items...) live in `damage`.

use super::conditions::HitRes;
use super::state::{Volatile, VolatileId, ACTIVE_PER_SIDE};
use crate::damage::Terrain;
use super::{Battle, MonRef};
use crate::dex::{Dex, ItemId, MoveId};

impl Battle {
    /// The held item's id, if any.
    pub(super) fn item_of(&self, r: MonRef) -> Option<&'static str> {
        self.mon(r).item.map(|i| Dex::get().item(i).id.as_str())
    }

    /// `useItem` / `eatItem` once the item's own condition passed. The
    /// caller runs the item's effect, then `after_use_item`.
    fn consume_item(&mut self, r: MonRef) -> bool {
        let m = self.mon_mut(r);
        if m.hp == 0 || !m.is_active || m.item.is_none() {
            return false;
        }
        m.item = None;
        true
    }

    /// AfterUseItem: Unburden.
    pub(super) fn after_use_item(&mut self, r: MonRef) {
        if self.ability_is(r, "unburden") {
            self.add_volatile(r, VolatileId::Unburden);
        }
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
                        self.after_use_item(r);
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
                    self.after_use_item(t);
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
        m.volatiles.0.push(Volatile { id: VolatileId::ChoiceLock, duration: None, counter: 0, move_id: Some(move_id), effect_order: 0 });
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

    /// A terrain seed's terrain and the stat it raises.
    fn seed(item: &str) -> Option<(Terrain, usize)> {
        Some(match item {
            "electricseed" => (Terrain::Electric, 1),
            "grassyseed" => (Terrain::Grassy, 1),
            "mistyseed" => (Terrain::Misty, 3),
            "psychicseed" => (Terrain::Psychic, 3),
            _ => return None,
        })
    }

    /// A seed's onStart / onTerrainChange: used once its terrain is up.
    pub(super) fn try_seed(&mut self, r: MonRef) {
        let Some((terrain, stat)) = self.item_of(r).and_then(Self::seed) else { return };
        let m = self.mon(r);
        if self.field.terrain != terrain || m.hp == 0 || !m.is_active {
            return;
        }
        // useItem: the item's boosts, then it's gone.
        self.boost(r, &[(stat, 1)], Some(r));
        if self.consume_item(r) {
            self.after_use_item(r);
        }
    }

    /// eachEvent('TerrainChange'): seeds, in Speed order.
    pub(super) fn terrain_change(&mut self) {
        let actives = self.all_active();
        let mut keyed: Vec<(MonRef, i32)> = actives.iter().map(|&r| (r, self.mon(r).speed)).collect();
        self.speed_sort(&mut keyed, |a, b| b.1.cmp(&a.1));
        for (r, _) in keyed {
            self.try_seed(r);
        }
    }

    /// White Herb's check: clear lowered stats, using the item.
    pub(super) fn white_herb(&mut self, r: MonRef) {
        if self.item_of(r) != Some("whiteherb") {
            return;
        }
        let m = self.mon(r);
        if m.hp == 0 || !m.is_active || !m.boosts.iter().any(|&b| b < 0) {
            return;
        }
        let m = self.mon_mut(r);
        for b in m.boosts.iter_mut() {
            if *b < 0 {
                *b = 0;
            }
        }
        if self.consume_item(r) {
            self.after_use_item(r);
        }
    }

    /// An event's onAny handlers for White Herb (AfterMove, AfterMega): every
    /// holder among `around`'s allies, itself and its foes, in Speed order.
    pub(super) fn any_white_herb(&mut self, around: MonRef) {
        let mut holders: Vec<MonRef> = Vec::new();
        for side in [around.side, 1 - around.side] {
            for pos in 0..ACTIVE_PER_SIDE {
                if let Some(r) = self.occupant(side, pos) {
                    let m = self.mon(r);
                    if m.hp > 0 && !m.fainted && self.item_of(r) == Some("whiteherb") {
                        holders.push(r);
                    }
                }
            }
        }
        let mut keyed: Vec<(MonRef, i32)> = holders.iter().map(|&r| (r, self.mon(r).speed)).collect();
        self.speed_sort(&mut keyed, |a, b| b.1.cmp(&a.1));
        for (r, _) in keyed {
            self.white_herb(r);
        }
    }

    /// DamagingHit for the defenders an attack damaged: Rocky Helmet (order 2)
    /// for contact moves, then freeze's thaw from Fire attacks.
    pub(super) fn damaging_hit(&mut self, user: MonRef, damaged: &[MonRef], contact: bool, fire: bool) {
        let mut helmets: Vec<(MonRef, i32)> = damaged
            .iter()
            .filter(|&&t| contact && self.item_of(t) == Some("rockyhelmet"))
            .map(|&t| (t, self.mon(t).speed))
            .collect();
        self.speed_sort(&mut helmets, |a, b| b.1.cmp(&a.1));
        for _ in helmets {
            let amount = (self.mon(user).max_hp() / 6) as u32;
            self.effect_damage(user, amount);
        }
        if fire {
            for &t in damaged {
                if self.mon(t).status == crate::damage::Status::Freeze {
                    self.cure_status(t);
                }
            }
        }
    }

    /// The defender's resist berry, eaten as the damage is calculated.
    pub(super) fn eat_resist_berry(&mut self, t: MonRef) {
        if self.consume_item(t) {
            self.after_use_item(t);
        }
    }

    /// `takeItem`: the TakeItem event lets Mega Stones stay with a Pokemon that
    /// can use them (`checker` is whose species counts) and Unburden notice.
    pub(super) fn take_item(&mut self, holder: MonRef, checker: MonRef) -> Option<ItemId> {
        let m = self.mon(holder);
        if !m.is_active {
            return None;
        }
        let item = m.item?;
        if !self.can_take(item, checker) {
            return None;
        }
        if self.ability_is(holder, "unburden") {
            self.add_volatile(holder, VolatileId::Unburden);
        }
        self.mon_mut(holder).item = None;
        Some(item)
    }

    /// An item's onTakeItem: some can't be removed, and a Mega Stone stays
    /// with a Pokemon of the species it evolves.
    fn can_take(&self, item: ItemId, checker: MonRef) -> bool {
        let dex = Dex::get();
        let data = dex.item(item);
        let base = &dex.species(self.mon(checker).species).base_species;
        !data.take_forbidden && !data.mega_stone.contains_key(base)
    }

    /// Trick's onHit: swap items. False when it fails.
    pub(super) fn trick(&mut self, user: MonRef, target: MonRef) -> bool {
        // takeItem on each: None = no item, Some(None) = blocked (false).
        let take = |b: &mut Battle, r: MonRef| -> Option<Option<ItemId>> {
            let item = b.mon(r).item?;
            if !b.can_take(item, r) {
                return Some(None);
            }
            if b.ability_is(r, "unburden") {
                b.add_volatile(r, VolatileId::Unburden);
            }
            b.mon_mut(r).item = None;
            Some(Some(item))
        };
        let yours = take(self, target);
        let mine = take(self, user);
        let restore = |b: &mut Battle| {
            if let Some(Some(i)) = yours {
                b.mon_mut(target).item = Some(i);
            }
            if let Some(Some(i)) = mine {
                b.mon_mut(user).item = Some(i);
            }
        };
        let blocked = yours == Some(None) || mine == Some(None);
        let (yours, mine) = (yours.flatten(), mine.flatten());
        if blocked || (yours.is_none() && mine.is_none()) {
            restore(self);
            return false;
        }
        // The second TakeItem: can the receiver hold it?
        if mine.is_some_and(|i| !self.can_take(i, target)) || yours.is_some_and(|i| !self.can_take(i, user)) {
            self.mon_mut(target).item = yours;
            self.mon_mut(user).item = mine;
            return false;
        }
        // setItem (needs HP and to be active).
        for (r, item) in [(target, mine), (user, yours)] {
            let m = self.mon_mut(r);
            if item.is_some() && m.hp > 0 && m.is_active {
                m.item = item;
            }
        }
        true
    }
}
