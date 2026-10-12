//! A battle's position as JSON, and a battle rebuilt from one.
//!
//! The Showdown client (python/selfplay/showdown) follows a live game from
//! the protocol and hands the engine the position at each of its
//! decisions; `Battle::from_position` rebuilds a battle there, so the
//! network and the search see the game as they see their own. `position`
//! writes the same shape from a battle. tests/position.rs checks the round
//! trip: a rebuilt battle plays on exactly like the original.
//!
//! Every key but the teams is optional. A missing one takes the value a
//! Pokemon has as it comes in (full HP and PP, no volatiles, its set's item
//! and ability), so the client only fills in what it knows. Ids are
//! Showdown ids; names work too.
//!
//! A position is taken at a decision: the start of a turn ("move"
//! requests), or a switch request. A switch request mid-turn (U-turn,
//! Eject Button) rebuilds with only the turn's residual still to come; the
//! actions left in the turn aren't part of a position.

use super::choice::SideRequest;
use super::state::{
    Attacker, MoveSlot, Mon, SideCondition, StatusState, SwitchFlag, Volatile, VolatileId, ACTIVE_PER_SIDE,
};
use super::{ActionKind, Battle, BattleError, MonRef, Res};
use crate::chance::Chance;
use crate::damage::{Status, Terrain, Weather, TYPELESS};
use crate::dex::{Dex, MoveId, TypeId};
use crate::team::{mega_forme, PokemonSet};
use serde_json::{json, Map, Value};

fn bad(msg: impl std::fmt::Display) -> BattleError {
    BattleError::Illegal(format!("position: {msg}"))
}

fn status_id(s: Status) -> &'static str {
    match s {
        Status::None => "",
        Status::Burn => "brn",
        Status::Paralysis => "par",
        Status::Poison => "psn",
        Status::Toxic => "tox",
        Status::Sleep => "slp",
        Status::Freeze => "frz",
    }
}

fn parse_status(s: &str) -> Res<Status> {
    Ok(match s {
        "" | "fnt" => Status::None,
        "brn" => Status::Burn,
        "par" => Status::Paralysis,
        "psn" => Status::Poison,
        "tox" => Status::Toxic,
        "slp" => Status::Sleep,
        "frz" => Status::Freeze,
        _ => return Err(bad(format!("unknown status {s:?}"))),
    })
}

fn weather_id(w: Weather) -> &'static str {
    match w {
        Weather::None => "",
        Weather::Sun => "sunnyday",
        Weather::Rain => "raindance",
        Weather::Sand => "sandstorm",
        Weather::Snow => "snowscape",
    }
}

fn parse_weather(s: &str) -> Res<Weather> {
    Ok(match crate::dex::to_id(s).as_str() {
        "" | "none" => Weather::None,
        "sunnyday" | "sun" | "desolateland" => Weather::Sun,
        "raindance" | "rain" | "primordialsea" => Weather::Rain,
        "sandstorm" | "sand" => Weather::Sand,
        "snowscape" | "snow" | "hail" => Weather::Snow,
        _ => return Err(bad(format!("unknown weather {s:?}"))),
    })
}

fn terrain_id(t: Terrain) -> &'static str {
    match t {
        Terrain::None => "",
        Terrain::Electric => "electricterrain",
        Terrain::Grassy => "grassyterrain",
        Terrain::Misty => "mistyterrain",
        Terrain::Psychic => "psychicterrain",
    }
}

fn parse_terrain(s: &str) -> Res<Terrain> {
    Ok(match crate::dex::to_id(s).as_str() {
        "" | "none" => Terrain::None,
        "electricterrain" => Terrain::Electric,
        "grassyterrain" => Terrain::Grassy,
        "mistyterrain" => Terrain::Misty,
        "psychicterrain" => Terrain::Psychic,
        _ => return Err(bad(format!("unknown terrain {s:?}"))),
    })
}

