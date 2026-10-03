//! Weather, terrain and switch-in abilities: field.setWeather/setTerrain,
//! the weather and terrain conditions (data/conditions.ts, the terrains in
//! data/moves.ts), runSwitch's SwitchIn event with Intimidate and the
//! weather and terrain setters.

use super::state::{SideCondition, ACTIVE_PER_SIDE};
use super::{Battle, MonRef, Res};
use crate::damage::{Terrain, Weather};
use crate::dex::Dex;

/// What a switch-in ability does on start.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum StartEffect {
    Intimidate,
    Weather(Weather),
    Terrain(Terrain),
    /// Heals the ally a quarter (onSwitchInPriority -2).
    Hospitality,
    /// Copies a foe's ability.
    Trace,
    /// Clears Reflect, Light Screen and Aurora Veil from both sides.
    ScreenCleaner,
    /// Counts the side's fallen (up to 5).
    SupremeOverlord,
    /// Lowers the foes' evasion, once per battle.
    SupersweetSyrup,
}

impl StartEffect {
    /// onSwitchInPriority.
    fn priority(self) -> i8 {
        match self {
            StartEffect::Hospitality => -2,
            _ => 0,
        }
    }
}

/// The abilities whose onStart the engine implements.
pub(super) fn start_effect(ability: &str) -> Option<StartEffect> {
    Some(match ability {
        "intimidate" => StartEffect::Intimidate,
        "hospitality" => StartEffect::Hospitality,
        "trace" => StartEffect::Trace,
        "screencleaner" => StartEffect::ScreenCleaner,
        "supremeoverlord" => StartEffect::SupremeOverlord,
        "supersweetsyrup" => StartEffect::SupersweetSyrup,
        "drought" => StartEffect::Weather(Weather::Sun),
        "drizzle" => StartEffect::Weather(Weather::Rain),
        "sandstream" => StartEffect::Weather(Weather::Sand),
        "snowwarning" => StartEffect::Weather(Weather::Snow),
        "electricsurge" => StartEffect::Terrain(Terrain::Electric),
        "grassysurge" => StartEffect::Terrain(Terrain::Grassy),
        "psychicsurge" => StartEffect::Terrain(Terrain::Psychic),
        _ => return None,
    })
}

/// A SwitchIn handler: its holder and the speed key it sorts by.
#[derive(Debug, Clone, Copy)]
struct SwitchIn {
    mon: MonRef,
    what: SwitchInKind,
    /// `pokemon.speed - speedOrder index / 4`, times 4 to stay integral.
    speed: i64,
    priority: i8,
    sub_order: u8,
    /// effectOrder, the tiebreak for SwitchIn handlers (hazards).
    effect_order: u64,
}

#[derive(Debug, Clone, Copy)]
enum SwitchInKind {
    /// tox's onSwitchIn.
    ToxicReset,
    Ability(StartEffect),
    /// A terrain seed's onStart (onSwitchInPriority -1).
    Seed,
    /// White Herb's onAnySwitchIn (priority -2), for every holder.
    WhiteHerb,
    /// An entry hazard on the Pokemon's side.
    Hazard(SideCondition),
}

impl Battle {
    /// `isGrounded()` (no supported item or effect lifts a Pokemon).
    pub(super) fn grounded(&self, r: MonRef) -> bool {
        let m = self.mon(r);
        let dex = Dex::get();
        let item = self.item_of(r);
        if item == Some("ironball") {
            return true;
        }
        let ability = self.ability_id(r);
        !m.has_type(dex.type_id("Flying").expect("Flying")) && ability != "levitate" && ability != "eelevate" && item != Some("airballoon")
    }

    /// `field.setWeather` from an ability (5 turns: no rock items supported).
    pub(super) fn set_weather(&mut self, w: Weather, source: MonRef) -> bool {
        if self.field.weather == w {
            return false;
        }
        // durationCallback: the matching rock makes it 8 turns.
        let rock = match w {
            Weather::Sun => "heatrock",
            Weather::Rain => "damprock",
            Weather::Sand => "smoothrock",
            Weather::Snow => "icyrock",
            Weather::None => "",
        };
        self.field.weather = w;
        self.field.weather_turns = if self.item_of(source) == Some(rock) { 8 } else { 5 };
        true
    }

    /// `field.setTerrain` (8 turns with Terrain Extender).
    pub(super) fn set_terrain(&mut self, t: Terrain, source: MonRef) -> bool {
        if self.field.terrain == t {
            return false;
        }
        self.field.terrain = t;
        self.field.terrain_turns = if self.item_of(source) == Some("terrainextender") { 8 } else { 5 };
        self.terrain_change();
        true
    }

