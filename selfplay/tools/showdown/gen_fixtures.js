// Generate engine test fixtures by running Showdown's own code.
//
//   node selfplay/tools/showdown/gen_fixtures.js /path/to/pokemon-showdown
//
// Writes selfplay/engine/tests/fixtures/*.json. The Rust tests compare the
// engine against these, so they run without Node or a Showdown checkout;
// regenerate whenever the Showdown commit in data/dex.json changes.
"use strict";
const fs = require("fs");
const path = require("path");

const showdown = path.resolve(process.argv[2] || process.env.SHOWDOWN || "");
if (!fs.existsSync(path.join(showdown, "dist", "sim"))) {
	console.error("usage: node gen_fixtures.js /path/to/built/pokemon-showdown");
	process.exit(1);
}
const {Battle, Dex, toID} = require(path.join(showdown, "dist", "sim"));
const FORMAT = "gen9championsvgc2026regmc";
const ROOT = path.join(__dirname, "..", "..");
const OUT_DIR = path.join(ROOT, "engine", "tests", "fixtures");
const pool = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "regmc_pool.json")));
const STATS = ["hp", "atk", "def", "spa", "spd", "spe"];

function write(name, value) {
	fs.mkdirSync(OUT_DIR, {recursive: true});
	const file = path.join(OUT_DIR, name);
	fs.writeFileSync(file, JSON.stringify(value) + "\n");
	return file;
}

// Stats: every pool species with every spread the usage stats list, plus the
// neutral 0-point spread under every nature.
function statFixtures() {
	const battle = new Battle({formatid: FORMAT});
	const natures = Dex.natures.all().map(n => n.name);
	const cases = [];
	for (const entry of Object.values(pool.species)) {
		const species = Dex.mod("champions").species.get(entry.name);
		const spreads = (entry.spreads || []).map(([nature, points]) => [nature, points]);
		for (const nature of natures) spreads.push([nature, [0, 0, 0, 0, 0, 0]]);
		for (const [nature, points] of spreads) {
			const set = {nature, evs: Object.fromEntries(STATS.map((s, i) => [s, points[i]])), level: 50};
			const stats = battle.spreadModify(species.baseStats, set);
			cases.push({species: toID(species.name), nature, points, stats: STATS.map(s => stats[s])});
		}
	}
	return cases;
}

// --- Damage -----------------------------------------------------------------
//
// Random but reproducible situations built from the pool's usage stats: four
// actives with sets drawn from their listed spreads, items and abilities, a
// damaging move from the attacker's list, and random weather, terrain,
// screens, boosts, statuses, HP and volatiles. Each case runs through a real
// Showdown battle: the same ModifyType/ModifyMove steps useMove takes, then
// getDamage once per random roll.

const champions = Dex.mod("champions");
const tr = Math.trunc;

// mulberry32: a small seeded PRNG so the fixtures are reproducible.
function rng(seed) {
	return () => {
		seed |= 0; seed = seed + 0x6D2B79F5 | 0;
		let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
		t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
		return ((t ^ t >>> 14) >>> 0) / 4294967296;
	};
}

function weightedPick(rand, entries) {
	const total = entries.reduce((s, [, w]) => s + w, 0);
	let x = rand() * total;
	for (const [v, w] of entries) {
		if ((x -= w) < 0) return v;
	}
	return entries[entries.length - 1][0];
}
const pick = (rand, list) => list[Math.floor(rand() * list.length)];
const chance = (rand, p) => rand() < p;

const detailed = Object.values(pool.species).filter(e => e.spreads && e.spreads.length && e.moves.length);
const speciesWeights = detailed.map(e => [e, e.raw_count]);

// Moves whose damage can't be recomputed once per roll: Beat Up consumes its
// ally list, and Fickle Beam and Shell Side Arm roll randomness inside the
// calculation.
const NOT_REPEATABLE = new Set(["beatup", "ficklebeam", "shellsidearm"]);