fn type_name(t: TypeId) -> String {
    if t == TYPELESS {
        "???".into()
    } else {
        Dex::get().type_names[t.0 as usize].clone()
    }
}

fn parse_type(s: &str) -> Res<TypeId> {
    if s == "???" {
        return Ok(TYPELESS);
    }
    Dex::get().type_id(s).ok_or_else(|| bad(format!("unknown type {s:?}")))
}

fn move_id(s: &str) -> Res<MoveId> {
    Dex::get().move_id(s).ok_or_else(|| bad(format!("unknown move {s:?}")))
}

fn opt_move(v: &Value) -> Res<Option<MoveId>> {
    match v.as_str() {
        None | Some("") => Ok(None),
        Some(s) => move_id(s).map(Some),
    }
}

fn opt_item(v: &Value) -> Res<Option<crate::dex::ItemId>> {
    match v.as_str() {
        None | Some("") => Ok(None),
        Some(s) => Dex::get()
            .item_id(s)
            .map(Some)
            .ok_or_else(|| bad(format!("unknown item {s:?}"))),
    }
}

fn ability_id(s: &str) -> Res<crate::dex::AbilityId> {
    Dex::get()
        .ability_id(s)
        .ok_or_else(|| bad(format!("unknown ability {s:?}")))
}

fn species_id(s: &str) -> Res<crate::dex::SpeciesId> {
    Dex::get()
        .species_id(s)
        .ok_or_else(|| bad(format!("unknown species {s:?}")))
}

/// Showdown's `moveThisTurnResult`: undefined (null), null ("none"), or
/// whether the move worked.
fn result_json(r: Option<Option<bool>>) -> Value {
    match r {
        None => Value::Null,
        Some(None) => json!("none"),
        Some(Some(b)) => json!(b),
    }
}

fn parse_result(v: &Value) -> Option<Option<bool>> {
    match v {
        Value::Bool(b) => Some(Some(*b)),
        Value::String(s) if s == "none" => Some(None),
        _ => None,
    }
}

fn flag(v: &Value, key: &str) -> bool {
    v.get(key).and_then(Value::as_bool).unwrap_or(false)
}

fn int(v: &Value, key: &str) -> Option<i64> {
    v.get(key).and_then(Value::as_i64)
}

fn bools2(v: &Value, key: &str) -> [bool; ACTIVE_PER_SIDE] {
    let mut out = [false; ACTIVE_PER_SIDE];
    if let Some(a) = v.get(key).and_then(Value::as_array) {
        for (i, x) in a.iter().take(ACTIVE_PER_SIDE).enumerate() {
            out[i] = x.as_bool().unwrap_or(false);
        }
    }
    out
}

fn volatile_json(v: &Volatile) -> Value {
    let dex = Dex::get();
    let mut o = json!({
        "id": v.id.id(),
        "duration": v.duration,
        "counter": v.counter,
        "move": v.move_id.map(|m| dex.move_data(m).id.clone()),
        "target_loc": v.target_loc,
        "order": v.effect_order,
    });
    if matches!(v.id, VolatileId::Charging(_)) {
        o["charging"] = json!(true);
    }
    o
}

fn parse_volatile(v: &Value, order: &mut u64) -> Res<Volatile> {
    let name = v
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| bad("volatile without an id"))?;
    let id = if flag(v, "charging") {
        VolatileId::Charging(move_id(name)?)
    } else {
        match name {
            "twoturnmove" => VolatileId::TwoTurnMove,
            "flashfire" => VolatileId::FlashFire,
            _ => VolatileId::parse(name).ok_or_else(|| bad(format!("unknown volatile {name:?}")))?,
        }
    };
    let duration = match v.get("duration") {
        Some(Value::Null) => None,
        Some(d) => Some(d.as_u64().ok_or_else(|| bad(format!("{name}: bad duration")))? as u8),
        None => id.duration(),
    };
    let effect_order = match int(v, "order") {
        Some(o) => o as u64,
        None => {
            *order += 1;
            *order
        }
    };
    Ok(Volatile {
        id,
        duration,
        counter: int(v, "counter").unwrap_or(0) as u32,
        move_id: opt_move(v.get("move").unwrap_or(&Value::Null))?,
        effect_order,
        target_loc: int(v, "target_loc").unwrap_or(0) as i8,
    })
}

