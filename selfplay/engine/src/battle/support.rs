//! What the battle engine implements so far. A team that brings anything
//! else is refused when the battle is created, rather than playing with the
//! effect silently missing. Each list grows as mechanics are added (and
//! checked against Showdown).

use crate::dex::{Category, Dex, HitEffect, MoveData, MoveTarget};
use crate::team::{mega_forme, PokemonSet};

/// Abilities whose every effect is implemented: damage modifiers (handled by
/// the damage module, which mirrors Showdown exactly) and Levitate's immunity.
const ABILITIES: &[&str] = &[
    "noability", "adaptability", "aerilate", "auraguard", "battlearmor", "blaze", "dragonize", "drizzle", "drought",
    "electricsurge", "filter", "grassysurge", "intimidate", "psychicsurge", "sandstream", "snowwarning",
    "armortail", "competitive", "defiant", "goodasgold", "prankster", "unburden",
    "chlorophyll", "clearbody", "compoundeyes", "contrary", "fairyaura", "flamebody", "flashfire", "galewings",
    "hospitality", "infiltrator", "innerfocus", "magicbounce", "mirrorarmor", "noguard", "poisontouch", "regenerator",
    "rockhead", "roughskin", "sandrush", "scrappy", "shadowtag", "slushrush", "soundproof", "speedboost",
    "spicyspray", "stamina", "sturdy", "swiftswim", "thermalexchange", "trace", "unnerve", "weakarmor",
    "firemane", "fluffy", "friendguard", "furcoat", "grasspelt", "guts", "hugepower", "ironfist", "levitate",
    "lightmetal", "liquidvoice", "marvelscale", "megalauncher", "minus", "multiscale", "overgrow", "pixilate",
    "plus", "punkrock", "purepower", "reckless", "refrigerate", "sharpness", "shellarmor", "sniper", "solidrock",
    "stakeout", "steelyspirit", "strongjaw", "swarm", "technician", "thickfat", "torrent", "toughclaws",
    "unaware",
];

/// Items whose every effect is implemented: damage boosts, Mega Stones and
/// the items in `items`.
const ITEMS: &[&str] = &[
    "blackbelt", "blackglasses", "charcoal", "choicescarf", "focussash", "leftovers", "lifeorb", "lightclay", "sitrusberry",
    "damprock", "heatrock", "icyrock", "smoothrock", "terrainextender",
    "electricseed", "grassyseed", "mistyseed", "psychicseed", "rockyhelmet", "whiteherb",
    "babiriberry", "chartiberry", "chopleberry", "cobaberry", "colburberry", "habanberry", "kasibberry", "kebiaberry",
    "occaberry", "passhoberry", "payapaberry", "rindoberry", "roseliberry", "shucaberry", "tangaberry", "wacanberry",
    "yacheberry", "dragonfang", "expertbelt", "fairyfeather", "hardstone",
    "lightball", "magnet", "metalcoat", "miracleseed", "muscleband", "mysticwater", "nevermeltice",
    "sharpbeak", "silkscarf", "silverpowder", "softsand", "spelltag", "twistedspoon", "wiseglasses",
];

/// Move data keys that add nothing beyond what `moves` implements.
const PLAIN_KEYS: &[&str] = &[
    "accuracy", "basePower", "boosts", "category", "critRatio", "drain", "flags", "handlers", "hasSheerForceBoost", "heal",
    "ignoreDefensive", "ignoreEvasion", "ignoreImmunity", "multihit", "isNonstandard", "name", "noPPBoosts", "num", "overrideDefensiveStat",
    "overrideOffensivePokemon", "overrideOffensiveStat", "pp", "priority", "recoil", "secondary", "secondaries",
    "self", "selfBoost", "selfSwitch", "stallingMove", "status", "target", "thawsTarget", "type", "volatileStatus", "willCrit",
];

/// Move handlers the damage module covers (it reports any specific move it
/// doesn't implement as unsupported when the move is used).
const DAMAGE_HANDLERS: &[&str] = &["basePowerCallback", "onBasePower", "onModifyType", "onModifyMove", "onEffectiveness"];

