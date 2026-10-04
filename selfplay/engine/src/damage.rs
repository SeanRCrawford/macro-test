//! Damage calculation, mirroring Showdown's `getDamage` and the champions
//! mod's `modifyDamage` step for step.
//!
//! Showdown applies most modifiers through events: every active effect with a
//! matching handler (abilities, items, volatiles, weather, terrain, screens,
//! the move itself) contributes, in an order set by each handler's
//! priority, its holder's speed and the effect's kind. Modifiers are combined
//! with rounding at every step, so the order changes results at the margins.
//! This module collects handlers the same way (`collect`) and applies them in
//! the same order (`fold`).
//!
//! Every handler an effect has (per the dex's handler names) must be
//! implemented here or the calculation returns `Unsupported`, so an effect the
//! engine doesn't know never silently does nothing.

use crate::dex::{
    AbilityId, Category, Dex, Handlers, IgnoreImmunity, ItemId, MoveData, MoveId, MoveTarget,
    SpeciesId, TypeId, ATK, DEF, SPA, SPD,
};
use crate::fixed::{chain, modify, of, ONE};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Weather {
    #[default]
    None,
    Sun,
    Rain,
    Sand,
    Snow,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Terrain {
    #[default]
    None,
    Electric,
    Grassy,
    Misty,
    Psychic,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Status {
    #[default]
    None,
    Burn,
    Paralysis,
    Poison,
    Toxic,
    Sleep,
    Freeze,
}

/// Volatile conditions that affect damage.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Volatiles {
    /// Times Helping Hand was used on this Pokemon this turn (each is x1.5).
    pub helping_hand: u8,
    pub charge: bool,
    pub flash_fire: bool,
    /// Used Glaive Rush last turn: takes double damage.
    pub glaive_rush: bool,
    /// A Gem was consumed for this move.
    pub gem: bool,
    /// Charging a semi-invulnerable move (Dig, Dive, Fly, Bounce...).
    pub semi_invulnerable: Option<MoveId>,
}

/// A Pokemon as the damage calculation sees it.
#[derive(Debug, Clone)]
pub struct Combatant {
    pub species: SpeciesId,
    /// Current types; a mono-type Pokemon repeats its type.
    pub types: [TypeId; 2],
    /// Stored stats; `stats[HP]` is max HP.
    pub stats: [u16; 6],
    pub hp: u16,
    /// Stat stages, indexed like stats (index 0 unused).
    pub boosts: [i8; 6],
    pub ability: AbilityId,
    pub item: Option<ItemId>,
    pub status: Status,
    /// Showdown's `pokemon.speed` (action speed): orders handlers of different Pokemon.
    pub speed: i32,
    pub volatiles: Volatiles,
    /// Turns on the field; 0 on the turn it switched in (Stakeout).
    pub active_turns: u16,
    /// Times hit by an attack this battle (Rage Fist).
    pub times_attacked: u8,
    /// Supreme Overlord's count of fainted allies, capped at 5.
    pub fallen: u8,
    /// `moveLastTurnResult === false` (Stomping Tantrum).
    pub move_last_turn_failed: bool,
    /// Already moved this turn and not newly switched in (Payback doubles).
    pub moved_this_turn: bool,
    /// `statsLoweredThisTurn` (Lash Out).
    pub stats_lowered_this_turn: bool,
    /// `getStat('spe')`: boosted and modified Speed (Gyro Ball).
    pub spe_stat: u32,
}

impl Combatant {
    /// A full-HP Pokemon with no boosts, status or volatiles.
    pub fn new(
        species: SpeciesId,
        stats: [u16; 6],
        ability: AbilityId,
        item: Option<ItemId>,
    ) -> Self {
        let dex = Dex::get();
        Combatant {
            species,
            types: dex.species(species).types,
            stats,
            hp: stats[0],
            boosts: [0; 6],
            ability,
            item,
            status: Status::None,
            speed: stats[5] as i32,
            volatiles: Volatiles::default(),
            active_turns: 1,
            times_attacked: 0,
            fallen: 0,
            move_last_turn_failed: false,
            moved_this_turn: false,
            stats_lowered_this_turn: false,
            spe_stat: stats[5] as u32,
        }
    }

    pub fn max_hp(&self) -> u16 {
        self.stats[0]
    }

    pub fn has_type(&self, t: TypeId) -> bool {
        self.types.contains(&t)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct SideState {
    pub reflect: bool,
    pub light_screen: bool,
    pub aurora_veil: bool,
    /// Pokemon on this side that have fainted this battle (Last Respects).
    pub fainted: u8,
}

/// One hit of one move: who is on the field, and what is being calculated.
#[derive(Debug, Clone)]
pub struct DamageCtx<'a> {
    /// Active Pokemon by position: side * 2 + slot. `None` is an empty slot.
    pub actives: [Option<&'a Combatant>; 4],
    pub attacker: usize,
    pub defender: usize,
    pub weather: Weather,
    pub terrain: Terrain,
    pub sides: [SideState; 2],
    pub crit: bool,
    /// The move hit more than one target this turn (x0.75).
    pub spread: bool,
    /// Which hit of a multi-hit move, from 1.
    pub hit: u8,
    /// The move got through the defender's protection (Unseen Fist): the
    /// champions mod quarters the damage.
    pub bypass_protect: bool,
    /// The damage goes to a substitute (resist berries stay out of it).
    pub hit_sub: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    /// The defender is immune (`getDamage` returns false).
    Immune,
    /// The move deals no damage (base power 0: `getDamage` returns undefined).
    NoDamage,
    /// Damage for each random roll; index r is the 100 - r % roll, so index 0
    /// is the highest roll and index 15 the lowest (85%).
    Damage([u32; 16]),
}

/// An effect whose damage-relevant behaviour isn't implemented yet.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Unsupported(pub String);

type Res<T> = Result<T, Unsupported>;

fn unsupported<T>(what: impl Into<String>) -> Res<T> {
    Err(Unsupported(what.into()))
}

/// The move as modified for this use (Showdown's ActiveMove).
#[derive(Debug, Clone)]
pub struct ActiveMove {
    pub id: MoveId,
    pub move_type: TypeId,
    pub base_power: u32,
    pub category: Category,
    pub target: MoveTarget,
    pub ignore_ability: bool,
    pub ignore_immunity: IgnoreImmunity,
    pub infiltrates: bool,
    pub has_sheer_force: bool,
    /// The -ate ability that changed this move's type (and boosts it).
    pub type_changer: Option<AbilityId>,
    /// Parental Bond made this a two-hit move (the second hit does 1/4).
    pub parental_bond: bool,
}

impl ActiveMove {
    fn new(id: MoveId, m: &MoveData) -> Self {
        ActiveMove {
            id,
            move_type: m.move_type,
            base_power: m.base_power as u32,
            category: m.category,
            target: m.target,
            ignore_ability: m.ignore_ability,
            ignore_immunity: m.ignore_immunity,
            infiltrates: false,
            has_sheer_force: false,
            type_changer: None,
            parental_bond: false,
        }
    }
}

/// Which effect a handler belongs to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Effect {
    Ability(AbilityId),
    Item(ItemId),
    Volatile(VolatileKind),
    /// A move's own handler (`onBasePower` on Knock Off...).
    MoveSelf(MoveId),
    Weather(Weather),
    Terrain(Terrain),
    Screen(Screen),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum VolatileKind {
    HelpingHand,
    Charge,
    FlashFire,
    GlaiveRush,
    Gem,
    /// The charging move's own condition (Dig, Dive, Fly, Bounce).
    SemiInvulnerable(MoveId),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Screen {
    Reflect,
    Light,
    AuroraVeil,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Holder {
    Mon(usize),
    Side(usize),
    Field,
}

/// One handler found for an event, with Showdown's sort keys.
#[derive(Debug, Clone)]
struct Ref {
    effect: Effect,
    holder: Holder,
    hook: String,
    order: i64,
    priority: i32,
    speed: i32,
    sub_order: i32,
}

/// What a handler did.
enum Act {
    None,
    /// `return this.chainModify(x)`.
    Chain(u32),
    /// `return this.modify(value, x)`: changes the value immediately.
    Modify(u32),
    /// Technician: boost if the power so far is at most 60.
    Technician,
    /// Fairy Aura: only the first holder's handler applies.
    FairyAura,
}

/// Which kind of effect is asking for the weather (Mega Sol only changes the
/// answer for moves, weather and itself).
#[derive(Clone, Copy, PartialEq, Eq)]
enum Asker {
    Move,
    Weather,
    Ability(&'static str),
}

const NO_ORDER: i64 = 4_294_967_296;

/// Moves whose type the -ate abilities leave alone.
const NO_MODIFY_TYPE: [&str; 7] = [
    "judgment",
    "multiattack",
    "naturalgift",
    "revelationdance",
    "technoblast",
    "terrainpulse",
    "weatherball",
];

fn type_boost_item(id: &str) -> Option<&'static str> {
    Some(match id {
        "blackbelt" => "Fighting",
        "blackglasses" => "Dark",
        "charcoal" => "Fire",
        "dragonfang" => "Dragon",
        "fairyfeather" => "Fairy",
        "hardstone" => "Rock",
        "magnet" => "Electric",
        "metalcoat" => "Steel",
        "miracleseed" => "Grass",
        "mysticwater" => "Water",
        "nevermeltice" => "Ice",
        "poisonbarb" => "Poison",
        "sharpbeak" => "Flying",
        "silkscarf" => "Normal",
        "silverpowder" => "Bug",
        "softsand" => "Ground",
        "spelltag" => "Ghost",
        "twistedspoon" => "Psychic",
        _ => return None,
    })
}

fn resist_berry(id: &str) -> Option<&'static str> {
    Some(match id {
        "occaberry" => "Fire",
        "passhoberry" => "Water",
        "wacanberry" => "Electric",
        "rindoberry" => "Grass",
        "yacheberry" => "Ice",
        "chopleberry" => "Fighting",
        "kebiaberry" => "Poison",
        "shucaberry" => "Ground",
        "cobaberry" => "Flying",
        "payapaberry" => "Psychic",
        "tangaberry" => "Bug",
        "chartiberry" => "Rock",
        "kasibberry" => "Ghost",
        "habanberry" => "Dragon",
        "colburberry" => "Dark",
        "babiriberry" => "Steel",
        "roseliberry" => "Fairy",
        _ => return None,
    })
}

/// Moves whose own damage handlers (basePowerCallback, onBasePower,
/// onModifyType, onModifyMove, onEffectiveness) this module implements.
pub const MOVES_WITH_HANDLERS: &[&str] = &[
    "acrobatics",
    "aurawheel",
    "beatup",
    "gyroball",
    "lashout",
    "payback",
    "barbbarrage",
    "blizzard",
    "eruption",
    "expandingforce",
    "facade",
    "freezedry",
    "grassknot",
    "hardpress",
    "heatcrash",
    "heavyslam",
    "hex",
    "hurricane",
    "knockoff",
    "lastrespects",
    "lowkick",
    "powertrip",
    "ragefist",
    "ragingbull",
    "reversal",
    "risingvoltage",
    "solarbeam",
    "solarblade",
    "storedpower",
    "stompingtantrum",
    "struggle",
    "temperflare",
    "terrainpulse",
    "thunder",
    "tripleaxel",
    "venoshock",
    "waterspout",
    "watershuriken",
    "weatherball",
];

/// The `???` type (Struggle): no STAB, no immunities, neutral to everything.
pub const TYPELESS: TypeId = TypeId(u8::MAX);

/// Calculate damage for every roll.
pub fn calculate(ctx: &DamageCtx, move_id: MoveId) -> Res<Outcome> {
    let am = prepare_move(ctx, move_id)?;
    damage_for(ctx, &am)
}

/// The move as it will be used: useMoveInner's ModifyType and ModifyMove.
pub fn prepare_move(ctx: &DamageCtx, move_id: MoveId) -> Res<ActiveMove> {
    let calc = Calc {
        ctx,
        dex: Dex::get(),
    };
    let data = calc.dex.move_data(move_id);
    let mut am = ActiveMove::new(move_id, data);
    calc.modify_type_and_move(&mut am, data)?;
    Ok(am)
}

/// `getDamage` for a move already prepared by `prepare_move`.
pub fn damage_for(ctx: &DamageCtx, am: &ActiveMove) -> Res<Outcome> {
    let calc = Calc {
        ctx,
        dex: Dex::get(),
    };
    let mut am = am.clone();
    let data = calc.dex.move_data(am.id);
    calc.get_damage(&mut am, data)
}

/// Whether the defender eats its type-resist berry (onSourceModifyDamage)
/// when `am` hits it for damage.
pub fn eats_resist_berry(ctx: &DamageCtx, am: &ActiveMove) -> Res<bool> {
    let calc = Calc {
        ctx,
        dex: Dex::get(),
    };
    let d = ctx.defender;
    let Some(t) = calc.effective_item(d).and_then(resist_berry) else {
        return Ok(false);
    };
    if am.move_type != calc.ty(t) || !calc.can_eat(d) {
        return Ok(false);
    }
    // Fixed damage (damageCallback, Seismic Toss...) skips ModifyDamage.
    let data = calc.dex.move_data(am.id);
    if data.handlers.has("damageCallback") || data.fixed_damage.is_some() || data.ohko {
        return Ok(false);
    }
    Ok(calc.type_mod(am, data)? > 0)
}

/// `runImmunity(move)` for the defender in `ctx`.
pub fn run_immunity(ctx: &DamageCtx, am: &ActiveMove) -> bool {
    Calc {
        ctx,
        dex: Dex::get(),
    }
    .run_immunity(am)
}

struct Calc<'a, 'b> {
    ctx: &'b DamageCtx<'a>,
    dex: &'static Dex,
}

impl<'a, 'b> Calc<'a, 'b> {
    fn mon(&self, i: usize) -> &'a Combatant {
        self.ctx.actives[i].expect("damage ctx refers to an empty slot")
    }
    fn attacker(&self) -> &'a Combatant {
        self.mon(self.ctx.attacker)
    }
    fn defender(&self) -> &'a Combatant {
        self.mon(self.ctx.defender)
    }
    fn ty(&self, name: &str) -> TypeId {
        self.dex.type_id(name).expect("type name")
    }
    fn ability_id(&self, i: usize) -> &'static str {
        &self.dex.ability(self.mon(i).ability).id
    }
    fn item_id(&self, i: usize) -> Option<&'static str> {
        self.mon(i).item.map(|it| self.dex.item(it).id.as_str())
    }
    fn active_indices(&self) -> impl Iterator<Item = usize> + '_ {
        (0..4).filter(|&i| self.ctx.actives[i].is_some())
    }

    // --- Showdown's Pokemon helpers -------------------------------------

    /// `hasAbility`: nothing in the format suppresses abilities outright
    /// (no Neutralizing Gas or Gastro Acid), so this is a plain comparison.
    fn has_ability(&self, i: usize, id: &str) -> bool {
        self.ability_id(i) == id
    }

    /// `ignoringItem`: Klutz (Embargo and Magic Room aren't in the format).
    fn ignoring_item(&self, i: usize) -> bool {
        match self.mon(i).item {
            Some(it) => self.has_ability(i, "klutz") && !self.dex.item(it).ignore_klutz,
            None => true,
        }
    }

    fn effective_item(&self, i: usize) -> Option<&'static str> {
        if self.ignoring_item(i) {
            None
        } else {
            self.item_id(i)
        }
    }

    /// `suppressingAbility`: the attacker's move ignores (breakable) abilities
    /// of everyone else.
    fn suppressing_ability(&self, i: usize, am: &ActiveMove) -> bool {
        am.ignore_ability && i != self.ctx.attacker
    }

    fn suppressing_weather(&self) -> bool {
        self.active_indices()
            .any(|i| self.dex.ability(self.mon(i).ability).suppress_weather)
    }

    /// `field.effectiveWeather()`.
    fn field_weather(&self) -> Weather {
        if self.suppressing_weather() {
            Weather::None
        } else {
            self.ctx.weather
        }
    }

    /// `pokemon.effectiveWeather()`, asked by an effect of kind `asker`. With
    /// Mega Sol, the attacker's move, weather and Mega Sol itself see sun.
    fn weather_for(&self, asker: Asker) -> Weather {
        let mega_sol = self.has_ability(self.ctx.attacker, "megasol");
        if mega_sol
            && matches!(
                asker,
                Asker::Move | Asker::Weather | Asker::Ability("megasol")
            )
        {
            return Weather::Sun;
        }
        self.field_weather()
    }

    /// `isGrounded`. `None` means airborne by Levitate/Eelevate.
    fn grounded(&self, i: usize, am: &ActiveMove) -> Option<bool> {
        let m = self.mon(i);
        let item = self.effective_item(i);
        if item == Some("ironball") {
            return Some(true);
        }
        if m.has_type(self.ty("Flying")) {
            return Some(false);
        }
        if (self.has_ability(i, "levitate") || self.has_ability(i, "eelevate"))
            && !self.suppressing_ability(i, am)
        {
            return None;
        }
        Some(item != Some("airballoon"))
    }

    fn is_grounded(&self, i: usize, am: &ActiveMove) -> bool {
        self.grounded(i, am) == Some(true)
    }

    /// `getWeight`, in hectograms.
    fn weight(&self, i: usize, am: &ActiveMove) -> Res<u32> {
        let mut w = self.dex.species(self.mon(i).species).weight_hg;
        let ab = self.dex.ability(self.mon(i).ability);
        if ab.handlers.has("onModifyWeight") && !(ab.breakable && self.suppressing_ability(i, am)) {
            match ab.id.as_str() {
                "lightmetal" => w /= 2,
                "heavymetal" => w *= 2,
                other => return unsupported(format!("ability {other}.onModifyWeight")),
            }
        }
        if let Some(it) = self.effective_item(i) {
            if self
                .dex
                .item(self.mon(i).item.unwrap())
                .handlers
                .has("onModifyWeight")
            {
                return unsupported(format!("item {it}.onModifyWeight"));
            }
        }
        Ok(w.max(1))
    }

    // --- Event handler collection ----------------------------------------

    fn handlers(&self, effect: Effect) -> &'static Handlers {
        let d = self.dex;
        let move_cond = |id: &str| &d.move_data(d.move_id(id).expect("known move")).condition;
        match effect {
            Effect::Ability(a) => &d.ability(a).handlers,
            Effect::Item(it) => &d.item(it).handlers,
            Effect::MoveSelf(m) => &d.move_data(m).handlers,
            Effect::Volatile(v) => match v {
                VolatileKind::HelpingHand => move_cond("helpinghand"),
                VolatileKind::Charge => move_cond("charge"),
                VolatileKind::GlaiveRush => move_cond("glaiverush"),
                VolatileKind::FlashFire => &d.ability(d.ability_id("flashfire").unwrap()).condition,
                VolatileKind::Gem => &d.condition(d.condition_id("gem").unwrap()).handlers,
                VolatileKind::SemiInvulnerable(m) => &d.move_data(m).condition,
            },
            Effect::Weather(w) => {
                let id = match w {
                    Weather::Sun => "sunnyday",
                    Weather::Rain => "raindance",
                    Weather::Sand => "sandstorm",
                    Weather::Snow => "snowscape",
                    Weather::None => unreachable!(),
                };
                &d.condition(d.condition_id(id).unwrap()).handlers
            }
            Effect::Terrain(t) => move_cond(match t {
                Terrain::Electric => "electricterrain",
                Terrain::Grassy => "grassyterrain",
                Terrain::Misty => "mistyterrain",
                Terrain::Psychic => "psychicterrain",
                Terrain::None => unreachable!(),
            }),
            Effect::Screen(s) => move_cond(match s {
                Screen::Reflect => "reflect",
                Screen::Light => "lightscreen",
                Screen::AuroraVeil => "auroraveil",
            }),
        }
    }

    /// Showdown's `resolvePriority` default subOrder by effect kind.
    fn default_sub_order(&self, effect: Effect, holder: Holder) -> i32 {
        match (effect, holder) {
            (Effect::Ability(a), _) => match self.dex.ability(a).id.as_str() {
                "poisontouch" | "perishbody" => 6,
                "stall" => 9,
                _ => 7,
            },
            (Effect::Item(_), _) => 8,
            (Effect::Volatile(_), _) => 2,
            (Effect::Screen(_), _) => 4,
            (Effect::Weather(_), _) | (Effect::Terrain(_), _) => 5,
            (Effect::MoveSelf(_), _) => 0,
        }
    }

    fn push(
        &self,
        out: &mut Vec<Ref>,
        effect: Effect,
        holder: Holder,
        hook: &str,
        am: &ActiveMove,
    ) {
        let h = self.handlers(effect);
        if !h.has(hook) {
            return;
        }
        if let Holder::Mon(i) = holder {
            match effect {
                Effect::Ability(a)
                    if self.dex.ability(a).breakable && self.suppressing_ability(i, am) =>
                {
                    return
                }
                Effect::Item(_) if self.ignoring_item(i) => return,
                _ => {}
            }
        }
        if matches!(effect, Effect::Weather(_)) && self.suppressing_weather() {
            return;
        }
        let speed = match holder {
            Holder::Mon(i) => self.mon(i).speed,
            _ => 0,
        };
        let sub_order = match h.hooks.get(&format!("{hook}SubOrder")) {
            Some(&s) if s != 0 => s,
            _ => self.default_sub_order(effect, holder),
        };
        out.push(Ref {
            effect,
            holder,
            hook: hook.to_string(),
            order: h
                .hooks
                .get(&format!("{hook}Order"))
                .map_or(NO_ORDER, |&o| o as i64),
            priority: h.hook(&format!("{hook}Priority")),
            speed,
            sub_order,
        });
    }

    /// Effects a Pokemon holds: volatiles, ability, item.
    fn mon_effects(&self, i: usize) -> Vec<Effect> {
        let m = self.mon(i);
        let v = m.volatiles;
        let mut out = Vec::new();
        if v.helping_hand > 0 {
            out.push(Effect::Volatile(VolatileKind::HelpingHand));
        }
        if v.charge {
            out.push(Effect::Volatile(VolatileKind::Charge));
        }
        if v.flash_fire {
            out.push(Effect::Volatile(VolatileKind::FlashFire));
        }
        if v.glaive_rush {
            out.push(Effect::Volatile(VolatileKind::GlaiveRush));
        }
        if v.gem {
            out.push(Effect::Volatile(VolatileKind::Gem));
        }
        if let Some(m) = v.semi_invulnerable {
            out.push(Effect::Volatile(VolatileKind::SemiInvulnerable(m)));
        }
        out.push(Effect::Ability(m.ability));
        if let Some(it) = m.item {
            out.push(Effect::Item(it));
        }
        out
    }

    fn side_of(i: usize) -> usize {
        i / 2
    }

    /// Showdown's `findEventHandlers` + `speedSort` for an event whose target
    /// is a Pokemon. `own_move`: the move's own handler takes part (runEvent's
    /// `onEffect`).
    fn collect(
        &self,
        event: &str,
        target: usize,
        source: Option<usize>,
        am: &ActiveMove,
        own_move: bool,
    ) -> Vec<Ref> {
        let mut out = Vec::new();
        let on = format!("on{event}");
        if own_move {
            // The move's handler counts as held by the event target.
            self.push(
                &mut out,
                Effect::MoveSelf(am.id),
                Holder::Mon(target),
                &on,
                am,
            );
        }
        for e in self.mon_effects(target) {
            self.push(&mut out, e, Holder::Mon(target), &on, am);
        }
        let side = Self::side_of(target);
        for i in self.active_indices().collect::<Vec<_>>() {
            let hooks: [String; 2] = if Self::side_of(i) == side {
                [format!("onAlly{event}"), format!("onAny{event}")]
            } else {
                [format!("onFoe{event}"), format!("onAny{event}")]
            };
            for e in self.mon_effects(i) {
                for hook in &hooks {
                    self.push(&mut out, e, Holder::Mon(i), hook, am);
                }
            }
        }
        if let Some(s) = source {
            for e in self.mon_effects(s) {
                self.push(&mut out, e, Holder::Mon(s), &format!("onSource{event}"), am);
            }
        }
        for (s, state) in self.ctx.sides.iter().enumerate() {
            let hooks: [String; 2] = if s == side {
                [on.clone(), format!("onAny{event}")]
            } else {
                [format!("onFoe{event}"), format!("onAny{event}")]
            };
            let screens = [
                (state.reflect, Screen::Reflect),
                (state.light_screen, Screen::Light),
                (state.aurora_veil, Screen::AuroraVeil),
            ];
            for (present, screen) in screens {
                if present {
                    for hook in &hooks {
                        self.push(&mut out, Effect::Screen(screen), Holder::Side(s), hook, am);
                    }
                }
            }
        }
        if self.ctx.weather != Weather::None {
            self.push(
                &mut out,
                Effect::Weather(self.ctx.weather),
                Holder::Field,
                &on,
                am,
            );
        }
        if self.ctx.terrain != Terrain::None {
            self.push(
                &mut out,
                Effect::Terrain(self.ctx.terrain),
                Holder::Field,
                &on,
                am,
            );
        }
        // Showdown's comparePriority. Exact ties are shuffled by Showdown;
        // here they keep collection order.
        out.sort_by(|a, b| {
            a.order
                .cmp(&b.order)
                .then(b.priority.cmp(&a.priority))
                .then(b.speed.cmp(&a.speed))
                .then(a.sub_order.cmp(&b.sub_order))
        });
        out
    }

    /// Apply handlers in order, as runEvent does.
    fn fold(
        &self,
        refs: &[Ref],
        init: i64,
        mut act: impl FnMut(&Ref, i64) -> Res<Act>,
    ) -> Res<i64> {
        let mut value = init;
        let mut m = ONE;
        let mut aura_done = false;
        for r in refs {
            match act(r, value)? {
                Act::None => {}
                Act::Chain(x) => m = chain(m, x),
                Act::Modify(x) => value = modify(value as u64, x) as i64,
                Act::Technician => {
                    if modify(value as u64, m) <= 60 {
                        m = chain(m, of(3, 2));
                    }
                }
                Act::FairyAura => {
                    if !aura_done {
                        aura_done = true;
                        m = chain(m, 5448);
                    }
                }
            }
        }
        if value >= 0 {
            value = modify(value as u64, m) as i64;
        }
        Ok(value)
    }

    fn effect_name(&self, e: Effect) -> String {
        match e {
            Effect::Ability(a) => format!("ability {}", self.dex.ability(a).id),
            Effect::Item(it) => format!("item {}", self.dex.item(it).id),
            Effect::MoveSelf(m) => format!("move {}", self.dex.move_data(m).id),
            other => format!("{other:?}"),
        }
    }

    fn not_implemented<T>(&self, r: &Ref) -> Res<T> {
        unsupported(format!("{}.{}", self.effect_name(r.effect), r.hook))
    }

    // --- The calculation -------------------------------------------------

    /// useMoveInner: the move's own ModifyType/ModifyMove, then everyone's.
    fn modify_type_and_move(&self, am: &mut ActiveMove, data: &MoveData) -> Res<()> {
        let a = self.ctx.attacker;
        let attacker = self.attacker();
        let species = self.dex.species(attacker.species);
        if data.handlers.has("onModifyType") {
            match data.id.as_str() {
                "weatherball" => {
                    if let Some(t) = match self.weather_for(Asker::Move) {
                        Weather::Sun => Some("Fire"),
                        Weather::Rain => Some("Water"),
                        Weather::Sand => Some("Rock"),
                        Weather::Snow => Some("Ice"),
                        Weather::None => None,
                    } {
                        am.move_type = self.ty(t);
                    }
                }
                "terrainpulse" => {
                    if self.is_grounded(a, am) {
                        if let Some(t) = match self.ctx.terrain {
                            Terrain::Electric => Some("Electric"),
                            Terrain::Grassy => Some("Grass"),
                            Terrain::Misty => Some("Fairy"),
                            Terrain::Psychic => Some("Psychic"),
                            Terrain::None => None,
                        } {
                            am.move_type = self.ty(t);
                        }
                    }
                }
                "aurawheel" => {
                    am.move_type = self.ty(if species.name == "Morpeko-Hangry" {
                        "Dark"
                    } else {
                        "Electric"
                    });
                }
                "ragingbull" => match species.name.as_str() {
                    "Tauros-Paldea-Combat" => am.move_type = self.ty("Fighting"),
                    "Tauros-Paldea-Blaze" => am.move_type = self.ty("Fire"),
                    "Tauros-Paldea-Aqua" => am.move_type = self.ty("Water"),
                    _ => {}
                },
                other => return unsupported(format!("move {other}.onModifyType")),
            }
        }
        if data.handlers.has("onModifyMove") {
            match data.id.as_str() {
                "weatherball" => {
                    if self.weather_for(Asker::Move) != Weather::None {
                        am.base_power *= 2;
                    }
                }
                "terrainpulse" => {
                    if self.ctx.terrain != Terrain::None && self.is_grounded(a, am) {
                        am.base_power *= 2;
                    }
                }
                "expandingforce" => {
                    if self.ctx.terrain == Terrain::Psychic && self.is_grounded(a, am) {
                        am.target = MoveTarget::AllAdjacentFoes;
                    }
                }
                // Accuracy only.
                "hurricane" | "thunder" | "blizzard" => {}
                // The hit count; the battle sets each hit's power.
                "beatup" => {}
                "struggle" => am.move_type = TYPELESS,
                other => return unsupported(format!("move {other}.onModifyMove")),
            }
        }
        let normal = self.ty("Normal");
        for r in self.collect("ModifyType", a, Some(self.ctx.defender), am, false) {
            match (r.effect, r.hook.as_str()) {
                (Effect::Ability(ab), "onModifyType") => {
                    let to = match self.dex.ability(ab).id.as_str() {
                        "aerilate" => "Flying",
                        "pixilate" => "Fairy",
                        "refrigerate" => "Ice",
                        "galvanize" => "Electric",
                        "dragonize" => "Dragon",
                        "liquidvoice" => {
                            if data.flags.has("sound") {
                                am.move_type = self.ty("Water");
                            }
                            continue;
                        }
                        _ => return self.not_implemented(&r),
                    };
                    if am.move_type == normal && !NO_MODIFY_TYPE.contains(&data.id.as_str()) {
                        am.move_type = self.ty(to);
                        am.type_changer = Some(ab);
                    }
                }
                _ => return self.not_implemented(&r),
            }
        }
        for r in self.collect("ModifyMove", a, Some(self.ctx.defender), am, false) {
            match (r.effect, r.hook.as_str()) {
                (Effect::Ability(ab), "onModifyMove") => match self.dex.ability(ab).id.as_str() {
                    "moldbreaker" => am.ignore_ability = true,
                    "scrappy" => {
                        let bits = (1u32 << self.ty("Fighting").0) | (1u32 << normal.0);
                        am.ignore_immunity = match am.ignore_immunity {
                            IgnoreImmunity::All => IgnoreImmunity::All,
                            IgnoreImmunity::No => IgnoreImmunity::Types(bits),
                            IgnoreImmunity::Types(t) => IgnoreImmunity::Types(t | bits),
                        };
                    }
                    "infiltrator" => am.infiltrates = true,
                    "sheerforce" => {
                        if data.has_secondaries {
                            am.has_sheer_force = true;
                        }
                    }
                    // Accuracy, hit count or target tracking only.
                    "skilllink" | "keeneye" | "illuminate" | "stalwart" => {}
                    // Aegislash's forme change; the battle does it first.
                    "stancechange" => {}
                    _ => return self.not_implemented(&r),
                },
                // Choice lock, extra flinch chance.
                (Effect::Item(it), "onModifyMove")
                    if matches!(self.dex.item(it).id.as_str(), "choicescarf" | "kingsrock") => {}
                _ => return self.not_implemented(&r),
            }
        }
        Ok(())
    }

    /// `runImmunity(move)`: false if the defender is immune by type.
    fn run_immunity(&self, am: &ActiveMove) -> bool {
        if am.move_type == TYPELESS {
            return true;
        }
        match am.ignore_immunity {
            IgnoreImmunity::All => return true,
            IgnoreImmunity::Types(bits) if bits & (1 << am.move_type.0) != 0 => return true,
            _ => {}
        }
        let d = self.ctx.defender;
        if am.move_type == self.ty("Ground") {
            return self.is_grounded(d, am);
        }
        let types = self.defender().types;
        !types.iter().any(|&t| self.dex.type_immune(am.move_type, t))
    }

    fn get_damage(&self, am: &mut ActiveMove, data: &MoveData) -> Res<Outcome> {
        let (a, d) = (self.ctx.attacker, self.ctx.defender);
        let attacker = self.attacker();
        let defender = self.defender();

        // Events this calculation doesn't model must not be in play.
        for event in ["NegateImmunity", "Type"] {
            for i in [a, d] {
                if let Some(r) = self.collect(event, i, None, am, false).first() {
                    return self.not_implemented(r);
                }
            }
        }

        if !self.run_immunity(am) {
            return Ok(Outcome::Immune);
        }
        // OHKO moves deal the target's max HP.
        if data.ohko {
            return Ok(Outcome::Damage([defender.max_hp() as u32; 16]));
        }
        if data.handlers.has("damageCallback") {
            let dmg = match data.id.as_str() {
                "superfang" => (defender.hp as u32 / 2).max(1),
                "finalgambit" => attacker.hp as u32,
                "endeavor" => (defender.hp as i64 - attacker.hp as i64).max(0) as u32,
                other => return unsupported(format!("move {other}.damageCallback")),
            };
            return Ok(Outcome::Damage([dmg; 16]));
        }
        if let Some(fixed) = data.fixed_damage {
            let dmg = match fixed {
                crate::dex::FixedDamage::Level => 50,
                crate::dex::FixedDamage::Amount(n) => n as u32,
            };
            return Ok(Outcome::Damage([dmg; 16]));
        }

        let category = am.category;
        let mut bp = am.base_power as i64;
        if data.handlers.has("basePowerCallback") {
            bp = self.base_power_callback(am, data)?;
        }
        if bp <= 0 {
            return Ok(Outcome::NoDamage);
        }

        let crit = self.ctx.crit && self.critical_hit(am)?;

        let bp = self.base_power_event(am, bp)?;
        if bp == 0 {
            return Ok(Outcome::Damage([0; 16]));
        }
        let bp = bp.max(1) as u64;

        // Attack and defense.
        let stat_holder = if data.override_offensive_target {
            defender
        } else {
            attacker
        };
        let physical = category == Category::Physical;
        let atk_stat = data
            .override_offensive_stat
            .unwrap_or(if physical { ATK } else { SPA });
        let def_stat = data
            .override_defensive_stat
            .unwrap_or(if physical { DEF } else { SPD });
        let mut atk_boost = stat_holder.boosts[atk_stat];
        let mut def_boost = defender.boosts[def_stat];
        if data.ignore_offensive || (crit && atk_boost < 0) {
            atk_boost = 0;
        }
        if data.ignore_defensive || (crit && def_boost > 0) {
            def_boost = 0;
        }
        // calculateStat runs ModifyBoost for the move's user and the target.
        let atk_boost = self.modify_boost(am, a, atk_stat, atk_boost)?;
        let def_boost = self.modify_boost(am, d, def_stat, def_boost)?;
        let attack = boosted(stat_holder.stats[atk_stat], atk_boost);
        let defense = boosted(defender.stats[def_stat], def_boost);
        let atk_event = if physical { "ModifyAtk" } else { "ModifySpA" };
        let attack = self.stat_event(atk_event, a, d, am, attack)? as u64;
        let def_event = if def_stat == DEF {
            "ModifyDef"
        } else {
            "ModifySpD"
        };
        let defense = self.stat_event(def_event, d, a, am, defense)? as u64;

        let base = (22 * bp * attack / defense) / 50;

        // modifyDamage (champions mod).
        let mut dmg = base + 2;
        if self.ctx.spread {
            dmg = modify(dmg, of(3, 4));
        } else if am.parental_bond && self.ctx.hit > 1 {
            dmg = modify(dmg, of(1, 4));
        }
        dmg = self.weather_modify_damage(am, dmg);
        if crit {
            dmg = dmg * 3 / 2;
        }

        let stab = self.stab(am)?;
        let type_mod = self.type_mod(am, data)?;
        let mut rolls = [0u32; 16];
        for (r, out) in rolls.iter_mut().enumerate() {
            let mut x = dmg * (100 - r as u64) / 100;
            x = modify(x, stab);
            if type_mod > 0 {
                x <<= type_mod;
            } else {
                for _ in type_mod..0 {
                    x /= 2;
                }
            }
            if attacker.status == Status::Burn
                && category == Category::Physical
                && !self.has_ability(a, "guts")
                && data.id != "facade"
            {
                x = modify(x, of(1, 2));
            }
            x = self.modify_damage_event(am, x, type_mod, crit)?;
            if self.ctx.bypass_protect {
                x = modify(x, of(1, 4));
            }
            if x == 0 {
                x = 1;
            }
            *out = (x % 65536) as u32;
        }
        Ok(Outcome::Damage(rolls))
    }

    fn base_power_callback(&self, am: &ActiveMove, data: &MoveData) -> Res<i64> {
        let (a, d) = (self.ctx.attacker, self.ctx.defender);
        let attacker = self.attacker();
        let defender = self.defender();
        let bp = am.base_power as i64;
        let weight_bp = |w: u32| match w {
            w if w >= 2000 => 120,
            w if w >= 1000 => 100,
            w if w >= 500 => 80,
            w if w >= 250 => 60,
            w if w >= 100 => 40,
            _ => 20,
        };
        let ratio_bp = |user: u32, target: u32| match user {
            u if u >= target * 5 => 120,
            u if u >= target * 4 => 100,
            u if u >= target * 3 => 80,
            u if u >= target * 2 => 60,
            _ => 40,
        };
        let positive_boosts = attacker
            .boosts
            .iter()
            .filter(|&&b| b > 0)
            .map(|&b| b as i64)
            .sum::<i64>();
        Ok(match data.id.as_str() {
            "acrobatics" => {
                if attacker.item.is_none() {
                    bp * 2
                } else {
                    bp
                }
            }
            // A fractional base power is truthy, then clamped to at least 1.
            "eruption" | "waterspout" => {
                (bp * attacker.hp as i64 / attacker.max_hp() as i64).max(attacker.hp.min(1) as i64)
            }
            "lowkick" | "grassknot" => weight_bp(self.weight(d, am)?),
            "heavyslam" | "heatcrash" => ratio_bp(self.weight(a, am)?, self.weight(d, am)?),
            "hardpress" => {
                let hp = defender.hp as i64;
                let max = defender.max_hp() as i64;
                let v = ((100 * (100 * (hp * 4096 / max)) + 2047) / 4096) / 100;
                if v == 0 {
                    1
                } else {
                    v
                }
            }
            "hex" => {
                if defender.status != Status::None {
                    bp * 2
                } else {
                    bp
                }
            }
            "lastrespects" => 50 + 50 * self.ctx.sides[Self::side_of(a)].fainted as i64,
            "powertrip" | "storedpower" => bp + 20 * positive_boosts,
            "ragefist" => (50 + 50 * attacker.times_attacked as i64).min(350),
            "reversal" => {
                let ratio = (attacker.hp as i64 * 48 / attacker.max_hp() as i64).max(1);
                match ratio {
                    r if r < 2 => 200,
                    r if r < 5 => 150,
                    r if r < 10 => 100,
                    r if r < 17 => 80,
                    r if r < 33 => 40,
                    _ => 20,
                }
            }
            "risingvoltage" => {
                if self.ctx.terrain == Terrain::Electric && self.is_grounded(d, am) {
                    bp * 2
                } else {
                    bp
                }
            }
            "tripleaxel" => 20 * self.ctx.hit as i64,
            // The battle sets each hit's power from that ally's base Attack.
            "beatup" => am.base_power as i64,
            "watershuriken" => bp, // only Ash-Greninja changes it
            // moveLastTurnResult === false
            // Doubled unless the target is newly switched in or still to move.
            "payback" => {
                if defender.moved_this_turn {
                    bp * 2
                } else {
                    bp
                }
            }
            "stompingtantrum" | "temperflare" => {
                if attacker.move_last_turn_failed {
                    bp * 2
                } else {
                    bp
                }
            }
            "gyroball" => {
                let user = self.speed_stat(a)?;
                let target = self.speed_stat(d)?;
                if user == 0 {
                    1
                } else {
                    (25 * target as i64 / user as i64 + 1).min(150)
                }
            }
            other => return unsupported(format!("move {other}.basePowerCallback")),
        })
    }

    /// `getStat('spe')` for Gyro Ball: boosted Speed. Speed modifiers
    /// (Choice Scarf, paralysis, Tailwind...) aren't modelled here yet.
    fn speed_stat(&self, i: usize) -> Res<u64> {
        Ok(self.mon(i).spe_stat as u64)
    }

    /// The ModifyBoost event for `stat_user`'s stage in `stat`: Unaware
    /// ignores the attacker's offensive stages (when the target has it) or the
    /// target's defensive stages (when the attacker has it).
    fn modify_boost(&self, am: &ActiveMove, stat_user: usize, stat: usize, boost: i8) -> Res<i8> {
        let (a, d) = (self.ctx.attacker, self.ctx.defender);
        let mut b = boost;
        for r in self.collect("ModifyBoost", stat_user, None, am, false) {
            match (r.effect, r.hook.as_str()) {
                (Effect::Ability(ab), "onAnyModifyBoost")
                    if self.dex.ability(ab).id == "unaware" =>
                {
                    let Holder::Mon(holder) = r.holder else {
                        unreachable!()
                    };
                    if holder == stat_user {
                        continue;
                    }
                    if holder == a && stat_user == d && matches!(stat, DEF | SPD) {
                        b = 0;
                    }
                    if stat_user == a && holder == d && matches!(stat, ATK | DEF | SPA) {
                        b = 0;
                    }
                }
                _ => return self.not_implemented(&r),
            }
        }
        Ok(b)
    }

    /// The CriticalHit event: Battle Armor / Shell Armor stop crits.
    /// Mimikyu with its disguise still up.
    fn disguised(&self, i: usize) -> bool {
        self.dex.species(self.mon(i).species).name == "Mimikyu"
    }

    fn critical_hit(&self, am: &ActiveMove) -> Res<bool> {
        let d = self.ctx.defender;
        let ab = self.dex.ability(self.mon(d).ability);
        if ab.blocks_crit && !(ab.breakable && self.suppressing_ability(d, am)) {
            return Ok(false);
        }
        if let Some(r) = self.collect("CriticalHit", d, None, am, false).first() {
            return match (r.effect, r.hook.as_str()) {
                // Disguise: no crit on an intact disguise.
                (Effect::Ability(ab), "onCriticalHit") if self.dex.ability(ab).id == "disguise" => {
                    Ok(!(self.disguised(d) && !self.ctx.hit_sub && self.run_immunity(am)))
                }
                _ => self.not_implemented(r),
            };
        }
        Ok(true)
    }

    fn base_power_event(&self, am: &ActiveMove, bp: i64) -> Res<i64> {
        let (a, d) = (self.ctx.attacker, self.ctx.defender);
        let attacker = self.attacker();
        let defender = self.defender();
        let mv = self.dex.move_data(am.id);
        let refs = self.collect("BasePower", a, Some(d), am, true);
        self.fold(&refs, bp, |r, _| {
            Ok(match (r.effect, r.hook.as_str()) {
                (Effect::MoveSelf(_), "onBasePower") => match mv.id.as_str() {
                    "lashout" => {
                        if attacker.stats_lowered_this_turn {
                            Act::Chain(of(2, 1))
                        } else {
                            Act::None
                        }
                    }
                    "barbbarrage" | "venoshock" => {
                        if matches!(defender.status, Status::Poison | Status::Toxic) {
                            Act::Chain(of(2, 1))
                        } else {
                            Act::None
                        }
                    }
                    "expandingforce" => {
                        if self.ctx.terrain == Terrain::Psychic && self.is_grounded(a, am) {
                            Act::Chain(of(3, 2))
                        } else {
                            Act::None
                        }
                    }
                    "facade" => {
                        if !matches!(attacker.status, Status::None | Status::Sleep) {
                            Act::Chain(of(2, 1))
                        } else {
                            Act::None
                        }
                    }
                    "knockoff" => {
                        if self.can_remove_item(d) {
                            Act::Chain(of(3, 2))
                        } else {
                            Act::None
                        }
                    }
                    "solarbeam" | "solarblade" => {
                        if matches!(
                            self.weather_for(Asker::Move),
                            Weather::Rain | Weather::Sand | Weather::Snow
                        ) {
                            Act::Chain(of(1, 2))
                        } else {
                            Act::None
                        }
                    }
                    _ => return self.not_implemented(r),
                },
                (Effect::Volatile(VolatileKind::HelpingHand), "onBasePower") => {
                    let mut m = ONE;
                    for _ in 0..attacker.volatiles.helping_hand {
                        m = m * 3 / 2;
                    }
                    Act::Chain(m)
                }
                (Effect::Volatile(VolatileKind::Charge), "onBasePower") => {
                    if am.move_type == self.ty("Electric") {
                        Act::Chain(of(2, 1))
                    } else {
                        Act::None
                    }
                }
                (Effect::Volatile(VolatileKind::Gem), "onBasePower") => Act::Chain(5325),
                (Effect::Ability(ab), "onBasePower") => {
                    let flag = |f: &str| mv.flags.has(f);
                    let yes = |c: bool, m: u32| if c { Act::Chain(m) } else { Act::None };
                    match self.dex.ability(ab).id.as_str() {
                        "aerilate" | "pixilate" | "refrigerate" | "galvanize" | "dragonize" => {
                            yes(am.type_changer == Some(ab), 4915)
                        }
                        "ironfist" => yes(flag("punch"), 4915),
                        "megalauncher" => yes(flag("pulse"), of(3, 2)),
                        "sharpness" => yes(flag("slicing"), of(3, 2)),
                        "strongjaw" => yes(flag("bite"), of(3, 2)),
                        "toughclaws" => yes(flag("contact"), 5325),
                        "punkrock" => yes(flag("sound"), 5325),
                        "reckless" => yes(mv.has_recoil || mv.has_crash_damage, 4915),
                        "sheerforce" => yes(am.has_sheer_force, 5325),
                        "technician" => Act::Technician,
                        "sandforce" => {
                            let t = am.move_type;
                            yes(
                                self.field_weather() == Weather::Sand
                                    && [self.ty("Rock"), self.ty("Ground"), self.ty("Steel")]
                                        .contains(&t),
                                5325,
                            )
                        }
                        "supremeoverlord" => {
                            const POW: [u32; 6] = [4096, 4506, 4915, 5325, 5734, 6144];
                            let fallen = attacker.fallen.min(5) as usize;
                            yes(fallen > 0, POW[fallen])
                        }
                        _ => return self.not_implemented(r),
                    }
                }
                (Effect::Item(it), "onBasePower") => {
                    let id = self.dex.item(it).id.as_str();
                    if let Some(t) = type_boost_item(id) {
                        if am.move_type == self.ty(t) {
                            Act::Chain(4915)
                        } else {
                            Act::None
                        }
                    } else {
                        match id {
                            "muscleband" if am.category == Category::Physical => Act::Chain(4505),
                            "wiseglasses" if am.category == Category::Special => Act::Chain(4505),
                            "muscleband" | "wiseglasses" => Act::None,
                            _ => return self.not_implemented(r),
                        }
                    }
                }
                (Effect::Ability(ab), "onAllyBasePower") => {
                    match self.dex.ability(ab).id.as_str() {
                        "steelyspirit" => {
                            if am.move_type == self.ty("Steel") {
                                Act::Chain(of(3, 2))
                            } else {
                                Act::None
                            }
                        }
                        _ => return self.not_implemented(r),
                    }
                }
                (Effect::Ability(ab), "onAnyBasePower") => match self.dex.ability(ab).id.as_str() {
                    "fairyaura" => {
                        if d == a
                            || am.category == Category::Status
                            || am.move_type != self.ty("Fairy")
                        {
                            Act::None
                        } else {
                            Act::FairyAura
                        }
                    }
                    _ => return self.not_implemented(r),
                },
                // Gust and Twister on a Bounce user.
                (Effect::Volatile(VolatileKind::SemiInvulnerable(_)), "onSourceBasePower") => {
                    if ["gust", "twister"].contains(&mv.id.as_str()) {
                        Act::Chain(of(2, 1))
                    } else {
                        Act::None
                    }
                }
                (Effect::Ability(ab), "onSourceBasePower") => {
                    match self.dex.ability(ab).id.as_str() {
                        "dryskin" => {
                            if am.move_type == self.ty("Fire") {
                                Act::Chain(of(5, 4))
                            } else {
                                Act::None
                            }
                        }
                        _ => return self.not_implemented(r),
                    }
                }
                (Effect::Terrain(t), "onBasePower") => match t {
                    Terrain::Grassy => {
                        if matches!(mv.id.as_str(), "earthquake" | "bulldoze" | "magnitude")
                            && self.is_grounded(d, am)
                        {
                            Act::Chain(of(1, 2))
                        } else if am.move_type == self.ty("Grass") && self.is_grounded(a, am) {
                            Act::Chain(5325)
                        } else {
                            Act::None
                        }
                    }
                    Terrain::Electric | Terrain::Psychic => {
                        let boosted = if t == Terrain::Electric {
                            "Electric"
                        } else {
                            "Psychic"
                        };
                        if am.move_type == self.ty(boosted) && self.is_grounded(a, am) {
                            Act::Chain(5325)
                        } else {
                            Act::None
                        }
                    }
                    Terrain::Misty => {
                        if am.move_type == self.ty("Dragon") && self.is_grounded(d, am) {
                            Act::Chain(of(1, 2))
                        } else {
                            Act::None
                        }
                    }
                    Terrain::None => Act::None,
                },
                _ => return self.not_implemented(r),
            })
        })
    }

    /// Knock Off's TakeItem check: a Mega Stone can't be taken from the
    /// species it Mega Evolves.
    fn can_remove_item(&self, i: usize) -> bool {
        let Some(it) = self.mon(i).item else {
            return false;
        };
        let item = self.dex.item(it);
        if item.take_forbidden {
            return false;
        }
        !self.dex.mega_stone_stays(it, self.mon(i).species)
    }

    /// ModifyAtk/SpA (target: attacker, source: defender) and ModifyDef/SpD
    /// (target: defender, source: attacker).
    fn stat_event(
        &self,
        event: &'static str,
        target: usize,
        source: usize,
        am: &ActiveMove,
        stat: u64,
    ) -> Res<i64> {
        let refs = self.collect(event, target, Some(source), am, false);
        let holder_mon = self.mon(target);
        let other = self.mon(source);
        let t = |n: &str| self.ty(n);
        let mv_type = am.move_type;
        let pinch =
            |ty: &str| mv_type == t(ty) && holder_mon.hp as u32 * 3 <= holder_mon.max_hp() as u32;
        let hook_self = format!("on{event}");
        let hook_source = format!("onSource{event}");
        self.fold(&refs, stat as i64, |r, _| {
            let yes = |c: bool, m: u32| if c { Act::Chain(m) } else { Act::None };
            let hook = r.hook.as_str();
            Ok(match r.effect {
                Effect::Ability(ab) if hook == hook_self => {
                    let id = self.dex.ability(ab).id.as_str();
                    match (event, id) {
                        ("ModifyAtk" | "ModifySpA", "blaze") => yes(pinch("Fire"), of(3, 2)),
                        ("ModifyAtk" | "ModifySpA", "overgrow") => yes(pinch("Grass"), of(3, 2)),
                        ("ModifyAtk" | "ModifySpA", "torrent") => yes(pinch("Water"), of(3, 2)),
                        ("ModifyAtk" | "ModifySpA", "swarm") => yes(pinch("Bug"), of(3, 2)),
                        ("ModifyAtk" | "ModifySpA", "firemane") => {
                            yes(mv_type == t("Fire"), of(3, 2))
                        }
                        ("ModifyAtk" | "ModifySpA", "waterbubble") => {
                            yes(mv_type == t("Water"), of(2, 1))
                        }
                        ("ModifyAtk" | "ModifySpA", "stakeout") => {
                            yes(other.active_turns == 0, of(2, 1))
                        }
                        ("ModifyAtk", "guts") => yes(holder_mon.status != Status::None, of(3, 2)),
                        ("ModifyAtk", "hugepower" | "purepower") => Act::Chain(of(2, 1)),
                        ("ModifyAtk", "hustle") => Act::Modify(of(3, 2)),
                        ("ModifySpA", "solarpower") => yes(
                            self.weather_for(Asker::Ability("solarpower")) == Weather::Sun,
                            of(3, 2),
                        ),
                        ("ModifySpA", "plus" | "minus") => {
                            let side = Self::side_of(target);
                            let ally_has = self.active_indices().any(|i| {
                                i != target
                                    && Self::side_of(i) == side
                                    && matches!(self.ability_id(i), "plus" | "minus")
                            });
                            yes(ally_has, of(3, 2))
                        }
                        ("ModifyDef", "furcoat") => Act::Chain(of(2, 1)),
                        ("ModifyDef", "grasspelt") => {
                            yes(self.ctx.terrain == Terrain::Grassy, of(3, 2))
                        }
                        ("ModifyDef", "marvelscale") => {
                            yes(holder_mon.status != Status::None, of(3, 2))
                        }
                        _ => return self.not_implemented(r),
                    }
                }
                Effect::Ability(ab) if hook == hook_source => {
                    let id = self.dex.ability(ab).id.as_str();
                    match (event, id) {
                        ("ModifyAtk" | "ModifySpA", "thickfat") => {
                            yes(mv_type == t("Fire") || mv_type == t("Ice"), of(1, 2))
                        }
                        ("ModifyAtk" | "ModifySpA", "heatproof" | "waterbubble") => {
                            yes(mv_type == t("Fire"), of(1, 2))
                        }
                        ("ModifyAtk" | "ModifySpA", "purifyingsalt") => {
                            yes(mv_type == t("Ghost"), of(1, 2))
                        }
                        _ => return self.not_implemented(r),
                    }
                }
                Effect::Volatile(VolatileKind::FlashFire) if hook == hook_self => yes(
                    mv_type == t("Fire") && self.has_ability(target, "flashfire"),
                    of(3, 2),
                ),
                Effect::Item(it) if hook == hook_self => {
                    match (event, self.dex.item(it).id.as_str()) {
                        ("ModifyAtk" | "ModifySpA", "lightball") => {
                            let base = &self.dex.species(holder_mon.species).base_species;
                            yes(base == "Pikachu", of(2, 1))
                        }
                        _ => return self.not_implemented(r),
                    }
                }
                Effect::Weather(w) if hook == hook_self => match (event, w) {
                    ("ModifySpD", Weather::Sand) => {
                        let applies = holder_mon.has_type(t("Rock"))
                            && self.weather_for(Asker::Weather) == Weather::Sand;
                        if applies {
                            Act::Modify(of(3, 2))
                        } else {
                            Act::None
                        }
                    }
                    ("ModifyDef", Weather::Snow) => {
                        let applies = holder_mon.has_type(t("Ice"))
                            && self.weather_for(Asker::Weather) == Weather::Snow;
                        if applies {
                            Act::Modify(of(3, 2))
                        } else {
                            Act::None
                        }
                    }
                    _ => return self.not_implemented(r),
                },
                _ => return self.not_implemented(r),
            })
        })
    }

    /// WeatherModifyDamage is a priorityEvent: the first handler to return a
    /// value ends it. Mega Sol (priority 1) applies sun's effect and returns.
    fn weather_modify_damage(&self, am: &ActiveMove, dmg: u64) -> u64 {
        let fire = self.ty("Fire");
        let water = self.ty("Water");
        let sun = |t: TypeId| {
            if t == fire {
                of(3, 2)
            } else if t == water {
                of(1, 2)
            } else {
                ONE
            }
        };
        if self.has_ability(self.ctx.attacker, "megasol") {
            return modify(dmg, sun(am.move_type));
        }
        let m = match self.field_weather() {
            Weather::Sun if self.weather_for(Asker::Weather) == Weather::Sun => sun(am.move_type),
            Weather::Rain if self.weather_for(Asker::Weather) == Weather::Rain => {
                if am.move_type == water {
                    of(3, 2)
                } else if am.move_type == fire {
                    of(1, 2)
                } else {
                    ONE
                }
            }
            _ => ONE,
        };
        modify(dmg, m)
    }

    /// STAB as a 4096-based modifier, after ModifySTAB (Adaptability).
    fn stab(&self, am: &ActiveMove) -> Res<u32> {
        let a = self.ctx.attacker;
        // "???" never gets STAB.
        let is_stab = am.move_type != TYPELESS && self.attacker().has_type(am.move_type);
        let mut stab: u32 = if is_stab { of(3, 2) } else { ONE };
        for r in self.collect("ModifySTAB", a, Some(self.ctx.defender), am, false) {
            match (r.effect, r.hook.as_str()) {
                (Effect::Ability(ab), "onModifySTAB")
                    if self.dex.ability(ab).id == "adaptability" =>
                {
                    if is_stab {
                        stab = if stab == of(2, 1) { 9216 } else { of(2, 1) };
                    }
                }
                _ => return self.not_implemented(&r),
            }
        }
        Ok(stab)
    }

    /// `runEffectiveness`: the sum of per-type steps, clamped to +-6.
    fn type_mod(&self, am: &ActiveMove, data: &MoveData) -> Res<i32> {
        let d = self.ctx.defender;
        let defender = self.defender();
        let mut types = vec![defender.types[0]];
        if defender.types[1] != defender.types[0] {
            types.push(defender.types[1]);
        }
        let refs = self.collect("Effectiveness", d, None, am, false);
        if am.move_type == TYPELESS {
            return Ok(0);
        }
        let mut total = 0i32;
        for t in types {
            let mut m = self.dex.type_mod(am.move_type, t) as i32;
            if data.handlers.has("onEffectiveness") {
                match data.id.as_str() {
                    "freezedry" => {
                        if t == self.ty("Water") {
                            m = 1;
                        }
                    }
                    other => return unsupported(format!("move {other}.onEffectiveness")),
                }
            }
            for r in &refs {
                match (r.effect, r.hook.as_str()) {
                    // Disguise: an intact disguise takes everything neutrally.
                    (Effect::Ability(ab), "onEffectiveness")
                        if self.dex.ability(ab).id == "disguise" =>
                    {
                        if am.category != Category::Status
                            && self.disguised(d)
                            && !self.ctx.hit_sub
                            && self.run_immunity(am)
                        {
                            m = 0;
                        }
                    }
                    (Effect::Item(it), "onEffectiveness") if self.dex.item(it).id == "ironball" => {
                        if am.move_type == self.ty("Ground") && defender.has_type(self.ty("Flying"))
                        {
                            m = 0;
                        }
                    }
                    _ => return self.not_implemented(r),
                }
            }
            total += m;
        }
        Ok(total.clamp(-6, 6))
    }

    /// The ModifyDamage event (target: attacker, source: defender).
    fn modify_damage_event(
        &self,
        am: &ActiveMove,
        dmg: u64,
        type_mod: i32,
        crit: bool,
    ) -> Res<u64> {
        let (a, d) = (self.ctx.attacker, self.ctx.defender);
        let defender = self.defender();
        let mv = self.dex.move_data(am.id);
        let refs = self.collect("ModifyDamage", a, Some(d), am, false);
        let v = self.fold(&refs, dmg as i64, |r, _| {
            let yes = |c: bool, m: u32| if c { Act::Chain(m) } else { Act::None };
            Ok(match (r.effect, r.hook.as_str()) {
                (Effect::Ability(ab), "onModifyDamage") => match self.dex.ability(ab).id.as_str() {
                    "sniper" => yes(crit, of(3, 2)),
                    _ => return self.not_implemented(r),
                },
                (Effect::Item(it), "onModifyDamage") => match self.dex.item(it).id.as_str() {
                    "lifeorb" => Act::Chain(5324),
                    "expertbelt" => yes(type_mod > 0, 4915),
                    _ => return self.not_implemented(r),
                },
                (Effect::Ability(ab), "onAnyModifyDamage") => {
                    match self.dex.ability(ab).id.as_str() {
                        "friendguard" => {
                            let Holder::Mon(h) = r.holder else {
                                unreachable!()
                            };
                            yes(d != h && Self::side_of(d) == Self::side_of(h), of(3, 4))
                        }
                        _ => return self.not_implemented(r),
                    }
                }
                (Effect::Ability(ab), "onSourceModifyDamage") => {
                    let flag = |f: &str| mv.flags.has(f);
                    match self.dex.ability(ab).id.as_str() {
                        "auraguard" => yes(flag("contact"), of(1, 2)),
                        "multiscale" => yes(defender.hp >= defender.max_hp(), of(1, 2)),
                        "filter" | "solidrock" => yes(type_mod > 0, of(3, 4)),
                        "punkrock" => yes(flag("sound"), of(1, 2)),
                        "fluffy" => {
                            // chainModify(mod) with mod = 2 (Fire), 0.5 (contact), 1 or both.
                            let fire = am.move_type == self.ty("Fire");
                            match (fire, flag("contact")) {
                                (true, false) => Act::Chain(of(2, 1)),
                                (false, true) => Act::Chain(of(1, 2)),
                                _ => Act::Chain(ONE),
                            }
                        }
                        _ => return self.not_implemented(r),
                    }
                }
                (Effect::Item(it), "onSourceModifyDamage") => {
                    let id = self.dex.item(it).id.as_str();
                    match resist_berry(id) {
                        Some(t) => yes(
                            am.move_type == self.ty(t)
                                && type_mod > 0
                                && self.can_eat(d)
                                && !self.ctx.hit_sub,
                            of(1, 2),
                        ),
                        None => return self.not_implemented(r),
                    }
                }
                (Effect::Volatile(VolatileKind::GlaiveRush), "onSourceModifyDamage") => {
                    Act::Chain(of(2, 1))
                }
                // Earthquake on a Dig user, Surf on a Dive user, Gust on Fly.
                (Effect::Volatile(VolatileKind::SemiInvulnerable(m)), "onSourceModifyDamage") => {
                    let hits = match self.dex.move_data(m).id.as_str() {
                        "dig" => ["earthquake", "magnitude"].contains(&mv.id.as_str()),
                        "dive" => ["surf", "whirlpool"].contains(&mv.id.as_str()),
                        "fly" => ["gust", "twister"].contains(&mv.id.as_str()),
                        _ => false,
                    };
                    yes(hits, of(2, 1))
                }
                (Effect::Screen(s), "onAnyModifyDamage") => {
                    let Holder::Side(side) = r.holder else {
                        unreachable!()
                    };
                    let state = &self.ctx.sides[side];
                    let protects = d != a && Self::side_of(d) == side && !crit && !am.infiltrates;
                    let applies = match s {
                        Screen::Reflect => am.category == Category::Physical,
                        Screen::Light => am.category == Category::Special,
                        Screen::AuroraVeil => {
                            !(state.reflect && am.category == Category::Physical
                                || state.light_screen && am.category == Category::Special)
                        }
                    };
                    yes(protects && applies, 2732)
                }
                _ => return self.not_implemented(r),
            })
        })?;
        Ok(v as u64)
    }

    /// `eatItem` succeeds unless a foe's Unnerve stops it.
    fn can_eat(&self, i: usize) -> bool {
        let side = Self::side_of(i);
        !self
            .active_indices()
            .any(|f| Self::side_of(f) != side && self.has_ability(f, "unnerve"))
    }
}

/// `calculateStat` with a stage: x1.5 per positive stage up to x4, divided
/// likewise for negative stages, flooring.
fn boosted(stat: u16, boost: i8) -> u64 {
    const TABLE: [(u64, u64); 7] = [(1, 1), (3, 2), (2, 1), (5, 2), (3, 1), (7, 2), (4, 1)];
    let b = boost.clamp(-6, 6);
    let (num, den) = TABLE[b.unsigned_abs() as usize];
    if b >= 0 {
        stat as u64 * num / den
    } else {
        stat as u64 * den / num
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stage_multipliers() {
        assert_eq!(boosted(100, 1), 150);
        assert_eq!(boosted(100, -1), 66);
        assert_eq!(boosted(101, 6), 404);
        assert_eq!(boosted(101, -2), 50);
    }
}