fn set_json(set: &PokemonSet) -> Value {
    let dex = Dex::get();
    json!({
        "species": dex.species(set.species).id,
        "item": set.item.map(|i| dex.item(i).id.clone()),
        "ability": dex.ability(set.ability).id,
        "nature": dex.nature_names[set.nature.0 as usize],
        "points": set.points,
        "moves": set.moves.iter().map(|&m| dex.move_data(m).id.clone()).collect::<Vec<_>>(),
    })
}

/// A team: a list of sets as `set_json` writes them, or a Showdown paste.
fn parse_team(v: &Value) -> Res<Vec<PokemonSet>> {
    if let Some(text) = v.as_str() {
        return crate::team::parse_paste(text).map_err(bad);
    }
    let sets = v.as_array().ok_or_else(|| bad("a team is a list of sets or a paste"))?;
    let dex = Dex::get();
    sets.iter()
        .map(|s| {
            let species = species_id(s["species"].as_str().unwrap_or(""))?;
            let ability = match s["ability"].as_str() {
                Some(a) if !a.is_empty() => ability_id(a)?,
                _ => ability_id(dex.species(species).abilities.first().map_or("", |a| a.as_str()))?,
            };
            let nature = match s["nature"].as_str() {
                Some(n) if !n.is_empty() => dex.nature_id(n).ok_or_else(|| bad(format!("unknown nature {n:?}")))?,
                _ => dex.nature_id("Serious").expect("Serious"),
            };
            let mut set = PokemonSet {
                name: dex.species(species).name.clone(),
                species,
                item: opt_item(&s["item"])?,
                ability,
                nature,
                points: [0; 6],
                points_filled: false,
                moves: s["moves"]
                    .as_array()
                    .ok_or_else(|| bad("a set needs moves"))?
                    .iter()
                    .map(|m| move_id(m.as_str().unwrap_or("")))
                    .collect::<Res<_>>()?,
            };
            match s.get("points").and_then(Value::as_array) {
                Some(p) if p.len() == 6 => {
                    for (k, x) in p.iter().enumerate() {
                        set.points[k] = x.as_u64().unwrap_or(0) as u16;
                    }
                }
                _ => {
                    set.points = crate::team::default_points(&set);
                    set.points_filled = true;
                }
            }
            Ok(set)
        })
        .collect()
}

fn request_json(r: &SideRequest) -> Value {
    match r {
        SideRequest::TeamPreview => json!("teampreview"),
        SideRequest::Move(_) => json!("move"),
        SideRequest::Switch(force) => json!({ "switch": force }),
        SideRequest::Wait => json!("wait"),
    }
}