function randomSet(rand, entry, wantDamagingMove) {
	const [nature, points] = weightedPick(rand, entry.spreads.map(([n, p, w]) => [[n, p], w]));
	const ability = weightedPick(rand, entry.abilities);
	// Mostly the species' own items; sometimes any legal item.
	const item = chance(rand, 0.85) && entry.items.length ? weightedPick(rand, entry.items) : pick(rand, pool.items);
	let moves = entry.moves.map(([m]) => m);
	if (wantDamagingMove) {
		moves = moves.filter(m => champions.moves.get(m).category !== "Status" && !NOT_REPEATABLE.has(toID(m)));
	}
	return {
		species: entry.name, ability, item, nature, level: 50,
		evs: Object.fromEntries(STATS.map((s, i) => [s, points[i]])),
		moves: moves.length ? [pick(rand, moves)] : [],
	};
}

const WEATHERS = ["", "", "", "", "sunnyday", "raindance", "sandstorm", "snowscape"];
const TERRAINS = ["", "", "", "electricterrain", "grassyterrain", "mistyterrain", "psychicterrain"];
const STATUSES = ["brn", "par", "psn", "tox", "slp", "frz"];

function randomCase(rand) {
	// Four different base species (species clause).
	const entries = [];
	const bases = new Set();
	while (entries.length < 4) {
		const e = weightedPick(rand, speciesWeights);
		const base = champions.species.get(e.name).baseSpecies;
		if (bases.has(base)) continue;
		bases.add(base);
		entries.push(e);
	}
	const sets = entries.map((e, i) => randomSet(rand, e, i === 0));
	if (!sets[0].moves.length) return null;
	const mons = sets.map(() => ({
		boosts: chance(rand, 0.6) ? {} : Object.fromEntries(["atk", "def", "spa", "spd"]
			.filter(() => chance(rand, 0.4)).map(s => [s, Math.floor(rand() * 5) - 2])),
		status: chance(rand, 0.8) ? "" : pick(rand, STATUSES),
		hpFraction: chance(rand, 0.6) ? 1 : rand(),
		activeTurns: chance(rand, 0.2) ? 0 : 1,
		timesAttacked: Math.floor(rand() * 3),
		volatiles: [],
	}));
	if (chance(rand, 0.1)) mons[0].volatiles.push("helpinghand");
	if (chance(rand, 0.03)) mons[0].volatiles.push("charge");
	if (sets[0].ability === "Flash Fire" && chance(rand, 0.5)) mons[0].volatiles.push("flashfire");
	if (chance(rand, 0.03)) mons[2].volatiles.push("glaiverush");
	const screens = ["reflect", "lightscreen", "auroraveil"].filter(() => chance(rand, 0.12));
	return {
		sets, mons, screens,
		weather: pick(rand, WEATHERS),
		terrain: pick(rand, TERRAINS),
		fainted: [Math.floor(rand() * 3), Math.floor(rand() * 3)],
		crit: chance(rand, 0.15),
		wantSpread: chance(rand, 0.8),
	};
}

const filler = name => ({species: name, ability: "No Ability", item: "", nature: "Hardy", level: 50,
	evs: {hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0}, moves: ["splash"]});

