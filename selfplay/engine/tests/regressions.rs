//! Engine bugs found in training, each checked against Showdown.

use engine::battle::choice::SideChoice;
use engine::battle::Battle;
use engine::chance::Chance;
use engine::team::parse_paste;

fn choose(b: &mut Battle, p1: &str, p2: &str) {
    let c = [p1, p2].map(|c| Some(SideChoice::from_showdown(c).unwrap()));
    b.choose(c).unwrap();
}

/// A spread contact move into Spiky Shield: the 1/8 recoil KOs the user,
/// and the move still hits the other target (Showdown: Basculegion goes
/// 195 -> 128 and loses an Attack stage). The engine panicked here ("damage
/// ctx refers to an empty slot") because a fainted user left the damage
/// calculation.
#[test]
fn spread_move_user_fainting_to_spiky_shield_still_hits() {
    let p1 = parse_paste(
        "Charizard @ Charizardite X\nAbility: Blaze\nLevel: 50\n- Breaking Swipe\n- Protect\n- Flare Blitz\n- Dragon Claw\n\n\
         Sableye @ Light Clay\nAbility: Prankster\nLevel: 50\n- Protect\n- Reflect\n- Light Screen\n- Foul Play\n\n\
         Incineroar @ Sitrus Berry\nAbility: Intimidate\nLevel: 50\n- Fake Out\n- Protect\n- Flare Blitz\n- Parting Shot\n\n\
         Pelipper @ Damp Rock\nAbility: Drizzle\nLevel: 50\n- Hurricane\n- Protect\n- Tailwind\n- Weather Ball",
    )
    .unwrap();
    let p2 = parse_paste(
        "Glimmora @ Focus Sash\nAbility: Toxic Debris\nLevel: 50\n- Spiky Shield\n- Sludge Bomb\n- Power Gem\n- Earth Power\n\n\
         Basculegion @ Focus Sash\nAbility: Adaptability\nLevel: 50\n- Wave Crash\n- Protect\n- Aqua Jet\n- Last Respects\n\n\
         Staraptor @ Staraptite\nAbility: Intimidate\nLevel: 50\n- Brave Bird\n- Protect\n- Close Combat\n- U-turn\n\n\
         Kingambit @ Occa Berry\nAbility: Defiant\nLevel: 50\n- Kowtow Cleave\n- Protect\n- Sucker Punch\n- Iron Head",
    )
    .unwrap();
    let mut b = Battle::new([p1, p2], Chance::policy(0.5)).unwrap();
    choose(&mut b, "team 1234", "team 1234");
    let zard = b.sides[0].pokemon.iter_mut().find(|m| m.position == 0).unwrap();
    zard.hp = 7;
    choose(&mut b, "move 1 mega, move 1", "move 1, move 3 2");
    let zard = b.sides[0].pokemon.iter().find(|m| m.uid == 0).unwrap();
    assert!(zard.fainted, "Spiky Shield KOs Charizard");
    let bascu = b.sides[1].pokemon.iter().find(|m| m.uid == 1).unwrap();
    assert!(bascu.hp < bascu.max_hp(), "Breaking Swipe still hits Basculegion");
    assert_eq!(bascu.boosts[0], -1, "and lowers its Attack");
}