/// Moves whose own handlers `moves` implements, and which.
const MOVE_HANDLERS: &[(&str, &[&str])] = &[
    ("protect", &["onPrepareHit", "onHit"]),
    ("detect", &["onPrepareHit", "onHit"]),
    ("fakeout", &["onTry", "onDisableMove"]),
    ("followme", &["onTry"]),
    ("ragepowder", &["onTry"]),
    ("helpinghand", &["onTryHit"]),
    ("partingshot", &["onHit"]),
    ("suckerpunch", &["onTry"]),
    ("grassyglide", &["onModifyPriority"]),
    ("wideguard", &["onTry", "onHitSide"]),
    ("lowkick", &["basePowerCallback", "onTryHit"]),
    ("grassknot", &["basePowerCallback", "onTryHit"]),
    ("direclaw", &[]),
    ("throatchop", &[]),
    ("encore", &[]),
    ("stompingtantrum", &["basePowerCallback"]),
    ("knockoff", &["onBasePower", "onAfterHit"]),
    ("firstimpression", &["onTry", "onDisableMove"]),
    ("psychicfangs", &["onTryHit"]),
    ("brickbreak", &["onTryHit"]),
    ("clangoroussoul", &["onTry", "onTryHit", "onHit"]),
    ("steelroller", &["onTry", "onHit", "onAfterSubDamage"]),
    ("trick", &["onTryImmunity", "onHit"]),
    ("finalgambit", &["damageCallback"]),
    ("auroraveil", &["onTry"]),
    ("glaiverush", &[]),
    ("hurricane", &["onModifyMove"]),
    ("perishsong", &["onHitField"]),
    ("spikyshield", &["onPrepareHit", "onHit"]),
    ("kingsshield", &["onPrepareHit", "onHit"]),
    ("banefulbunker", &["onPrepareHit", "onHit"]),
    ("yawn", &["onTryHit"]),
    ("disable", &["onTryHit"]),
    ("electroshot", &["onTryMove"]),
    ("meteorbeam", &["onTryMove"]),
    ("solarbeam", &["onTryMove", "onBasePower"]),
    ("solarblade", &["onTryMove", "onBasePower"]),
];

/// Status moves that set a side or field condition, which `moves` implements.
const FIELD_MOVES: &[&str] = &[
    "tailwind", "trickroom", "reflect", "lightscreen", "wideguard", "auroraveil", "raindance", "sunnyday", "sandstorm",
    "snowscape", "electricterrain", "grassyterrain", "mistyterrain", "psychicterrain", "perishsong",
];

/// Moves whose secondary has an onHit that `moves` implements.
const SECONDARY_ON_HIT: &[&str] = &["direclaw", "throatchop"];

/// Volatiles a move may add (Protect's own condition is the protect volatile).
const VOLATILES: &[&str] = &[
    "flinch", "protect", "followme", "ragepowder", "helpinghand", "encore", "glaiverush", "confusion", "yawn", "taunt",
    "disable", "roost", "spikyshield", "kingsshield", "banefulbunker", "imprison", "mustrecharge",
];

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
    let field_move = FIELD_MOVES.contains(&m.id.as_str());
    // A move's `condition` is the volatile it adds (protect, followme...).
    let condition_ok = !m.has_key("condition")
        || m.primary.volatile_status.as_deref().is_some_and(|v| VOLATILES.contains(&v))
        || m.primary.self_effect.as_ref().and_then(|e| e.volatile_status.as_deref()).is_some_and(|v| VOLATILES.contains(&v))
        || field_move
        || matches!(m.id.as_str(), "throatchop" | "glaiverush");
    // Final Gambit is the only selfdestruct move supported.
    let selfdestruct_ok = !m.has_key("selfdestruct") || m.id == "finalgambit";
    let target_ok = match m.category {
        Category::Status => matches!(
            m.target,
            MoveTarget::SelfTarget
                | MoveTarget::Normal
                | MoveTarget::Any
                | MoveTarget::AdjacentFoe
                | MoveTarget::AllAdjacentFoes
                | MoveTarget::AdjacentAlly
                | MoveTarget::Allies
        ),
        _ => matches!(
            m.target,
            MoveTarget::Normal | MoveTarget::Any | MoveTarget::AdjacentFoe | MoveTarget::AllAdjacentFoes | MoveTarget::AllAdjacent | MoveTarget::RandomNormal
        ),
    };
    let target_ok = target_ok || (field_move && matches!(m.target, MoveTarget::All | MoveTarget::AllySide));
    let field_key = |k: &str| field_move && matches!(k, "sideCondition" | "pseudoWeather" | "weather" | "terrain");
    let switch_ok = !m.has_key("selfSwitch") || m.self_switch;
    switch_ok
        && selfdestruct_ok
        && m.keys.iter().all(|k| {
            PLAIN_KEYS.contains(&k.as_str()) || (k == "condition" && condition_ok) || field_key(k) || (k == "selfdestruct" && selfdestruct_ok)
        })
        && effect_supported(&m.primary, &[PLAIN_KEYS, &["condition", "sideCondition", "pseudoWeather", "selfdestruct", "weather", "terrain"]].concat())
        && m.self_boost.as_ref().is_none_or(|e| effect_supported(e, &[]))
        && m.secondaries.iter().all(|s| effect_supported(s, &[]))
        && (m.nested_handlers.is_empty() || (SECONDARY_ON_HIT.contains(&m.id.as_str()) && m.nested_handlers == ["secondary.onHit"]))
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
