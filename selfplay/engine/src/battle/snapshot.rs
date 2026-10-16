//! A JSON view of the battle for comparison with Showdown's state.
//! tools/showdown/gen_fixtures.js builds the same shape from a Showdown
//! Battle object.

use super::choice::SideRequest;
use super::{Battle, Outcome};
use crate::damage::{Status, Terrain, Weather};
use crate::dex::Dex;
use serde_json::{json, Value};

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

fn weather_id(w: Weather) -> &'static str {
    match w {
        Weather::None => "",
        Weather::Sun => "sunnyday",
        Weather::Rain => "raindance",
        Weather::Sand => "sandstorm",
        Weather::Snow => "snowscape",
    }
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

impl Battle {
    pub fn snapshot(&self) -> Value {
        let dex = Dex::get();
        let requests: Vec<Value> = self
            .requests
            .iter()
            .map(|r| match r {
                SideRequest::TeamPreview => json!("teampreview"),
                SideRequest::Move(_) => json!("move"),
                SideRequest::Switch(force) => json!({"switch": force}),
                SideRequest::Wait => json!("wait"),
            })
            .collect();
        let sides: Vec<Value> = self
            .sides
            .iter()
            .map(|side| {
                let pokemon: Vec<Value> = side
                    .pokemon
                    .iter()
                    .map(|m| {
                        json!({
                            "species": dex.species(m.species).id,
                            "hp": m.hp,
                            "maxhp": m.max_hp(),
                            "status": if m.fainted { "fnt" } else { status_id(m.status) },
                            "active": m.is_active,
                            "boosts": m.boosts,
                            "item": m.item.map(|i| dex.item(i).id.clone()).unwrap_or_default(),
                            "ability": dex.ability(m.ability).id,
                            "pp": m.moves.iter().map(|s| s.pp).collect::<Vec<_>>(),
                            "volatiles": sorted_volatiles(m),
                        })
                    })
                    .collect();
                let mut conditions = serde_json::Map::new();
                for c in super::state::SideCondition::ALL {
                    if side.condition(c) > 0 {
                        // Hazards: layers (or true for the single-layer ones).
                        let value = match c {
                            super::state::SideCondition::Spikes | super::state::SideCondition::ToxicSpikes => json!(side.condition(c)),
                            _ if c.is_hazard() => json!(true),
                            _ => json!(side.condition(c)),
                        };
                        conditions.insert(c.id().into(), value);
                    }
                }
                json!({"totalFainted": side.total_fainted, "sideConditions": conditions, "pokemon": pokemon})
            })
            .collect();
        let mut pseudo = serde_json::Map::new();
        if self.field.trick_room > 0 {
            pseudo.insert("trickroom".into(), json!(self.field.trick_room));
        }
        if self.field.gravity > 0 {
            pseudo.insert("gravity".into(), json!(self.field.gravity));
        }
        json!({
            "turn": self.turn,
            "requests": requests,
            "outcome": match self.outcome {
                None => Value::Null,
                Some(Outcome::Win(s)) => json!(s),
                Some(Outcome::Tie) => json!("tie"),
            },
            "weather": weather_id(self.field.weather),
            "terrain": terrain_id(self.field.terrain),
            "weatherTurns": if self.field.weather == Weather::None { Value::Null } else { json!(self.field.weather_turns) },
            "terrainTurns": if self.field.terrain == Terrain::None { Value::Null } else { json!(self.field.terrain_turns) },
            "pseudoWeather": pseudo,
            "sides": sides,
        })
    }
}

fn sorted_volatiles(m: &super::state::Mon) -> Vec<&'static str> {
    let mut v: Vec<&'static str> = m.volatiles.0.iter().map(|v| v.id.id()).collect();
    v.sort();
    v
}

impl Battle {
    /// The battle as `side` sees it, for people (the play-against-the-bot
    /// screen): both teams in team order with display names, moves and
    /// state. The observer's Pokemon show HP in points; the opponent's as a
    /// percentage, and only those it brought that have appeared show
    /// anything beyond their team sheet.
    pub fn view(&self, side: usize) -> Value {
        let dex = Dex::get();
        let sides: Vec<Value> = [side, 1 - side]
            .iter()
            .map(|&s| {
                let own = s == side;
                let sd = &self.sides[s];
                let pokemon: Vec<Value> = self.teams[s]
                    .iter()
                    .enumerate()
                    .map(|(uid, set)| {
                        let m = sd.pokemon.iter().find(|m| m.uid == uid);
                        let seen = m.filter(|m| own || m.revealed);
                        let moves: Vec<Value> = match seen {
                            Some(m) => m
                                .moves
                                .iter()
                                .map(|ms| {
                                    json!({
                                        "name": dex.move_data(ms.id).name,
                                        "pp": ms.pp,
                                        "maxpp": ms.max_pp,
                                        "disabled": own && (ms.disabled || ms.imprisoned),
                                    })
                                })
                                .collect(),
                            None => set
                                .moves
                                .iter()
                                .map(|&id| json!({"name": dex.move_data(id).name}))
                                .collect(),
                        };
                        let mut v = json!({
                            "species": dex.species(seen.map_or(set.species, |m| m.species)).name,
                            "item": seen
                                .map_or(set.item, |m| m.item)
                                .map(|i| dex.item(i).name.clone()),
                            "ability": dex.ability(seen.map_or(set.ability, |m| m.ability)).name,
                            "moves": moves,
                            "brought": own && m.is_some(),
                            "seen": seen.is_some(),
                        });
                        if let Some(m) = seen {
                            let o = v.as_object_mut().expect("object");
                            if own {
                                o.insert("hp".into(), json!(m.hp));
                                o.insert("maxhp".into(), json!(m.max_hp()));
                            }
                            o.insert(
                                "hp_pct".into(),
                                json!((100.0 * m.hp as f64 / m.max_hp().max(1) as f64).ceil()),
                            );
                            o.insert("status".into(), json!(if m.fainted { "fnt" } else { status_id(m.status) }));
                            o.insert("boosts".into(), json!(m.boosts));
                            o.insert(
                                "slot".into(),
                                if m.is_active && m.position < 2 { json!(m.position) } else { Value::Null },
                            );
                            o.insert("position".into(), json!(m.position));
                            o.insert("volatiles".into(), json!(sorted_volatiles(m)));
                        }
                        v
                    })
                    .collect();
                let mut conditions = serde_json::Map::new();
                for c in super::state::SideCondition::ALL {
                    if sd.condition(c) > 0 {
                        conditions.insert(c.id().into(), json!(sd.condition(c)));
                    }
                }
                json!({"pokemon": pokemon, "conditions": conditions, "fainted": sd.total_fainted,
                       "mega_used": self.mega_used(s)})
            })
            .collect();
        json!({
            "turn": self.turn,
            "outcome": match self.outcome {
                None => Value::Null,
                Some(Outcome::Win(s)) => json!(if s == side { "win" } else { "loss" }),
                Some(Outcome::Tie) => json!("tie"),
            },
            "weather": weather_id(self.field.weather),
            "weather_turns": self.field.weather_turns,
            "terrain": terrain_id(self.field.terrain),
            "terrain_turns": self.field.terrain_turns,
            "trick_room": self.field.trick_room,
            "gravity": self.field.gravity,
            "you": sides[0],
            "foe": sides[1],
        })
    }
}
