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

/// How a source sets its teams' weights.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Weighting {
    /// By the placement in each file name.
    Placement,
    /// Every team this weight.
    Fixed(f64),
    /// The folder's teams together this fraction of all draws, however many.
    Share(f64),
}

/// Load several folders as one corpus. Each source is `PATH` (teams
/// weighted by the placement in their names), `PATH=WEIGHT` (every team
/// WEIGHT) or `PATH=share:FRACTION` (the folder's teams together FRACTION of
/// the corpus's draws, however many there are). A `weights.json` in a folder
/// (file name without ".txt" -> number) scales its teams against each other.
/// Teams from every source but the first are named "<folder name>/<file
/// name>", so names stay unique.
pub fn load_sources(sources: &[String]) -> std::io::Result<(Vec<CorpusTeam>, Vec<Rejected>)> {
    let (mut teams, mut rejected) = (Vec::new(), Vec::new());
    let mut shares: Vec<(std::ops::Range<usize>, f64)> = Vec::new();
    for (i, src) in sources.iter().enumerate() {
        let (path, how) = match src.rsplit_once('=') {
            Some((p, w)) => match (w.strip_prefix("share:").and_then(|f| f.parse::<f64>().ok()), w.parse::<f64>()) {
                (Some(f), _) => (p, Weighting::Share(f.clamp(0.0, 0.95))),
                (None, Ok(x)) => (p, Weighting::Fixed(x)),
                (None, Err(_)) => (src.as_str(), Weighting::Placement),
            },
            None => (src.as_str(), Weighting::Placement),
        };
        let dir = Path::new(path);
        let (mut t, mut r) = load_dir(dir)?;
        let relative: std::collections::HashMap<String, f64> = std::fs::read_to_string(dir.join("weights.json"))
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default();
        let prefix = if i == 0 {
            String::new()
        } else {
            dir.file_name()
                .map(|n| format!("{}/", n.to_string_lossy()))
                .unwrap_or_default()
        };
        for team in &mut t {
            let scale = relative.get(&team.name).copied().filter(|w| w.is_finite() && *w >= 0.0).unwrap_or(1.0);
            team.weight = scale
                * match how {
                    Weighting::Placement => team.weight,
                    Weighting::Fixed(w) => w,
                    Weighting::Share(_) => 1.0,
                };
            team.name = format!("{prefix}{}", team.name);
        }
        for x in &mut r {
            x.name = format!("{prefix}{}", x.name);
        }
        if let Weighting::Share(f) = how {
            shares.push((teams.len()..teams.len() + t.len(), f));
        }
        teams.append(&mut t);
        rejected.append(&mut r);
    }
    // Shares: each such folder's weight becomes its fraction of the total
    // (the rest keep theirs).
    let in_share = |k: usize| shares.iter().any(|(r, _)| r.contains(&k));
    let others: f64 = (0..teams.len()).filter(|&k| !in_share(k)).map(|k| teams[k].weight).sum();
    let fractions: f64 = shares.iter().map(|(r, f)| if r.is_empty() { 0.0 } else { *f }).sum::<f64>().min(0.95);
    for (range, f) in &shares {
        let sum: f64 = teams[range.clone()].iter().map(|t| t.weight).sum();
        if sum > 0.0 && others > 0.0 {
            let target = f / (1.0 - fractions) * others;
            for t in &mut teams[range.clone()] {
                t.weight *= target / sum;
            }
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
        assert_eq!(
            p("vgcluca's Maddo's Cup #12 Champion Team"),
            Placement::Champion
        );
        assert_eq!(
            p("Jannik's ValkrixVGC Pokemon Champions League #03 Champion Team"),
            Placement::Champion
        );
        assert_eq!(
            p("ApronVGC's Frankfurt Regional 2027 Runner Up Team"),
            Placement::RunnerUp
        );
        assert_eq!(
            p("Dorian Kang's Baltimore Regional 2027 Top 16 Team"),
            Placement::Top(16)
        );
        assert_eq!(p("Elm's Baxcalibur Floette Team"), Placement::Unplaced);
        assert_eq!(p("Pokemon Champions showcase"), Placement::Unplaced);
    }

    #[test]
    fn a_folder_can_take_a_share_of_the_draws_and_weight_its_teams() {
        // Three corpus teams copied into a folder with relative weights 1:3
        // (the third unlisted: 1), as a 20% share next to the corpus.
        let corpus = Path::new(env!("CARGO_MANIFEST_DIR")).join("../data/corpus");
        let dir = std::env::temp_dir().join(format!("corpus_share_{}", std::process::id()));
        let seen = dir.join("seen");
        std::fs::create_dir_all(&seen).unwrap();
        let mut names = Vec::new();
        for entry in std::fs::read_dir(&corpus).unwrap().filter_map(|e| e.ok()) {
            let p = entry.path();
            if p.extension().is_some_and(|e| e == "txt") && check(&std::fs::read_to_string(&p).unwrap()).is_ok() {
                let name = format!("t{}", names.len());
                std::fs::copy(&p, seen.join(format!("{name}.txt"))).unwrap();
                names.push(name);
                if names.len() == 3 {
                    break;
                }
            }
        }
        std::fs::write(seen.join("weights.json"), r#"{"t0": 1.0, "t1": 3.0}"#).unwrap();
        let src = |s: &str| vec![corpus.to_string_lossy().into_owned(), format!("{}{s}", seen.to_string_lossy())];
        let (teams, _) = load_sources(&src("=share:0.2")).unwrap();
        let total: f64 = teams.iter().map(|t| t.weight).sum();
        let w = |n: &str| teams.iter().find(|t| t.name == format!("seen/{n}")).unwrap().weight;
        assert!(((w("t0") + w("t1") + w("t2")) / total - 0.2).abs() < 1e-9);
        assert!((w("t1") / w("t0") - 3.0).abs() < 1e-9 && (w("t2") / w("t0") - 1.0).abs() < 1e-9);
        // A fixed weight still applies, scaled by weights.json.
        let (teams, _) = load_sources(&src("=2")).unwrap();
        let w = |n: &str| teams.iter().find(|t| t.name == format!("seen/{n}")).unwrap().weight;
        assert_eq!((w("t0"), w("t1"), w("t2")), (2.0, 6.0, 2.0));
        std::fs::remove_dir_all(&dir).ok();
    }
}