impl Battle {
    /// The position as JSON (see the module docs).
    pub fn position(&self) -> Value {
        let dex = Dex::get();
        let sides: Vec<Value> = (0..2)
            .map(|s| {
                let sd = &self.sides[s];
                let mut conditions = Map::new();
                let mut orders = Map::new();
                for c in SideCondition::ALL {
                    if sd.condition(c) > 0 {
                        conditions.insert(c.id().into(), json!(sd.condition(c)));
                        orders.insert(c.id().into(), json!(sd.condition_order[c as usize]));
                    }
                }
                json!({
                    "team": self.teams[s].iter().map(set_json).collect::<Vec<_>>(),
                    "pokemon": sd.pokemon.iter().map(mon_json).collect::<Vec<_>>(),
                    "mega_used": self.mega_used[s],
                    "pokemon_left": sd.pokemon_left,
                    "total_fainted": sd.total_fainted,
                    "fainted_this_turn": sd.fainted_this_turn,
                    "fainted_last_turn": sd.fainted_last_turn,
                    "revival_blessing": sd.revival_blessing,
                    "healing_wish": sd.healing_wish,
                    "slot_filled": sd.slot_filled,
                    "conditions": conditions,
                    "condition_order": orders,
                })
            })
            .collect();
        let _ = dex;
        json!({
            "turn": self.turn,
            "requests": self.requests.iter().map(request_json).collect::<Vec<_>>(),
            "residual": self.mid_turn && self.queue.iter().any(|a| matches!(a.kind, ActionKind::Residual)),
            "field": {
                "weather": weather_id(self.field.weather),
                "weather_turns": self.field.weather_turns,
                "terrain": terrain_id(self.field.terrain),
                "terrain_turns": self.field.terrain_turns,
                "trick_room": self.field.trick_room,
                "gravity": self.field.gravity,
            },
            "effect_order": self.effect_order,
            "sides": sides,
        })
    }

    /// Whether the rest of the turn is only its residual (or nothing), so
    /// `position` holds everything still to come.
    pub fn position_is_complete(&self) -> bool {
        self.queue.iter().all(|a| matches!(a.kind, ActionKind::Residual))
            && self.faint_queue.is_empty()
            && !self.is_over()
            && !self
                .requests
                .iter()
                .any(|r| matches!(r, SideRequest::TeamPreview))
    }