    /// An ability's onStart.
    pub(super) fn ability_start(&mut self, r: MonRef, effect: StartEffect) {
        match effect {
            StartEffect::Intimidate => {
                // adjacentFoes(): in doubles every foe with HP.
                let foe = 1 - r.side;
                let targets: Vec<MonRef> = (0..ACTIVE_PER_SIDE)
                    .filter_map(|p| self.occupant(foe, p))
                    .filter(|&t| self.mon(t).hp > 0 && !self.mon(t).fainted)
                    .collect();
                for t in targets {
                    self.boost_by(t, &[(0, -1)], Some(r), super::conditions::BoostCause::Intimidate);
                }
            }
            StartEffect::Weather(w) => {
                self.set_weather(w, r);
            }
            StartEffect::Hospitality => {
                let side = r.side;
                let allies: Vec<MonRef> = (0..ACTIVE_PER_SIDE)
                    .filter_map(|p| self.occupant(side, p))
                    .filter(|&a| a != r && self.mon(a).hp > 0 && !self.mon(a).fainted)
                    .collect();
                for a in allies {
                    let amount = (self.mon(a).max_hp() / 4) as u32;
                    self.heal(a, amount);
                }
            }
            StartEffect::SupersweetSyrup => {
                if self.mon(r).syrup_triggered {
                    return;
                }
                self.mon_mut(r).syrup_triggered = true;
                for t in self.adjacent_foes(r) {
                    self.boost(t, &[(6, -1)], Some(r));
                }
            }
            StartEffect::SupremeOverlord => {
                let fallen = self.sides[r.side].total_fainted.min(5);
                self.mon_mut(r).fallen = fallen;
            }
            StartEffect::ScreenCleaner => {
                for side in [r.side, 1 - r.side] {
                    for c in [SideCondition::Reflect, SideCondition::LightScreen, SideCondition::AuroraVeil] {
                        self.sides[side].conditions[c as usize] = 0;
                    }
                }
            }
            StartEffect::Trace => {
                // seek unless a foe has no ability; then an Update right away.
                let foes = self.adjacent_foes(r);
                let seek = !foes.iter().any(|&f| self.ability_is(f, "noability"));
                self.mon_mut(r).trace_seek = seek;
                if seek {
                    self.trace_update(r);
                }
            }
            StartEffect::Terrain(t) => {
                self.set_terrain(t, r);
            }
        }
    }

    /// `runSwitch`'s fieldEvent('SwitchIn') over the Pokemon that just came
    /// in: abilities' onStart and Toxic's counter reset, in Speed order (ties
    /// broken by the speed order of every active, as Showdown's speedOrder).
    pub(super) fn switch_in_event(&mut self, switchers: &[MonRef]) -> Res<()> {
        let mut all: Vec<MonRef> = Vec::new();
        for side in 0..2 {
            for pos in 0..ACTIVE_PER_SIDE {
                if let Some(r) = self.occupant(side, pos) {
                    all.push(r);
                }
            }
        }
        let mut keyed: Vec<(MonRef, i32)> = all.iter().map(|&r| (r, self.mon(r).speed)).collect();
        self.speed_sort(&mut keyed, |a, b| b.1.cmp(&a.1));
        let order: Vec<MonRef> = keyed.into_iter().map(|(r, _)| r).collect();

        let mut handlers = Vec::new();
        for &r in switchers {
            let m = self.mon(r);
            let index = order.iter().position(|&o| o == r).unwrap_or(0) as i64;
            let speed = m.speed as i64 * 4 - index;
            if m.status == crate::damage::Status::Toxic {
                handlers.push(SwitchIn { mon: r, what: SwitchInKind::ToxicReset, speed, priority: 0, sub_order: 0, effect_order: 0 });
            }
            for c in SideCondition::ALL {
                if c.is_hazard() && self.sides[r.side].condition(c) > 0 {
                    let effect_order = self.sides[r.side].condition_order[c as usize];
                    handlers.push(SwitchIn { mon: r, what: SwitchInKind::Hazard(c), speed, priority: 0, sub_order: 4, effect_order });
                }
            }
            if let Some(e) = start_effect(&Dex::get().ability(m.ability).id) {
                handlers.push(SwitchIn { mon: r, what: SwitchInKind::Ability(e), speed, priority: e.priority(), sub_order: 7, effect_order: 0 });
            }
            if self.item_of(r).is_some_and(|i| i.ends_with("seed")) {
                handlers.push(SwitchIn { mon: r, what: SwitchInKind::Seed, speed, priority: -1, sub_order: 8, effect_order: 0 });
            }
        }
        for &r in &all {
            if self.item_of(r) == Some("whiteherb") {
                let index = order.iter().position(|&o| o == r).unwrap_or(0) as i64;
                let speed = self.mon(r).speed as i64 * 4 - index;
                handlers.push(SwitchIn { mon: r, what: SwitchInKind::WhiteHerb, speed, priority: -2, sub_order: 8, effect_order: 0 });
            }
        }
        self.speed_sort(&mut handlers, |a, b| {
            b.priority.cmp(&a.priority).then(b.speed.cmp(&a.speed)).then(a.sub_order.cmp(&b.sub_order)).then(a.effect_order.cmp(&b.effect_order))
        });
        for h in handlers {
            if self.mon(h.mon).fainted {
                continue;
            }
            match h.what {
                SwitchInKind::ToxicReset => self.mon_mut(h.mon).status_state.stage = 0,
                SwitchInKind::Seed => self.try_seed(h.mon),
                SwitchInKind::WhiteHerb => self.white_herb(h.mon),
                SwitchInKind::Hazard(c) => {
                    if self.mon(h.mon).hp > 0 && self.sides[h.mon.side].condition(c) > 0 {
                        self.hazard_switch_in(h.mon, c);
                    }
                }
                SwitchInKind::Ability(e) => {
                    if self.mon(h.mon).hp > 0 {
                        self.ability_start(h.mon, e);
                    }
                }
            }
            self.faint_messages()?;
            if self.is_over() {
                return Ok(());
            }
        }
        Ok(())
    }

