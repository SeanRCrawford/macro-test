//! Held items with effects outside the damage formula: Focus Sash, Sitrus
//! Berry, Life Orb's recoil, Leftovers and Choice Scarf's lock (data/items.ts).
//! Damage modifiers (Life Orb's boost, type items...) live in `damage`.

use super::conditions::HitRes;
use super::state::{SwitchFlag, Volatile, VolatileId, ACTIVE_PER_SIDE};
use super::{Battle, MonRef};
use crate::damage::Terrain;
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
        m.last_item = m.item.take();
        true
    }

    /// `useItem` with its AfterUseItem.
    pub(super) fn use_item(&mut self, r: MonRef) -> bool {
        if !self.consume_item(r) {
            return false;
        }
        self.after_use_item(r);
        true
    }

    /// AfterMoveSecondary on the move's targets, all handlers sorted
    /// together: frz thaws a target (thawsTarget), Eject Button (priority 2),
    /// Red Card.
    pub(super) fn after_move_secondary(
        &mut self,
        targets: &[MonRef],
        taken: &[u32],
        total: u32,
        user: MonRef,
        data: &crate::dex::MoveData,
    ) {
        #[derive(Clone, Copy)]
        enum H {
            Thaw,
            EjectButton,
            RedCard,
            Pickpocket,
            Berserk(u32),
        }
        // (holder, kind, priority, speed, subOrder)
        let mut hs: Vec<(MonRef, H, i32, i32, u8)> = Vec::new();
        for (i, &t) in targets.iter().enumerate() {
            let speed = self.mon(t).speed;
            if self.mon(t).status == crate::damage::Status::Freeze {
                hs.push((t, H::Thaw, 0, speed, 0));
            }
            // ignoringAbility: a fainted (inactive) Pokemon's ability does nothing.
            if self.ability_is(t, "pickpocket") && self.mon(t).is_active {
                hs.push((t, H::Pickpocket, 0, speed, 7));
            }
            if self.ability_is(t, "berserk") && self.mon(t).is_active {
                hs.push((t, H::Berserk(taken[i]), 0, speed, 7));
            }
            match self.item_of(t) {
                Some("ejectbutton") => hs.push((t, H::EjectButton, 2, speed, 8)),
                Some("redcard") => hs.push((t, H::RedCard, 0, speed, 8)),
                _ => {}
            }
        }
        self.speed_sort(&mut hs, |a, b| {
            b.2.cmp(&a.2).then(b.3.cmp(&a.3)).then(a.4.cmp(&b.4))
        });
        let attack = data.category != crate::dex::Category::Status;
        for (t, h, _, _, _) in hs {
            match h {
                H::Thaw => {
                    if data.has_key("thawsTarget")
                        && self.mon(t).status == crate::damage::Status::Freeze
                    {
                        self.cure_status(t);
                    }
                }
                H::EjectButton => {
                    if user == t
                        || self.mon(t).hp == 0
                        || !attack
                        || self.item_of(t) != Some("ejectbutton")
                    {
                        continue;
                    }
                    let m = self.mon(t);
                    if self.switchable(t.side).is_empty()
                        || m.force_switch_flag
                        || m.being_called_back
                    {
                        continue;
                    }
                    if self
                        .all_active()
                        .iter()
                        .any(|&a| self.mon(a).switch_flag == Some(SwitchFlag::Replace))
                    {
                        continue;
                    }
                    self.mon_mut(t).switch_flag = Some(SwitchFlag::Replace);
                    if !self.use_item(t) {
                        self.mon_mut(t).switch_flag = None;
                    }
                }
                H::Pickpocket => {
                    let m = self.mon(t);
                    if user == t
                        || !self.contact(data)
                        || m.item.is_some()
                        || m.switch_flag.is_some()
                        || m.force_switch_flag
                    {
                        continue;
                    }
                    if self.mon(user).switch_flag == Some(SwitchFlag::Replace) {
                        continue;
                    }
                    let Some(item) = self.take_item(user, t) else {
                        continue;
                    };
                    // setItem fails on a Pokemon with no HP: the item goes back.
                    if self.mon(t).hp == 0 || !self.mon(t).is_active {
                        self.mon_mut(user).item = Some(item);
                    } else {
                        self.mon_mut(t).item = Some(item);
                    }
                }
                H::Berserk(damage) => {
                    self.mon_mut(t).berserk_checked = true;
                    let m = self.mon(t);
                    let (hp, max) = (m.hp as u32, m.max_hp() as u32);
                    if user != t && hp > 0 && total > 0 && hp * 2 <= max && (hp + damage) * 2 > max
                    {
                        self.boost(t, &[(2, 1)], Some(t));
                    }
                }
                H::RedCard => {
                    if user == t
                        || self.mon(user).hp == 0
                        || self.mon(t).hp == 0
                        || !attack
                        || self.item_of(t) != Some("redcard")
                    {
                        continue;
                    }
                    let (u, m) = (self.mon(user), self.mon(t));
                    if !u.is_active
                        || self.switchable(user.side).is_empty()
                        || u.force_switch_flag
                        || m.force_switch_flag
                    {
                        continue;
                    }
                    // DragOut: Guard Dog stays.
                    if self.use_item(t) && !self.resists_drag(user) {
                        self.mon_mut(user).force_switch_flag = true;
                    }
                }
            }
        }
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
            // Abilities (subOrder 7) before items (8): Thermal Exchange cures
            // burns, Trace keeps looking.
            if (self.ability_is(r, "thermalexchange") || self.ability_is(r, "waterbubble"))
                && self.mon(r).status == crate::damage::Status::Burn
            {
                self.cure_status(r);
            }
            // Disguise's onUpdate: Mimikyu-Busted for good, at an eighth's cost.
            if self.mon(r).disguise_busted && self.ability_is(r, "disguise") {
                let dex = Dex::get();
                let busted = dex.species_id("Mimikyu-Busted").expect("Mimikyu-Busted");
                let m = self.mon_mut(r);
                m.disguise_busted = false;
                m.species = busted;
                m.base_species = busted;
                m.set_types(dex.species(busted).types);
                let amount = (self.mon(r).max_hp() / 8) as u32;
                self.effect_damage(r, amount);
            }
            if self.ability_is(r, "owntempo") {
                self.mon_mut(r).volatiles.remove(VolatileId::Confusion);
            }
            self.trace_update(r);
            if self.ability_is(r, "oblivious") {
                self.mon_mut(r).volatiles.remove(VolatileId::Taunt);
            }
            self.item_update(r);
            // TryEatItem: Unnerve, and Sitrus's TryHeal (Heal Block).
            // Berserk's TryEatItem holds healing berries until its check.
            let berserk_hold = self.ability_is(r, "berserk") && !self.mon(r).berserk_checked;
            if self.item_of(r) == Some("sitrusberry")
                && !berserk_hold
                && !self.unnerved(r)
                && !self.mon(r).volatiles.has(VolatileId::HealBlock)
            {
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

    /// A foe's Unnerve: berries can't be eaten (onFoeTryEatItem).
    pub(super) fn unnerved(&self, r: MonRef) -> bool {
        self.adjacent_foes(r)
            .into_iter()
            .any(|f| self.ability_is(f, "unnerve"))
    }

    /// The Damage event for move damage: Sturdy (priority -30), then Focus
    /// Sash (-40).
    pub(super) fn on_move_damage(&mut self, t: MonRef, damage: u32) -> u32 {
        // Disguise (onDamage priority 1): the disguise takes it all.
        if self.ability_is(t, "disguise")
            && Dex::get().species(self.mon(t).species).name == "Mimikyu"
        {
            self.mon_mut(t).disguise_busted = true;
            return 0;
        }
        let m = self.mon(t);
        let damage = if self.ability_is(t, "sturdy") && m.hp == m.max_hp() && damage >= m.hp as u32
        {
            m.hp as u32 - 1
        } else {
            damage
        };
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
    /// Life Orb's recoil (priority 0) or Shell Bell's heal (-1).
    pub(super) fn after_move_secondary_self(
        &mut self,
        user: MonRef,
        target: MonRef,
        is_status: bool,
        total_damage: u32,
    ) {
        match self.item_of(user) {
            Some("lifeorb")
                if user != target && !is_status && !self.mon(user).force_switch_flag =>
            {
                let amount = (self.mon(user).max_hp() / 10) as u32;
                self.effect_damage(user, amount);
            }
            Some("shellbell") if total_damage > 0 && !self.mon(user).force_switch_flag => {
                // heal(totalDamage / 8): a fraction rounds up to 1, else down.
                self.heal(user, (total_damage / 8).max(1));
            }
            _ => {}
        }
    }

    /// `eatItem` for a berry: Unnerve stops it; then the item is gone.
    fn eat_berry(&mut self, r: MonRef) -> bool {
        if self.unnerved(r) || !self.consume_item(r) {
            return false;
        }
        true
    }

    /// A berry's onEat for `eater` (Bug Bite eats the target's).
    pub(super) fn eat_effect(&mut self, eater: MonRef, item: ItemId) {
        use crate::damage::Status;
        match Dex::get().item(item).id.as_str() {
            "sitrusberry" => {
                let amount = (self.mon(eater).max_hp() / 4) as u32;
                self.heal(eater, amount);
            }
            "lumberry" => {
                self.cure_status(eater);
                self.mon_mut(eater).volatiles.remove(VolatileId::Confusion);
            }
            "chestoberry" if self.mon(eater).status == Status::Sleep => {
                self.cure_status(eater);
            }
            "leppaberry" => {
                let m = self.mon_mut(eater);
                let slot = m
                    .moves
                    .iter()
                    .position(|s| s.pp == 0)
                    .or_else(|| m.moves.iter().position(|s| s.pp < s.max_pp));
                if let Some(i) = slot {
                    m.moves[i].pp = (m.moves[i].pp + 10).min(m.moves[i].max_pp);
                }
            }
            _ => {}
        }
    }

    /// Update for status-curing and PP items: Mental Herb, Lum, Chesto, Leppa.
    fn item_update(&mut self, r: MonRef) {
        use super::state::VolatileId as V;
        use crate::damage::Status;
        let m = self.mon(r);
        if m.hp == 0 {
            return;
        }
        match self.item_of(r) {
            Some("mentalherb") => {
                let cured = [V::Taunt, V::Encore, V::Disable, V::HealBlock];
                if cured.iter().any(|&v| m.volatiles.has(v)) && self.consume_item(r) {
                    for v in cured {
                        self.mon_mut(r).volatiles.remove(v);
                    }
                    self.after_use_item(r);
                }
            }
            Some("lumberry") if m.status != Status::None || m.volatiles.has(V::Confusion) => {
                if self.eat_berry(r) {
                    self.cure_status(r);
                    self.mon_mut(r).volatiles.remove(V::Confusion);
                    self.after_use_item(r);
                }
            }
            Some("chestoberry") if m.status == Status::Sleep => {
                if self.eat_berry(r) {
                    self.cure_status(r);
                    self.after_use_item(r);
                }
            }
            Some("leppaberry") if m.moves.iter().any(|s| s.pp == 0) && self.eat_berry(r) => {
                let m = self.mon_mut(r);
                let slot = m
                    .moves
                    .iter()
                    .position(|s| s.pp == 0)
                    .or_else(|| m.moves.iter().position(|s| s.pp < s.max_pp));
                if let Some(i) = slot {
                    m.moves[i].pp = (m.moves[i].pp + 10).min(m.moves[i].max_pp);
                }
                self.after_use_item(r);
            }
            _ => {}
        }
    }

    /// AfterSetStatus: Lum Berry cures the status at once.
    pub(super) fn after_set_status(&mut self, r: MonRef) {
        if self.item_of(r) == Some("lumberry") && self.eat_berry(r) {
            self.cure_status(r);
            self.mon_mut(r)
                .volatiles
                .remove(super::state::VolatileId::Confusion);
            self.after_use_item(r);
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
        m.volatiles.0.push(Volatile {
            id: VolatileId::ChoiceLock,
            duration: None,
            counter: 0,
            move_id: Some(move_id),
            effect_order: 0,
            target_loc: 0,
        });
    }

    /// choicelock's onBeforeMove. False: the move is blocked.
    pub(super) fn choice_lock_allows(&mut self, user: MonRef, move_id: MoveId) -> bool {
        let Some(locked) = self
            .mon(user)
            .volatiles
            .0
            .iter()
            .find(|v| v.id == VolatileId::ChoiceLock)
            .and_then(|v| v.move_id)
        else {
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
        let locked = self
            .mon(user)
            .volatiles
            .0
            .iter()
            .find(|v| v.id == VolatileId::ChoiceLock)?
            .move_id?;
        if self.item_of(user) != Some("choicescarf") || self.mon(user).move_slot(locked).is_none() {
            self.mon_mut(user).volatiles.remove(VolatileId::ChoiceLock);
            return None;
        }
        Some(locked)
    }

    /// Choice Scarf's onModifySpe, as a modifier (4096 = none).
    pub(super) fn speed_modifier(&self, r: MonRef) -> u32 {
        match self.item_of(r) {
            Some("choicescarf") => 6144,
            Some("ironball") => 2048,
            _ => 4096,
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
        let Some((terrain, stat)) = self.item_of(r).and_then(Self::seed) else {
            return;
        };
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
        let mut keyed: Vec<(MonRef, i32)> =
            actives.iter().map(|&r| (r, self.mon(r).speed)).collect();
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
        let mut keyed: Vec<(MonRef, i32)> =
            holders.iter().map(|&r| (r, self.mon(r).speed)).collect();
        self.speed_sort(&mut keyed, |a, b| b.1.cmp(&a.1));
        for (r, _) in keyed {
            self.white_herb(r);
        }
    }

    /// DamagingHit for the defenders an attack damaged: Rough Skin (order 1),
    /// Rocky Helmet (2), then by holder Speed the rest (Stamina, Flame Body,
    /// Weak Armor, Thermal Exchange, Spicy Spray, the attacker's Poison Touch)
    /// and freeze's thaw from Fire attacks.
    pub(super) fn damaging_hit(
        &mut self,
        user: MonRef,
        damaged: &[MonRef],
        dealt: &[u32],
        total_so_far: u32,
        data: &crate::dex::MoveData,
        move_type: crate::dex::TypeId,
    ) {
        #[derive(Clone, Copy, PartialEq)]
        enum H {
            Thaw,
            RoughSkin,
            RockyHelmet,
            Stamina,
            FlameBody,
            WeakArmor,
            ThermalExchange,
            SpicySpray,
            CursedBody,
            PoisonPoint,
            SeedSower,
            Mummy,
            Static,
            Gooey,
            Justified,
            Rattled,
            SandSpit,
            ToxicDebris,
            Electromorphosis,
            WanderingSpirit,
            InnardsOut,
            PoisonTouch,
            AirBalloon,
            EffectSpore,
        }
        let dex = Dex::get();
        let contact = self.contact(data);
        let fire = move_type == dex.type_id("Fire").expect("Fire");
        let status_move = data.category == crate::dex::Category::Status;
        // (holder, target, kind, order, speed, subOrder)
        let mut hs: Vec<(MonRef, MonRef, H, u64, i32, u8)> = Vec::new();
        const NONE: u64 = 4_294_967_296;
        for &t in damaged {
            let speed = self.mon(t).speed;
            if self.mon(t).status == crate::damage::Status::Freeze {
                hs.push((t, t, H::Thaw, NONE, speed, 0));
            }
            let ab = match self.ability_id(t) {
                "roughskin" => Some((H::RoughSkin, 1)),
                "stamina" => Some((H::Stamina, NONE)),
                "flamebody" => Some((H::FlameBody, NONE)),
                "weakarmor" => Some((H::WeakArmor, NONE)),
                "thermalexchange" => Some((H::ThermalExchange, NONE)),
                "spicyspray" => Some((H::SpicySpray, NONE)),
                "cursedbody" => Some((H::CursedBody, NONE)),
                "poisonpoint" => Some((H::PoisonPoint, NONE)),
                "seedsower" => Some((H::SeedSower, NONE)),
                "mummy" => Some((H::Mummy, NONE)),
                "static" => Some((H::Static, NONE)),
                "gooey" => Some((H::Gooey, NONE)),
                "justified" => Some((H::Justified, NONE)),
                "rattled" => Some((H::Rattled, NONE)),
                "sandspit" => Some((H::SandSpit, NONE)),
                "toxicdebris" => Some((H::ToxicDebris, NONE)),
                "electromorphosis" => Some((H::Electromorphosis, 1)),
                "wanderingspirit" => Some((H::WanderingSpirit, NONE)),
                "innardsout" => Some((H::InnardsOut, 1)),
                "effectspore" => Some((H::EffectSpore, NONE)),
                _ => None,
            };
            if let Some((k, order)) = ab {
                hs.push((t, t, k, order, speed, 7));
            }
            match self.item_of(t) {
                Some("rockyhelmet") => hs.push((t, t, H::RockyHelmet, 2, speed, 8)),
                Some("airballoon") => hs.push((t, t, H::AirBalloon, NONE, speed, 8)),
                _ => {}
            }
            if self.ability_is(user, "poisontouch")
                && !self.ability_is(t, "shielddust")
                && self.item_of(t) != Some("covertcloak")
            {
                hs.push((user, t, H::PoisonTouch, NONE, self.mon(user).speed, 6));
            }
        }
        self.speed_sort(&mut hs, |a, b| {
            a.3.cmp(&b.3).then(b.4.cmp(&a.4)).then(a.5.cmp(&b.5))
        });
        for (_, t, h, _, _, _) in hs {
            match h {
                H::Thaw => {
                    if fire && !status_move && self.mon(t).status == crate::damage::Status::Freeze {
                        self.cure_status(t);
                    }
                }
                H::RoughSkin if contact => {
                    let amount = (self.mon(user).max_hp() / 8) as u32;
                    self.effect_damage(user, amount);
                }
                H::RockyHelmet if contact => {
                    let amount = (self.mon(user).max_hp() / 6) as u32;
                    self.effect_damage(user, amount);
                }
                H::Stamina => {
                    self.boost(t, &[(1, 1)], Some(user));
                }
                H::FlameBody if contact && self.chance.chance(3, 10) => {
                    self.try_set_status_from(user, crate::damage::Status::Burn, Some(t));
                }
                H::WeakArmor if self.physical(data) => {
                    self.boost(t, &[(1, -1), (4, 2)], Some(t));
                }
                H::ThermalExchange if fire => {
                    self.boost(t, &[(0, 1)], Some(user));
                }
                H::SpicySpray => {
                    self.try_set_status_from(user, crate::damage::Status::Burn, Some(t));
                }
                H::CursedBody => {
                    if !self.mon(user).volatiles.has(VolatileId::Disable)
                        && data.id != "struggle"
                        && self.chance.chance(3, 10)
                    {
                        // disable's onStart: a turn shorter on the Pokemon
                        // that is moving now.
                        if self.add_volatile(user, VolatileId::Disable).truthy() {
                            if let Some(v) =
                                self.mon_mut(user).volatiles.get_mut(VolatileId::Disable)
                            {
                                v.duration = Some(4);
                            }
                        }
                    }
                }
                H::Static if contact && self.chance.chance(3, 10) => {
                    self.try_set_status_from(user, crate::damage::Status::Paralysis, Some(t));
                }
                H::Gooey if contact => {
                    self.boost(user, &[(4, -1)], Some(t));
                }
                H::Justified if move_type == dex.type_id("Dark").expect("Dark") => {
                    self.boost(t, &[(0, 1)], Some(user));
                }
                H::Rattled
                    if ["Dark", "Bug", "Ghost"]
                        .iter()
                        .any(|n| move_type == dex.type_id(n).expect("type")) =>
                {
                    self.boost(t, &[(4, 1)], Some(user));
                }
                H::ToxicDebris if self.physical(data) => {
                    // The attacker's side (its foes', if it hit an ally).
                    let side = if user.side == t.side {
                        1 - user.side
                    } else {
                        user.side
                    };
                    self.add_hazard(side, super::state::SideCondition::ToxicSpikes);
                }
                H::WanderingSpirit if contact => {
                    self.skill_swap(user, t);
                }
                // Innards Out: fainting, it hits back for what it lost.
                H::InnardsOut if self.mon(t).hp == 0 => {
                    let i = damaged.iter().position(|&d| d == t).unwrap_or(0);
                    let amount = dealt.get(i).copied().unwrap_or(0) + total_so_far;
                    self.effect_damage(user, amount);
                }
                H::Electromorphosis => {
                    self.add_volatile(t, VolatileId::Charge);
                }
                H::SandSpit => {
                    self.set_weather(crate::damage::Weather::Sand, t);
                }
                H::SeedSower => {
                    self.set_terrain(Terrain::Grassy, t);
                }
                H::Mummy if contact => {
                    let dex = Dex::get();
                    let ab = dex.ability(self.mon(user).ability);
                    if ab.id != "mummy" && !ab.flags.iter().any(|f| f == "cantsuppress") {
                        self.set_ability(user, self.mon(t).ability);
                    }
                }
                H::PoisonPoint if contact && self.chance.chance(3, 10) => {
                    self.try_set_status_from(user, crate::damage::Status::Poison, Some(t));
                }
                H::PoisonTouch if contact && self.chance.chance(3, 10) => {
                    self.try_set_status_from(t, crate::damage::Status::Poison, Some(user));
                }
                H::EffectSpore if contact && self.run_status_immunity(user, "powder") => {
                    let r = self.chance.random(100);
                    let status = match r {
                        0..=10 => Some(crate::damage::Status::Sleep),
                        11..=20 => Some(crate::damage::Status::Paralysis),
                        21..=29 => Some(crate::damage::Status::Poison),
                        _ => None,
                    };
                    if let Some(s) = status {
                        self.try_set_status_from(user, s, Some(t));
                    }
                }
                H::AirBalloon => {
                    // The balloon pops: the item is simply gone.
                    self.mon_mut(t).item = None;
                    self.after_use_item(t);
                }
                _ => {}
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
    pub(super) fn take_item(&mut self, holder: MonRef, taker: MonRef) -> Option<ItemId> {
        // No isActive check: a Pokemon the move just fainted still loses it.
        let item = self.mon(holder).item?;
        // The TakeItem event: Unburden's handler (subOrder 7) runs before the
        // item's own refusal (8).
        if self.ability_is(holder, "unburden") {
            self.add_volatile(holder, VolatileId::Unburden);
        }
        if !self.can_take(item, holder) {
            return None;
        }
        // Sticky Hold: nobody else takes it while it has HP (Knock Off passes
        // its user as the taker).
        if self.ability_is(holder, "stickyhold") && self.mon(holder).hp > 0 && taker != holder {
            return None;
        }
        self.mon_mut(holder).item = None;
        Some(item)
    }

    /// An item's onTakeItem: some can't be removed, and a Mega Stone stays
    /// with a Pokemon of the species it evolves.
    fn can_take(&self, item: ItemId, checker: MonRef) -> bool {
        let dex = Dex::get();
        let data = dex.item(item);
        !data.take_forbidden && !dex.mega_stone_stays(item, self.mon(checker).species)
    }

    /// Trick's onHit: swap items. False when it fails.
    pub(super) fn trick(&mut self, user: MonRef, target: MonRef) -> bool {
        // takeItem on each: None = no item, Some(None) = blocked (false).
        let take = |b: &mut Battle, r: MonRef| -> Option<Option<ItemId>> {
            let item = b.mon(r).item?;
            if b.ability_is(r, "unburden") {
                b.add_volatile(r, VolatileId::Unburden);
            }
            if !b.can_take(item, r) {
                return Some(None);
            }
            // Sticky Hold keeps the target's item from Trick.
            if r == target && b.ability_is(r, "stickyhold") && b.mon(r).hp > 0 {
                return Some(None);
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
        if mine.is_some_and(|i| !self.can_take(i, target))
            || yours.is_some_and(|i| !self.can_take(i, user))
        {
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
