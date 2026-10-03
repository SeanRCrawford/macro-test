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
            "pseudoWeather": if self.field.trick_room > 0 { json!({"trickroom": self.field.trick_room}) } else { json!({}) },
            "sides": sides,
        })
    }
}

fn sorted_volatiles(m: &super::state::Mon) -> Vec<&'static str> {
    let mut v: Vec<&'static str> = m.volatiles.0.iter().map(|v| v.id.id()).collect();
    v.sort();
    v
}