    /// A hazard's onSwitchIn.
    fn hazard_switch_in(&mut self, r: MonRef, c: SideCondition) {
        let dex = Dex::get();
        let max = self.mon(r).max_hp() as u32;
        let layers = self.sides[r.side].condition(c) as u32;
        let foe_lead = self.occupant(1 - r.side, 0);
        match c {
            SideCondition::StealthRock => {
                let rock = dex.type_id("Rock").expect("Rock");
                let t = self.mon(r).types;
                let mut type_mod = dex.type_mod(rock, t[0]) as i32;
                if t[1] != t[0] {
                    type_mod += dex.type_mod(rock, t[1]) as i32;
                }
                let amount = match type_mod.clamp(-6, 6) {
                    m if m >= 0 => max * (1 << m) / 8,
                    m => max / (8 << -m),
                };
                self.effect_damage(r, amount);
            }
            SideCondition::Spikes if self.grounded(r) => {
                let amount = [0, 3, 4, 6][layers as usize] * max / 24;
                self.effect_damage(r, amount);
            }
            SideCondition::ToxicSpikes if self.grounded(r) => {
                let m = self.mon(r);
                if m.has_type(dex.type_id("Poison").expect("Poison")) {
                    self.sides[r.side].conditions[c as usize] = 0;
                } else if !m.has_type(dex.type_id("Steel").expect("Steel")) {
                    let status = if layers >= 2 { crate::damage::Status::Toxic } else { crate::damage::Status::Poison };
                    self.try_set_status(r, status);
                }
            }
            SideCondition::StickyWeb if self.grounded(r) => {
                self.boost(r, &[(4, -1)], foe_lead);
            }
            _ => {}
        }
    }

    /// addSideCondition for a hazard: a new one, or another layer. False if
    /// it can't stack higher.
    pub(super) fn add_hazard(&mut self, side: usize, c: SideCondition) -> bool {
        let cur = self.sides[side].condition(c);
        if cur >= c.max_layers() {
            return false;
        }
        if cur == 0 {
            self.effect_order += 1;
            self.sides[side].condition_order[c as usize] = self.effect_order;
        }
        self.sides[side].conditions[c as usize] = cur + 1;
        true
    }

    /// The weather's Residual handler: count down, or deal sandstorm damage
    /// (eachEvent('Weather') in Speed order) and run Update.
    /// Returns true when the weather ended.
    pub(super) fn weather_residual(&mut self) -> bool {
        self.field.weather_turns = self.field.weather_turns.saturating_sub(1);
        if self.field.weather_turns == 0 {
            self.field.weather = Weather::None;
            return true;
        }
        let actives = self.all_active();
        let mut keyed: Vec<(MonRef, i32)> = actives.iter().map(|&r| (r, self.mon(r).speed)).collect();
        self.speed_sort(&mut keyed, |a, b| b.1.cmp(&a.1));
        // eachEvent('Weather'): per Pokemon, sandstorm's onWeather (subOrder
        // 5), then the ability's (7).
        let weather = self.field.weather;
        for (r, _) in keyed {
            if self.mon(r).hp == 0 {
                continue;
            }
            if weather == Weather::Sand && self.run_status_immunity(r, "sandstorm") {
                let amount = (self.mon(r).max_hp() / 16) as u32;
                self.effect_damage(r, amount);
            }
            if self.mon(r).hp == 0 {
                continue;
            }
            let max = self.mon(r).max_hp() as u32;
            match (Dex::get().ability(self.mon(r).ability).id.as_str(), weather) {
                ("solarpower" | "dryskin", Weather::Sun) => {
                    self.effect_damage(r, max / 8);
                }
                ("dryskin", Weather::Rain) => {
                    self.heal(r, max / 8);
                }
                ("raindish", Weather::Rain) => {
                    self.heal(r, max / 16);
                }
                ("icebody", Weather::Snow) => {
                    self.heal(r, max / 16);
                }
                _ => {}
            }
        }
        self.each_update();
        false
    }

