//! What the battle engine implements so far. A team that brings anything
//! else is refused when the battle is created, rather than playing with the
//! effect silently missing. Each list grows as mechanics are added (and
//! checked against Showdown).

use crate::dex::{Category, Dex, HitEffect, MoveData, MoveTarget};
use crate::team::{mega_forme, PokemonSet};

/// Abilities whose every effect is implemented: damage modifiers (handled by
/// the damage module, which mirrors Showdown exactly) and Levitate's immunity.
const ABILITIES: &[&str] = &[
    "noability", "adaptability", "aerilate", "auraguard", "battlearmor", "blaze", "dragonize", "filter",
    "firemane", "fluffy", "friendguard", "furcoat", "grasspelt", "guts", "hugepower", "ironfist", "levitate",
    "lightmetal", "liquidvoice", "marvelscale", "megalauncher", "minus", "multiscale", "overgrow", "pixilate",
    "plus", "punkrock", "purepower", "reckless", "refrigerate", "sharpness", "shellarmor", "sniper", "solidrock",
    "stakeout", "steelyspirit", "strongjaw", "swarm", "technician", "thickfat", "torrent", "toughclaws",
    "unaware",
];

/// Items whose every effect is implemented: damage boosts, Mega Stones and
/// the items in `items`.
const ITEMS: &[&str] = &[
    "blackbelt", "blackglasses", "charcoal", "choicescarf", "focussash", "leftovers", "lifeorb", "sitrusberry", "dragonfang", "expertbelt", "fairyfeather", "hardstone",
    "lightball", "magnet", "metalcoat", "miracleseed", "muscleband", "mysticwater", "nevermeltice",
    "sharpbeak", "silkscarf", "silverpowder", "softsand", "spelltag", "twistedspoon", "wiseglasses",
];

/// Move data keys that add nothing beyond what `moves` implements.
const PLAIN_KEYS: &[&str] = &[
    "accuracy", "basePower", "boosts", "category", "critRatio", "drain", "flags", "handlers", "heal",
    "ignoreDefensive", "ignoreImmunity", "isNonstandard", "name", "noPPBoosts", "num", "overrideDefensiveStat",
    "overrideOffensivePokemon", "overrideOffensiveStat", "pp", "priority", "recoil", "secondary", "secondaries",
    "self", "stallingMove", "status", "target", "thawsTarget", "type", "volatileStatus", "willCrit",
];

/// Move handlers the damage module covers (it reports any specific move it
/// doesn't implement as unsupported when the move is used).
const DAMAGE_HANDLERS: &[&str] = &["basePowerCallback", "onBasePower", "onModifyType", "onModifyMove", "onEffectiveness"];

/// Moves whose own handlers `moves` implements, and which.
const MOVE_HANDLERS: &[(&str, &[&str])] = &[
    ("protect", &["onPrepareHit", "onHit"]),
    ("detect", &["onPrepareHit", "onHit"]),
    ("fakeout", &["onTry", "onDisableMove"]),
];

/// Volatiles a move may add (Protect's own condition is the protect volatile).
const VOLATILES: &[&str] = &["flinch", "protect"];

pub fn ability_supported(id: &str) -> bool {
    ABILITIES.contains(&id)
}

pub fn item_supported(id: &str) -> bool {
    ITEMS.contains(&id) || Dex::get().item_id(id).is_some_and(|i| !Dex::get().item(i).mega_stone.is_empty())
}

/// A HitEffect (the move's, a secondary or a `self` part) whose every part is
/// implemented.
fn effect_supported(e: &HitEffect, allowed: &[&str]) -> bool {
    e.keys.iter().all(|k| allowed.contains(&k.as_str()) || ["boosts", "chance", "self", "status", "volatileStatus"].contains(&k.as_str()))
        && e.volatile_status.as_deref().is_none_or(|v| VOLATILES.contains(&v))
        && e.self_effect.as_deref().is_none_or(|s| effect_supported(s, &[]))
}

pub fn move_supported(m: &MoveData) -> bool {
    let special = MOVE_HANDLERS.iter().find(|(id, _)| *id == m.id).map(|(_, h)| *h);
    let handlers_ok = match special {
        Some(allowed) => m.handlers.names.iter().all(|h| allowed.contains(&h.as_str())),
        None => {
            m.handlers.names.iter().all(|h| DAMAGE_HANDLERS.contains(&h.as_str()))
                && (m.handlers.names.is_empty() || crate::damage::MOVES_WITH_HANDLERS.contains(&m.id.as_str()))
        }
    };
    // Protect's `condition` is the protect volatile, implemented in `moves`.
    let condition_ok = !m.has_key("condition") || m.primary.volatile_status.as_deref() == Some("protect");
    let target_ok = match m.category {
        Category::Status => matches!(
            m.target,
            MoveTarget::SelfTarget | MoveTarget::Normal | MoveTarget::Any | MoveTarget::AdjacentFoe | MoveTarget::AllAdjacentFoes | MoveTarget::AdjacentAlly
        ),
        _ => matches!(
            m.target,
            MoveTarget::Normal | MoveTarget::Any | MoveTarget::AdjacentFoe | MoveTarget::AllAdjacentFoes | MoveTarget::AllAdjacent | MoveTarget::RandomNormal
        ),
    };
    m.keys.iter().all(|k| PLAIN_KEYS.contains(&k.as_str()) || (k == "condition" && condition_ok))
        && effect_supported(&m.primary, &[PLAIN_KEYS, &["condition"]].concat())
        && m.secondaries.iter().all(|s| effect_supported(s, &[]))
        && m.nested_handlers.is_empty()
        && handlers_ok
        && target_ok
}

/// Every reason this set can't be played yet; empty means it can.
pub fn unsupported(set: &PokemonSet) -> Vec<String> {
    let dex = Dex::get();
    let mut out = Vec::new();
    let ability = &dex.ability(set.ability).id;
    if !ability_supported(ability) {
        out.push(format!("ability {ability}"));
    }
    if let Some(it) = set.item {
        let item = &dex.item(it).id;
        if !item_supported(item) {
            out.push(format!("item {item}"));
        }
    }
    if let Some(mega) = mega_forme(set) {
        let ab = crate::dex::to_id(&dex.species(mega).abilities[0]);
        if !ability_supported(&ab) {
            out.push(format!("Mega ability {ab}"));
        }
    }
    for &m in &set.moves {
        let data = dex.move_data(m);
        if !move_supported(data) {
            out.push(format!("move {}", data.id));
        }
    }
    out
}

pub fn check_set(set: &PokemonSet) -> Result<(), String> {
    let problems = unsupported(set);
    if problems.is_empty() {
        Ok(())
    } else {
        Err(format!("{}: {}", Dex::get().species(set.species).name, problems.join(", ")))
    }
}

/// Supported ids, as written to data/support.json for the fixture generator.
pub fn lists() -> (Vec<String>, Vec<String>, Vec<String>) {
    let dex = Dex::get();
    let moves = dex.moves.iter().filter(|m| move_supported(m)).map(|m| m.id.clone()).collect();
    let abilities = ABILITIES.iter().map(|s| s.to_string()).collect();
    let items = dex.items.iter().filter(|i| item_supported(&i.id)).map(|i| i.id.clone()).collect();
    (moves, abilities, items)
}
