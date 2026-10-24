//! The damage cache gives exactly the uncached damage tables, including for
//! calculations that depend on HP (Eruption, Multiscale, pinch abilities...).

use engine::battle::choice::SideRequest;
use engine::battle::Battle;
use engine::chance::{Chance, Rng};
use engine::corpus::load_sources;
use engine::damage::DamageCache;
use engine::dex::Dex;
use engine::enumerate::{enumerate, EnumConfig};
use std::path::Path;

/// Positions from random play between the corpus teams, favouring teams
/// with HP-dependent damage.
fn positions(n: usize, seed: u64) -> Vec<Battle> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
    let (teams, _) = load_sources(&[root.join("data/corpus").display().to_string()]).unwrap();
    let hp_moves = ["eruption", "waterspout", "superfang", "finalgambit", "hardpress"];
    let hp_abilities = ["blaze", "torrent", "overgrow", "swarm", "multiscale"];
    let favoured: Vec<_> = teams
        .iter()
        .filter(|t| {
            t.sets.iter().any(|s| {
                let d = Dex::get();
                s.moves.iter().any(|&m| hp_moves.contains(&d.move_data(m).id.as_str()))
                    || hp_abilities.contains(&d.ability(s.ability).id.as_str())
            })
        })
        .collect();
    assert!(!favoured.is_empty());
    let mut rng = Rng::new(seed);
    let mut out = Vec::new();
    let mut game = 0;
    while out.len() < n {
        game += 1;
        let a = favoured[rng.below(favoured.len() as u32) as usize].sets.clone();
        let b = teams[rng.below(teams.len() as u32) as usize].sets.clone();
        let mut battle = Battle::new([a, b], Chance::seeded(game)).unwrap();
        battle.turn_limit = Some(30);
        while !battle.is_over() && out.len() < n {
            if (0..2).all(|s| matches!(battle.requests[s], SideRequest::Move(_))) {
                out.push(battle.clone());
            }
            let choices = [0, 1].map(|s| {
                (!matches!(battle.requests[s], SideRequest::Wait)).then(|| {
                    let o = battle.legal_choices(s);
                    o[rng.below(o.len() as u32) as usize].clone()
                })
            });
            battle.choose(choices).unwrap();
        }
    }
    out
}

#[test]
fn cached_tables_match_uncached() {
    let mut rng = Rng::new(17);
    let mut cache = DamageCache::new();
    let mut checked = 0;
    for b in positions(300, 4) {
        // The position at many HP values, through one cache.
        for _ in 0..6 {
            let mut x = b.clone();
            for side in 0..2 {
                for m in x.sides[side].pokemon.iter_mut().filter(|m| m.is_active && m.hp > 0) {
                    m.hp = 1 + rng.below(m.max_hp() as u32) as u16;
                }
            }
            assert_eq!(x.damage_table_with(Some(&mut cache)), x.damage_table());
            checked += 1;
        }
        // And the chance outcomes of a turn from it.
        let choices = [0, 1].map(|s| {
            let o = b.legal_choices(s);
            Some(o[rng.below(o.len() as u32) as usize].clone())
        });
        for o in enumerate(&b, &choices, &EnumConfig::default()).unwrap().outcomes {
            assert_eq!(o.battle.damage_table_with(Some(&mut cache)), o.battle.damage_table());
            checked += 1;
        }
    }
    assert!(cache.hits > cache.misses, "{} hits, {} misses", cache.hits, cache.misses);
    assert!(cache.hp_dependent > 50, "only {} HP-dependent calculations", cache.hp_dependent);
    assert!(checked > 2000);
}