    /// The terrain's duration countdown.
    /// Returns true when the terrain ended.
    pub(super) fn terrain_residual(&mut self) -> bool {
        self.field.terrain_turns = self.field.terrain_turns.saturating_sub(1);
        if self.field.terrain_turns == 0 {
            self.clear_terrain();
            return true;
        }
        false
    }

    /// Grassy Terrain's per-Pokemon residual heal.
    pub(super) fn grassy_heal(&mut self, r: MonRef) {
        if self.grounded(r) && super::moves::semi_invulnerable(self.mon(r)).is_none() {
            let amount = (self.mon(r).max_hp() / 16).max(1) as u32;
            self.heal(r, amount);
        }
    }

    /// The SetStatus event: Electric Terrain stops sleep and Misty Terrain
    /// every status on grounded Pokemon.
    pub(super) fn terrain_blocks_status(&self, r: MonRef, status: crate::damage::Status) -> bool {
        match self.field.terrain {
            Terrain::Electric => status == crate::damage::Status::Sleep && self.grounded(r),
            Terrain::Misty => self.grounded(r),
            _ => false,
        }
    }

    /// Psychic Terrain's onTryHit: priority moves fail against grounded foes.
    pub(super) fn psychic_terrain_blocks(&self, user: MonRef, target: MonRef, priority: i8, self_target: bool) -> bool {
        self.field.terrain == Terrain::Psychic && priority > 0 && !self_target && target.side != user.side && self.grounded(target)
    }

    /// setAbility: the old ability's End (Unburden and Flash Fire drop their
    /// volatiles), then the new one.
    pub(super) fn set_ability(&mut self, r: MonRef, ability: crate::dex::AbilityId) {
        let m = self.mon_mut(r);
        match Dex::get().ability(m.ability).id.as_str() {
            "unburden" => {
                m.volatiles.remove(super::state::VolatileId::Unburden);
            }
            "flashfire" => {
                m.volatiles.remove(super::state::VolatileId::FlashFire);
            }
            _ => {}
        }
        m.ability = ability;
        // A fresh abilityState.
        m.protean_used = false;
        m.fallen = 0;
    }

    /// `adjacentFoes()`: in doubles, every foe with HP.
    pub(super) fn adjacent_foes(&self, r: MonRef) -> Vec<MonRef> {
        let foe = 1 - r.side;
        (0..ACTIVE_PER_SIDE).filter_map(|p| self.occupant(foe, p)).filter(|&t| self.mon(t).hp > 0 && !self.mon(t).fainted).collect()
    }

    /// Trace's onUpdate: copy a random foe's (traceable) ability, which then
    /// starts.
    pub(super) fn trace_update(&mut self, r: MonRef) {
        if !self.mon(r).trace_seek || !self.ability_is(r, "trace") {
            return;
        }
        let dex = Dex::get();
        let options: Vec<MonRef> = self
            .adjacent_foes(r)
            .into_iter()
            .filter(|&f| {
                let ab = dex.ability(self.mon(f).ability);
                ab.id != "noability" && !ab.flags.iter().any(|x| x == "notrace")
            })
            .collect();
        if options.is_empty() {
            return;
        }
        let pick = options[self.chance.sample(options.len())];
        let ability = self.mon(pick).ability;
        self.set_ability(r, ability);
        self.mon_mut(r).trace_seek = false;
        if let Some(e) = start_effect(&dex.ability(ability).id) {
            self.ability_start(r, e);
        }
    }

    /// `field.clearTerrain`, then eachEvent('TerrainChange').
    pub(super) fn clear_terrain(&mut self) {
        if self.field.terrain == Terrain::None {
            return;
        }
        self.field.terrain = Terrain::None;
        self.field.terrain_turns = 0;
        self.terrain_change();
    }
}
