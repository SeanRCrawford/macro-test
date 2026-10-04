//! The team corpus (`data/corpus`): Showdown pastes named like
//! "player's Event Top 8 Team.txt". Teams that placed are weighted up, the
//! best placings most, so training sees proven teams more often.

use crate::battle::support;
use crate::team::{self, PokemonSet};
use std::path::Path;

/// How a team did, read from its paste's name.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Placement {
    Champion,
    RunnerUp,
    /// "Top N".
    Top(u16),
    /// No placement in the name (ladder or showcase teams).
    Unplaced,
}

impl Placement {
    /// Read the placement from a paste's name: "Champion" (not
    /// "Champions"), "Runner Up" or "Top N".
    pub fn from_name(name: &str) -> Placement {
        let words: Vec<String> = name
            .split(|c: char| !c.is_ascii_alphanumeric())
            .filter(|w| !w.is_empty())
            .map(str::to_ascii_lowercase)
            .collect();
        let mut best = Placement::Unplaced;
        for (i, w) in words.iter().enumerate() {
            let p = match w.as_str() {
                "champion" => Placement::Champion,
                "runner" if words.get(i + 1).is_some_and(|n| n == "up") => Placement::RunnerUp,
                "runnerup" => Placement::RunnerUp,
                "top" => match words.get(i + 1).and_then(|n| n.parse().ok()) {
                    Some(n) => Placement::Top(n),
                    None => continue,
                },
                _ => continue,
            };
            if p.weight() > best.weight() {
                best = p;
            }
        }
        best
    }

    /// Sampling weight relative to an unplaced team.
    pub fn weight(self) -> f64 {
        match self {
            Placement::Champion => 8.0,
            Placement::RunnerUp => 6.0,
            Placement::Top(n) if n <= 4 => 5.0,
            Placement::Top(n) if n <= 8 => 4.0,
            Placement::Top(n) if n <= 16 => 3.0,
            Placement::Top(n) if n <= 32 => 2.0,
            Placement::Top(_) => 1.5,
            Placement::Unplaced => 1.0,
        }
    }
}

#[derive(Debug, Clone)]
pub struct CorpusTeam {
    /// The paste's file name without ".txt".
    pub name: String,
    pub placement: Placement,
    pub weight: f64,
    pub sets: Vec<PokemonSet>,
}

/// A paste left out, and why.
#[derive(Debug, Clone)]
pub struct Rejected {
    pub name: String,
    pub reason: String,
}

/// Load every `.txt` paste in `dir`, in name order. Teams that don't
/// parse, aren't legal six-Pokemon Reg M-C teams, or use something the
/// engine doesn't support yet are rejected.
pub fn load_dir(dir: &Path) -> std::io::Result<(Vec<CorpusTeam>, Vec<Rejected>)> {
    let mut paths: Vec<_> = std::fs::read_dir(dir)?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|e| e == "txt"))
        .collect();
    paths.sort();
    let (mut teams, mut rejected) = (Vec::new(), Vec::new());
    for p in paths {
        let name = p
            .file_stem()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        let text = std::fs::read_to_string(&p)?;
        match check(&text) {
            Ok(sets) => {
                let placement = Placement::from_name(&name);
                teams.push(CorpusTeam {
                    name,
                    placement,
                    weight: placement.weight(),
                    sets,
                });
            }
            Err(reason) => rejected.push(Rejected { name, reason }),
        }
    }
    Ok((teams, rejected))
}

fn check(text: &str) -> Result<Vec<PokemonSet>, String> {
    let sets = team::parse_paste(text)?;
    if sets.len() != 6 {
        return Err(format!("{} Pokemon, not 6", sets.len()));
    }
    if let Some(p) = team::validate(&sets).first() {
        return Err(p.message.clone());
    }
    let missing: Vec<String> = sets.iter().flat_map(support::unsupported).collect();
    if !missing.is_empty() {
        return Err(format!("unsupported: {}", missing.join(", ")));
    }
    Ok(sets)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn placements_from_names() {
        let p = Placement::from_name;
        assert_eq!(p("vgcluca's Maddo's Cup #12 Champion Team"), Placement::Champion);
        assert_eq!(
            p("Jannik's ValkrixVGC Pokemon Champions League #03 Champion Team"),
            Placement::Champion
        );
        assert_eq!(p("ApronVGC's Frankfurt Regional 2027 Runner Up Team"), Placement::RunnerUp);
        assert_eq!(p("Dorian Kang's Baltimore Regional 2027 Top 16 Team"), Placement::Top(16));
        assert_eq!(p("Elm's Baxcalibur Floette Team"), Placement::Unplaced);
        assert_eq!(p("Pokemon Champions showcase"), Placement::Unplaced);
    }
}
