//! Random legal play between random supported teams: every game must end,
#![allow(clippy::needless_range_loop)]
//! every request must offer a legal choice, and the state must stay sane.

use engine::battle::choice::SideRequest;
use engine::battle::{support, Battle};
use engine::chance::{Chance, Rng};
use engine::dex::{Dex, SpeciesId};
use engine::team::PokemonSet;

fn random_team(rng: &mut Rng) -> Vec<PokemonSet> {
    let dex = Dex::get();
    let (moves, abilities, items) = support::lists();
    let legal: Vec<SpeciesId> = (0..dex.species.len() as u16)
        .map(SpeciesId)
        .filter(|&s| {
            let sp = dex.species(s);
            sp.nonstandard.is_none() && sp.battle_only.is_none() && !sp.learnset.is_empty()
        })
        .collect();
    let mut team = Vec::new();
    let mut nums = Vec::new();
    while team.len() < 6 {
        let s = legal[rng.below(legal.len() as u32) as usize];
        let sp = dex.species(s);
        if nums.contains(&sp.num) {
            continue;
        }
        let usable: Vec<_> = sp.learnset.iter().copied().filter(|m| moves.contains(&dex.move_data(*m).id)).collect();
        if usable.is_empty() {
            continue;
        }
        nums.push(sp.num);
        let ability = sp
            .abilities
            .iter()
            .map(|a| engine::dex::to_id(a))
            .find(|a| abilities.contains(a))
            .unwrap_or_else(|| "noability".into());
        let item = if rng.below(2) == 0 { None } else { Some(&items[rng.below(items.len() as u32) as usize]) };
        let mut points = [0u16; 6];
        let mut left = 66u16;
        while left > 0 {
            let i = rng.below(6) as usize;
            if points[i] < 32 {
                points[i] += 1;
                left -= 1;
            }
        }
        let n = 1 + rng.below(4.min(usable.len() as u32)) as usize;
        let mut mv = Vec::new();
        while mv.len() < n {
            let m = usable[rng.below(usable.len() as u32) as usize];
            if !mv.contains(&m) {
                mv.push(m);
            }
        }
        let set = PokemonSet {
            name: sp.name.clone(),
            species: s,
            item: item.map(|i| dex.item_id(i).unwrap()),
            ability: dex.ability_id(&ability).unwrap(),
            nature: engine::dex::NatureId(rng.below(25) as u8),
            points,
            points_filled: false,
            moves: mv,
        };
        if support::check_set(&set).is_ok() {
            team.push(set);
        } else {
            nums.pop();
        }
    }
    team
}

#[test]
fn random_games_terminate_sanely() {
    let mut rng = Rng::new(1);
    let mut finished = 0;
    for game in 0..300u64 {
        let teams = [random_team(&mut rng), random_team(&mut rng)];
        let mut b = Battle::new(teams, Chance::seeded(game)).unwrap();
        let mut steps = 0;
        while !b.is_over() && steps < 400 {
            steps += 1;
            let mut choices = [None, None];
            for side in 0..2 {
                if matches!(b.requests[side], SideRequest::Wait) {
                    continue;
                }
                let legal = b.legal_choices(side);
                assert!(!legal.is_empty(), "game {game} step {steps}: no legal choice for side {side}: {:?}", b.requests[side]);
                choices[side] = Some(legal[rng.below(legal.len() as u32) as usize].clone());
            }
            assert!(choices.iter().any(Option::is_some), "game {game}: nobody to move but not over");
            let before = b.clone();
            let c2 = choices.clone();
            let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| b.choose(choices)));
            match r {
                Ok(res) => res.unwrap_or_else(|e| panic!("game {game} step {steps}: {e:?}")),
                Err(_) => panic!(
                    "game {game} step {steps} panicked.\nchoices {:?}\nrequests {:?}\nstate {}",
                    c2,
                    before.requests,
                    serde_json::to_string_pretty(&before.snapshot()).unwrap()
                ),
            }
            for side in &b.sides {
                for m in &side.pokemon {
                    assert!(m.hp <= m.max_hp());
                    assert_eq!(m.fainted, m.hp == 0, "fainted flag out of sync");
                }
                let active = side.pokemon.iter().filter(|m| m.is_active).count();
                assert!(active <= 2);
            }
        }
        if b.is_over() {
            finished += 1;
        }
    }
    assert!(finished >= 295, "only {finished}/300 games finished within 400 steps");
}