    /// A battle at `p` (see the module docs), drawing its random events
    /// from `chance`. Both teams must be supported (see `support`).
    pub fn from_position(p: &Value, chance: Chance) -> Res<Battle> {
        let sides = p
            .get("sides")
            .and_then(Value::as_array)
            .filter(|s| s.len() == 2)
            .ok_or_else(|| bad("needs two sides"))?;
        let teams = [parse_team(&sides[0]["team"])?, parse_team(&sides[1]["team"])?];
        for t in &teams {
            if t.is_empty() || t.len() > 6 {
                return Err(bad(format!("a team of {} Pokemon", t.len())));
            }
        }
        let mut b = Battle::new(teams, chance)?;
        b.queue.clear();
        b.turn = int(p, "turn").unwrap_or(1).max(0) as u32;
        let f = p.get("field").cloned().unwrap_or(json!({}));
        b.field.weather = parse_weather(f["weather"].as_str().unwrap_or(""))?;
        b.field.weather_turns = int(&f, "weather_turns").unwrap_or(0) as u8;
        b.field.terrain = parse_terrain(f["terrain"].as_str().unwrap_or(""))?;
        b.field.terrain_turns = int(&f, "terrain_turns").unwrap_or(0) as u8;
        b.field.trick_room = int(&f, "trick_room").unwrap_or(0) as u8;
        b.field.gravity = int(&f, "gravity").unwrap_or(0) as u8;
        let mut order = int(p, "effect_order").unwrap_or(0) as u64;
        for s in 0..2 {
            let sp = &sides[s];
            let entries = sp["pokemon"]
                .as_array()
                .ok_or_else(|| bad("a side needs its pokemon (the ones brought, in order)"))?;
            if entries.is_empty() || entries.len() > 6 {
                return Err(bad(format!("side {s} brought {} Pokemon", entries.len())));
            }
            let mut list = Vec::new();
            for (i, e) in entries.iter().enumerate() {
                let uid = int(e, "uid").ok_or_else(|| bad("a Pokemon needs its uid (team index)"))? as usize;
                if uid >= b.teams[s].len() || list.iter().any(|m: &Mon| m.uid == uid) {
                    return Err(bad(format!("side {s}: bad uid {uid}")));
                }
                let mut m = Mon::new(&b.teams[s][uid]);
                m.uid = uid;
                m.position = i;
                apply_mon(&mut m, e, &mut order)?;
                list.push(m);
            }
            let mega_used = flag(sp, "mega_used")
                || list.iter().any(|m| m.base_species != m.set.species && mega_forme(&m.set) == Some(m.base_species));
            b.mega_used[s] = mega_used;
            if mega_used {
                for m in list.iter_mut() {
                    m.can_mega_evo = None;
                }
            }
            let sd = &mut b.sides[s];
            sd.pokemon_left = int(sp, "pokemon_left")
                .map(|n| n as usize)
                .unwrap_or_else(|| list.iter().filter(|m| !m.fainted).count());
            sd.total_fainted = int(sp, "total_fainted")
                .map(|n| n as u8)
                .unwrap_or_else(|| list.iter().filter(|m| m.fainted).count() as u8);
            sd.fainted_this_turn = flag(sp, "fainted_this_turn");
            sd.fainted_last_turn = flag(sp, "fainted_last_turn");
            sd.revival_blessing = bools2(sp, "revival_blessing");
            sd.healing_wish = bools2(sp, "healing_wish");
            sd.slot_filled = match sp.get("slot_filled") {
                Some(_) => bools2(sp, "slot_filled"),
                None => [0, 1].map(|p| list.get(p).is_some_and(|m| m.is_active || m.fainted)),
            };
            if let Some(c) = sp.get("conditions").and_then(Value::as_object) {
                for (id, value) in c {
                    let cond = SideCondition::ALL
                        .into_iter()
                        .find(|x| x.id() == crate::dex::to_id(id))
                        .ok_or_else(|| bad(format!("unknown side condition {id:?}")))?;
                    let n = match value {
                        Value::Bool(true) => 1,
                        v => v.as_u64().unwrap_or(0) as u8,
                    };
                    sd.conditions[cond as usize] = n;
                    sd.condition_order[cond as usize] = match sp["condition_order"].get(id).and_then(Value::as_u64) {
                        Some(o) => o,
                        None => {
                            order += 1;
                            order
                        }
                    };
                }
            }
            sd.pokemon = list;
        }
        b.effect_order = order.max(
            b.sides
                .iter()
                .flat_map(|sd| sd.pokemon.iter().flat_map(|m| m.volatiles.0.iter().map(|v| v.effect_order)))
                .chain(b.sides.iter().flat_map(|sd| sd.condition_order))
                .max()
                .unwrap_or(0),
        );
        b.benched = [Vec::new(), Vec::new()];

        // The decision: a new turn's moves, or switches.
        let requests = p.get("requests").and_then(Value::as_array);
        let switch_of = |s: usize| -> Option<[bool; ACTIVE_PER_SIDE]> {
            let r = requests?.get(s)?.get("switch")?;
            Some(bools2(&json!({ "x": r }), "x"))
        };
        let switching = [switch_of(0), switch_of(1)];
        if switching.iter().any(Option::is_some) {
            b.mid_turn = true;
            for s in 0..2 {
                let force = switching[s].unwrap_or([false; ACTIVE_PER_SIDE]);
                for pos in 0..ACTIVE_PER_SIDE {
                    if !force[pos] || !b.sides[s].slot_filled[pos] {
                        continue;
                    }
                    let m = &mut b.sides[s].pokemon[pos];
                    if m.switch_flag.is_none() {
                        m.switch_flag = Some(match (m.fainted, m.last_move) {
                            (false, Some(mv)) => SwitchFlag::Move(mv),
                            _ => SwitchFlag::Replace,
                        });
                    }
                }
            }
            // The flags of the turn's move request still stand; without
            // them, work them out from the position.
            for s in 0..2 {
                for (pos, e) in sides[s]["pokemon"].as_array().into_iter().flatten().take(ACTIVE_PER_SIDE).enumerate() {
                    let known = e["moves"].as_array().is_some_and(|m| m.iter().any(|x| x.get("disabled").is_some()));
                    if !known && b.sides[s].slot_filled[pos] && !b.sides[s].pokemon[pos].fainted {
                        b.refresh_disabled(s, pos);
                    }
                }
            }
            if flag(p, "residual") {
                b.push_action(ActionKind::Residual);
            }
            b.make_switch_request();
            if b.requests.iter().all(|r| matches!(r, SideRequest::Wait)) {
                return Err(bad("a switch request with no slot to fill"));
            }
        } else {
            b.mid_turn = false;
            for s in 0..2 {
                for pos in 0..ACTIVE_PER_SIDE {
                    if b.sides[s].slot_filled[pos] {
                        b.refresh_disabled(s, pos);
                    }
                }
            }
            b.make_move_request();
            if b.requests.iter().all(|r| matches!(r, SideRequest::Wait)) {
                return Err(bad("no Pokemon able to move"));
            }
        }
        Ok(b)
    }
}