// Build the battle, then force the case's state onto it. Start-of-battle
// effects (Intimidate, weather and terrain setters, seeds) are undone by
// setting everything explicitly afterwards.
function runCase(c, seed) {
	const battle = new Battle({formatid: FORMAT, seed: [seed, seed + 1, seed + 2, seed + 3]});
	battle.setPlayer("p1", {name: "a", team: [c.sets[0], c.sets[1], filler("Magikarp"), filler("Feebas")]});
	battle.setPlayer("p2", {name: "b", team: [c.sets[2], c.sets[3], filler("Wailmer"), filler("Goldeen")]});
	battle.choose("p1", "team 1234");
	battle.choose("p2", "team 1234");
	const actives = [...battle.sides[0].active, ...battle.sides[1].active];
	battle.field.clearWeather();
	battle.field.clearTerrain();
	if (c.weather) battle.field.setWeather(c.weather, "debug");
	if (c.terrain) battle.field.setTerrain(c.terrain, "debug");
	for (const [i, side] of battle.sides.entries()) {
		for (const id of ["reflect", "lightscreen", "auroraveil"]) side.removeSideCondition(id);
		if (i === 1) for (const id of c.screens) side.addSideCondition(id, "debug");
		side.totalFainted = c.fainted[i];
	}
	for (const [i, pokemon] of actives.entries()) {
		const m = c.mons[i];
		pokemon.volatiles = {};
		pokemon.clearBoosts();
		for (const [s, v] of Object.entries(m.boosts)) pokemon.boosts[s] = v;
		pokemon.status = m.status;
		pokemon.statusState = {id: m.status, target: pokemon};
		pokemon.hp = Math.max(1, Math.floor(pokemon.maxhp * m.hpFraction));
		pokemon.item = toID(c.sets[i].item);
		pokemon.itemState = {id: pokemon.item, target: pokemon};
		pokemon.activeTurns = m.activeTurns;
		pokemon.timesAttacked = m.timesAttacked;
		if (pokemon.hasAbility("supremeoverlord")) {
			pokemon.abilityState.fallen = Math.min(c.fainted[pokemon.side.n], 5) || undefined;
		}
		for (const v of m.volatiles) pokemon.addVolatile(v);
	}
	for (const pokemon of actives) pokemon.updateSpeed();
	const speeds = actives.map(p => p.speed);
	if (new Set(speeds).size < speeds.length) return null; // Showdown shuffles exact ties

	const [attacker, , defender] = actives;
	let move = battle.dex.getActiveMove(c.sets[0].moves[0]);
	battle.setActiveMove(move, attacker, defender);
	battle.singleEvent("ModifyType", move, null, attacker, defender, move, move);
	battle.singleEvent("ModifyMove", move, null, attacker, defender, move, move);
	move = battle.runEvent("ModifyType", attacker, defender, move, move);
	move = battle.runEvent("ModifyMove", attacker, defender, move, move);
	const spread = c.wantSpread && ["allAdjacentFoes", "allAdjacent"].includes(move.target);
	move.spreadHit = spread;
	move.hit = 1;
	move.willCrit = c.crit;

	// Snapshot the state before calculating: Final Gambit faints its user.
	const record = {
		move: toID(c.sets[0].moves[0]),
		weather: c.weather, terrain: c.terrain,
		sides: [0, 1].map(s => ({
			reflect: !!battle.sides[s].sideConditions.reflect,
			lightScreen: !!battle.sides[s].sideConditions.lightscreen,
			auroraVeil: !!battle.sides[s].sideConditions.auroraveil,
			fainted: battle.sides[s].totalFainted,
		})),
		crit: c.crit, spread, hit: 1,
		mons: actives.map((p, i) => ({
			species: p.species.id,
			types: p.getTypes(),
			stats: [p.maxhp, ...["atk", "def", "spa", "spd", "spe"].map(s => p.storedStats[s])],
			hp: p.hp,
			boosts: ["atk", "def", "spa", "spd", "spe"].map(s => p.boosts[s]),
			ability: p.ability,
			item: toID(c.sets[i].item),
			status: p.status,
			speed: p.speed,
			volatiles: Object.keys(p.volatiles),
			helpingHand: p.volatiles.helpinghand ? Math.round(Math.log(p.volatiles.helpinghand.multiplier) / Math.log(1.5)) : 0,
			activeTurns: p.activeTurns,
			timesAttacked: p.timesAttacked,
			fallen: p.abilityState.fallen || 0,
		})),
	};
	let result;
	const rolls = [];
	// Fixed-damage moves ignore the roll, and Final Gambit faints its user
	// mid-calculation, so they are computed once.
	const rollCount = move.damageCallback || move.damage ? 1 : 16;
	for (let r = 0; r < rollCount; r++) {
		defender.item = toID(c.sets[2].item);
		defender.itemState = {id: defender.item, target: defender};
		battle.randomizer = baseDamage => tr(tr(baseDamage * (100 - r)) / 100);
		const d = battle.actions.getDamage(attacker, defender, move, true);
		if (d === false || d === undefined || d === null) {
			result = d === false ? "immune" : "nodamage";
			break;
		}
		rolls.push(d);
	}
	while (rolls.length && rolls.length < 16) rolls.push(rolls[0]);
	record.result = result || rolls;
	return record;
}

function damageFixtures(count, seed) {
	const rand = rng(seed);
	const cases = [];
	let attempts = 0;
	while (cases.length < count) {
		attempts++;
		const c = randomCase(rand);
		if (!c) continue;
		const out = runCase(c, attempts);
		if (out) cases.push(out);
	}
	return cases;
}

const stats = statFixtures();
console.log(`${write("stats.json", stats)}: ${stats.length} cases`);
const damage = damageFixtures(Number(process.env.DAMAGE_CASES || 4000), 20261003);
console.log(`${write("damage.json", damage)}: ${damage.length} cases`);