fn mon_json(m: &Mon) -> Value {
    let dex = Dex::get();
    let species_types = dex.species(m.species).types;
    let first = json!({
        "uid": m.uid,
        "species": dex.species(m.species).id,
        "base_species": dex.species(m.base_species).id,
        "types": if m.types != species_types { json!(m.types.map(type_name)) } else { Value::Null },
        "roost_types": m.roost_types.map(|t| t.map(type_name)),
        "hp": m.hp,
        "status": status_id(m.status),
        "status_time": m.status_state.time,
        "status_stage": m.status_state.stage,
        "boosts": m.boosts,
        "ability": dex.ability(m.ability).id,
        "base_ability": dex.ability(m.base_ability).id,
        "item": m.item.map(|i| dex.item(i).id.clone()),
        "last_item": m.last_item.map(|i| dex.item(i).id.clone()),
        "moves": m.moves.iter().map(slot_json).collect::<Vec<_>>(),
        "base_moves": m.base_moves.as_ref().map(|b| b.iter().map(slot_json).collect::<Vec<_>>()),
        "active": m.is_active,
        "fainted": m.fainted,
        "revealed": m.revealed,
        "volatiles": m.volatiles.0.iter().map(volatile_json).collect::<Vec<_>>(),
    });
    let more = json!({
        "last_move": m.last_move.map(|x| dex.move_data(x).id.clone()),
        "last_move_target_loc": m.last_move_target_loc,
        "move_this_turn": m.move_this_turn.map(|x| dex.move_data(x).id.clone()),
        "move_this_turn_result": result_json(m.move_this_turn_result),
        "move_last_turn_result": result_json(m.move_last_turn_result),
        "active_turns": m.active_turns,
        "active_move_actions": m.active_move_actions,
        "times_attacked": m.times_attacked,
        "attacked_by": m.attacked_by.iter().map(|a| json!({
            "side": a.source.side, "uid": a.source.uid, "damage": a.damage, "this_turn": a.this_turn,
        })).collect::<Vec<_>>(),
        "hurt_this_turn": m.hurt_this_turn,
        "newly_switched": m.newly_switched,
        "switch_flag": match m.switch_flag {
            None => Value::Null,
            Some(SwitchFlag::Replace) => json!("replace"),
            Some(SwitchFlag::Move(id)) => json!(dex.move_data(id).id),
        },
        "skip_before_switch_out": m.skip_before_switch_out,
        "being_called_back": m.being_called_back,
        "baton_passing": m.baton_passing,
        "shed_tailing": m.shed_tailing,
        "fallen": m.fallen,
        "syrup_triggered": m.syrup_triggered,
        "protean_used": m.protean_used,
        "disguise_busted": m.disguise_busted,
        "berserk_checked": m.berserk_checked,
        "trace_seek": m.trace_seek,
        "illusion": m.illusion,
        "transformed": m.transformed,
        "round_boost": m.round_boost,
        "stats_raised_this_turn": m.stats_raised_this_turn,
        "stats_lowered_this_turn": m.stats_lowered_this_turn,
        "can_mega": m.can_mega_evo.is_some(),
        "speed": m.speed,
    });
    let mut out = first;
    if let (Some(o), Value::Object(more)) = (out.as_object_mut(), more) {
        o.extend(more);
    }
    out
}

fn slot_json(s: &MoveSlot) -> Value {
    json!({
        "id": Dex::get().move_data(s.id).id,
        "pp": s.pp,
        "max_pp": s.max_pp,
        "used": s.used,
        "disabled": s.disabled,
        "imprisoned": s.imprisoned,
    })
}

fn parse_slots(v: &Value) -> Res<Vec<MoveSlot>> {
    let dex = Dex::get();
    v.as_array()
        .ok_or_else(|| bad("moves is a list"))?
        .iter()
        .map(|s| {
            let id = move_id(s["id"].as_str().unwrap_or(""))?;
            let max_pp = int(s, "max_pp").map_or_else(|| dex.move_data(id).max_pp(), |n| n as u8);
            Ok(MoveSlot {
                id,
                pp: int(s, "pp").map_or(max_pp, |n| n.clamp(0, max_pp as i64) as u8),
                max_pp,
                disabled: flag(s, "disabled"),
                imprisoned: flag(s, "imprisoned"),
                used: flag(s, "used"),
            })
        })
        .collect()
}

/// Apply a position's Pokemon entry to `m`, fresh from its set.
fn apply_mon(m: &mut Mon, e: &Value, order: &mut u64) -> Res<()> {
    let dex = Dex::get();
    if let Some(s) = e.get("base_species").and_then(Value::as_str) {
        let id = species_id(s)?;
        if id != m.species {
            m.set_species(id);
            m.base_species = id;
        }
    }
    if let Some(s) = e.get("species").and_then(Value::as_str) {
        let id = species_id(s)?;
        if id != m.species {
            m.set_species(id);
        }
    }
    // A Mega's ability is its own from then on.
    if m.base_species != m.set.species && mega_forme(&m.set) == Some(m.base_species) {
        let a = ability_id(&dex.species(m.base_species).abilities[0])?;
        m.ability = a;
        m.base_ability = a;
        m.can_mega_evo = None;
    }
    if let Some(t) = e.get("types").and_then(Value::as_array) {
        if t.len() == 2 {
            m.types = [parse_type(t[0].as_str().unwrap_or(""))?, parse_type(t[1].as_str().unwrap_or(""))?];
        }
    }
    if let Some(t) = e.get("roost_types").and_then(Value::as_array) {
        if t.len() == 2 {
            m.roost_types = Some([parse_type(t[0].as_str().unwrap_or(""))?, parse_type(t[1].as_str().unwrap_or(""))?]);
        }
    }
    if let Some(hp) = int(e, "hp") {
        m.hp = hp.clamp(0, m.max_hp() as i64) as u16;
    }
    if let Some(s) = e.get("status").and_then(Value::as_str) {
        m.status = parse_status(s)?;
    }
    m.status_state = StatusState {
        time: int(e, "status_time").unwrap_or(0) as i8,
        stage: int(e, "status_stage").unwrap_or(0) as u8,
    };
    if let Some(b) = e.get("boosts").and_then(Value::as_array) {
        for (k, x) in b.iter().take(7).enumerate() {
            m.boosts[k] = x.as_i64().unwrap_or(0).clamp(-6, 6) as i8;
        }
    }
    if let Some(a) = e.get("base_ability").and_then(Value::as_str) {
        m.base_ability = ability_id(a)?;
        m.ability = m.base_ability;
    }
    if let Some(a) = e.get("ability").and_then(Value::as_str) {
        m.ability = ability_id(a)?;
    }
    if let Some(i) = e.get("item") {
        m.item = opt_item(i)?;
    }
    if let Some(i) = e.get("last_item") {
        m.last_item = opt_item(i)?;
    }
    if let Some(v) = e.get("moves") {
        m.moves = parse_slots(v)?;
        if m.moves.is_empty() {
            return Err(bad("a Pokemon with no moves"));
        }
    }
    if let Some(v) = e.get("base_moves").filter(|v| !v.is_null()) {
        m.base_moves = Some(parse_slots(v)?);
    }
    m.is_active = flag(e, "active");
    m.fainted = flag(e, "fainted") || e.get("status").and_then(Value::as_str) == Some("fnt");
    if m.fainted {
        m.faint_queued = true;
        m.hp = 0;
        m.is_active = false;
        m.status = Status::None;
    }
    m.revealed = e.get("revealed").and_then(Value::as_bool).unwrap_or(m.is_active || m.fainted);
    if let Some(v) = e.get("volatiles").and_then(Value::as_array) {
        let mut vols: Vec<Volatile> = v.iter().map(|x| parse_volatile(x, order)).collect::<Res<_>>()?;
        vols.sort_by_key(|x| x.effect_order);
        m.volatiles.0 = vols;
    }
    m.last_move = opt_move(e.get("last_move").unwrap_or(&Value::Null))?;
    m.last_move_target_loc = int(e, "last_move_target_loc").unwrap_or(0) as i8;
    m.move_this_turn = opt_move(e.get("move_this_turn").unwrap_or(&Value::Null))?;
    m.move_this_turn_result = parse_result(e.get("move_this_turn_result").unwrap_or(&Value::Null));
    m.move_last_turn_result = parse_result(e.get("move_last_turn_result").unwrap_or(&Value::Null));
    m.active_turns = int(e, "active_turns").unwrap_or(0) as u16;
    m.active_move_actions = int(e, "active_move_actions").unwrap_or(0) as u16;
    m.times_attacked = int(e, "times_attacked").unwrap_or(0) as u8;
    if let Some(a) = e.get("attacked_by").and_then(Value::as_array) {
        m.attacked_by = a
            .iter()
            .map(|x| Attacker {
                source: MonRef {
                    side: int(x, "side").unwrap_or(0) as usize,
                    uid: int(x, "uid").unwrap_or(0) as usize,
                },
                damage: int(x, "damage").unwrap_or(0) as u32,
                this_turn: flag(x, "this_turn"),
            })
            .collect();
    }
    m.hurt_this_turn = int(e, "hurt_this_turn").map(|n| n as u16);
    m.newly_switched = flag(e, "newly_switched");
    m.switch_flag = match e.get("switch_flag").and_then(Value::as_str) {
        None => None,
        Some("replace") => Some(SwitchFlag::Replace),
        Some(mv) => Some(SwitchFlag::Move(move_id(mv)?)),
    };
    m.skip_before_switch_out = flag(e, "skip_before_switch_out");
    m.being_called_back = flag(e, "being_called_back");
    m.baton_passing = flag(e, "baton_passing");
    m.shed_tailing = flag(e, "shed_tailing");
    m.fallen = int(e, "fallen").unwrap_or(0) as u8;
    m.syrup_triggered = flag(e, "syrup_triggered");
    m.protean_used = flag(e, "protean_used");
    m.disguise_busted = flag(e, "disguise_busted");
    m.berserk_checked = e.get("berserk_checked").and_then(Value::as_bool).unwrap_or(true);
    m.trace_seek = flag(e, "trace_seek");
    m.illusion = flag(e, "illusion");
    m.transformed = flag(e, "transformed");
    m.round_boost = flag(e, "round_boost");
    m.stats_raised_this_turn = flag(e, "stats_raised_this_turn");
    m.stats_lowered_this_turn = flag(e, "stats_lowered_this_turn");
    // Showdown's `pokemon.speed`, as of the last updateSpeed (a benched
    // Pokemon keeps the last one it had).
    if let Some(v) = int(e, "speed") {
        m.speed = v as i32;
    }
    if e.get("can_mega").and_then(Value::as_bool) == Some(false) {
        m.can_mega_evo = None;
    }
    Ok(())
}
