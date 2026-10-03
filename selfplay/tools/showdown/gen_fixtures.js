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

// --- Teams ------------------------------------------------------------------
//
// Parsing: Showdown's Teams.import of every repo team. Validation: generated
// teams, legal or with one deliberate rule break, judged by Showdown's
// TeamValidator for the format. Teams are stored as export text, so the Rust
// side parses exactly what Showdown validated.

const {Teams, TeamValidator} = require(path.join(showdown, "dist", "sim"));
const REPO = path.join(ROOT, "..");

function parseFixtures() {
	const dir = path.join(REPO, "data", "teams");
	return fs.readdirSync(dir).sort().map(file => {
		const text = fs.readFileSync(path.join(dir, file), "utf8");
		return {
			file,
			sets: Teams.import(text).map(set => ({
				name: set.name, species: toID(set.species), item: toID(set.item), ability: toID(set.ability),
				nature: toID(set.nature), moves: set.moves.map(toID),
				points: /EVs:/.test(text) ? STATS.map(st => set.evs[st]) : null,
			})),
		};
	});
}

function legalSet(rand, entry, usedItems) {
	let species = champions.species.get(entry.name);
	let item = null;
	if (species.battleOnly) {
		// Usage lists Megas by forme; a team holds the base species and its stone.
		item = species.requiredItem;
		species = champions.species.get(species.battleOnly);
	}
	const baseEntry = pool.species[species.id];
	const abilitySource = baseEntry && baseEntry.abilities ? baseEntry.abilities : [[species.abilities[0], 1]];
	const ability = weightedPick(rand, abilitySource);
	if (!item) {
		const options = entry.items.map(([i]) => i).filter(i => !usedItems.has(i) && !champions.items.get(i).megaStone);
		item = options.length ? pick(rand, options) : pick(rand, pool.items.filter(i => !usedItems.has(i) && !champions.items.get(i).megaStone));
	}
	usedItems.add(item);
	const [nature, points] = weightedPick(rand, entry.spreads.map(([n, p, w]) => [[n, p], w]));
	const moves = [...new Set(entry.moves.map(([m]) => m))].sort(() => rand() - 0.5).slice(0, 4);
	return {species: species.name, item, ability, nature, level: 50,
		evs: Object.fromEntries(STATS.map((st, i) => [st, points[i]])), moves};
}

function legalTeam(rand) {
	const team = [];
	const nums = new Set();
	const usedItems = new Set();
	while (team.length < 6) {
		const e = weightedPick(rand, speciesWeights);
		const sp = champions.species.get(e.name);
		if (nums.has(sp.num)) continue;
		nums.add(sp.num);
		team.push(legalSet(rand, e, usedItems));
	}
	return team;
}

const MUTATIONS = [
	["illegal species", (rand, t) => { t[0].species = pick(rand, champions.species.all().filter(s => s.isNonstandard === "Past" && s.num > 0)).name; }],
	["restricted legendary", (rand, t) => { t[0].species = "Koraidon"; t[0].ability = "Orichalcum Pulse"; }],
	["battle-only forme", (rand, t) => { t[0].species = "Garchomp-Mega"; t[0].ability = "Sand Force"; t[0].item = "Leftovers"; }],
	// Valid: Showdown reads a Mega forme holding its stone as the base species.
	["mega forme with its stone", (rand, t) => {
		t[0] = {...t[0], species: "Garchomp-Mega", ability: "Sand Force", item: "Garchompite",
			moves: ["Earthquake", "Dragon Claw", "Protect"]};
		for (const s of t.slice(1)) if (s.item === "Garchompite" || champions.species.get(s.species).num === 445) s.item = "Leftovers";
	}],
	["species clause", (rand, t) => { t[1] = {...t[1], species: t[0].species, ability: t[0].ability, moves: t[0].moves}; }],
	["item clause", (rand, t) => { t[1].item = t[0].item; }],
	["illegal item", (rand, t) => { t[0].item = pick(rand, champions.items.all().filter(i => i.isNonstandard === "Past")).name; }],
	["unused legal item", (rand, t) => { t[0].item = "Focus Band"; }],
	["wrong ability", (rand, t) => { t[0].ability = "Huge Power"; }],
	["unlearnable move", (rand, t) => { t[0].moves[0] = "Spore"; }],
	["duplicate move", (rand, t) => { t[0].moves = [t[0].moves[0], t[0].moves[0]]; }],
	["too many points in a stat", (rand, t) => { t[0].evs.hp = 33; }],
	["too many points total", (rand, t) => { t[0].evs = {hp: 32, atk: 32, def: 32, spa: 0, spd: 0, spe: 0}; }],
	["five pokemon", (rand, t) => { t.pop(); }],
];

function validationFixtures(count, seed) {
	const rand = rng(seed);
	const validator = TeamValidator.get(FORMAT);
	const cases = [];
	while (cases.length < count) {
		const team = legalTeam(rand);
		let mutation = "none";
		if (chance(rand, 0.6)) {
			const [name, apply] = pick(rand, MUTATIONS);
			mutation = name;
			apply(rand, team);
		}
		const problems = validator.validateTeam(team);
		cases.push({mutation, text: Teams.export(team), valid: !problems, problems: problems || []});
	}
	return cases;
}

// --- Battles ----------------------------------------------------------------
//
// Whole battles between random teams the engine supports (data/support.json),
// played by a random bot. Showdown's PRNG is replaced by the same threshold
// policy as the engine's Chance::Policy, so both simulators make the same
// "random" decisions and can be compared decision by decision. At every
// decision the fixture records Showdown's state, how many legal choices each
// side had, and the choices made.

const support = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "support.json")));
const supportedMoves = new Set(support.moves);
const supportedAbilities = new Set(support.abilities);
const supportedItems = support.items;

function policyPrng(t) {
	const idx = n => Math.min(n - 1, Math.max(0, Math.ceil(t * n) - 1));
	return {
		random(m, n) {
			if (m === undefined) return Math.min(t, 0.999999);
			if (n === undefined) { n = m; m = 0; }
			return n - m <= 1 ? m : m + idx(n - m);
		},
		randomChance(num, den) { return num >= den || num / den >= t; },
		sample(items) { return items[idx(items.length)]; },
		shuffle() {},
		getSeed() { return `policy:${t}`; },
		get startingSeed() { return `policy:${t}`; },
		clone() { return this; },
	};
}

function supportedSet(rand, entry, usedItems) {
	let species = champions.species.get(entry.name);
	let item = "";
	if (species.battleOnly) {
		const megaAbility = toID(species.abilities[0]);
		if (!supportedAbilities.has(megaAbility)) return null;
		item = species.requiredItem;
		species = champions.species.get(species.battleOnly);
	}
	const abilities = Object.values(species.abilities).map(toID).filter(a => supportedAbilities.has(a));
	const ability = abilities.length ? pick(rand, abilities) : "noability";
	if (!item && chance(rand, 0.5)) {
		const options = supportedItems.filter(i => !usedItems.has(i) && !champions.items.get(i).megaStone);
		item = pick(rand, options);
	}
	if (item) usedItems.add(toID(item));
	const poolMoves = entry.moves.map(([m]) => toID(m)).filter(m => supportedMoves.has(m));
	let moves = [...new Set(poolMoves)];
	if (!moves.length) return null;
	moves = moves.sort(() => rand() - 0.5).slice(0, 4);
	const [nature, points] = weightedPick(rand, entry.spreads.map(([n, p, w]) => [[n, p], w]));
	return {species: species.name, ability: champions.abilities.get(ability).name, item, nature, level: 50,
		evs: Object.fromEntries(STATS.map((st, i) => [st, points[i]])), moves};
}

function supportedTeam(rand) {
	const team = [];
	const nums = new Set();
	const usedItems = new Set();
	let tries = 0;
	while (team.length < 6 && tries++ < 500) {
		const e = weightedPick(rand, speciesWeights);
		const num = champions.species.get(e.name).num;
		if (nums.has(num)) continue;
		const set = supportedSet(rand, e, usedItems);
		if (!set) continue;
		nums.add(num);
		team.push(set);
	}
	return team.length === 6 ? team : null;
}

const TARGETED = new Set(["normal", "any", "adjacentAlly", "adjacentAllyOrSelf", "adjacentFoe"]);

// Mirrors engine/src/battle/choice.rs valid_target_loc for doubles.
function validTargetLoc(loc, sourcePos, target) {
	const sourceLoc = -(sourcePos + 1);
	const isSelf = loc === sourceLoc;
	const isFoe = loc > 0;
	const adjacent = isFoe ? true : Math.abs(loc - sourceLoc) === 1;
	switch (target) {
	case "randomNormal": case "scripted": case "normal": return adjacent;
	case "adjacentAlly": return adjacent && !isFoe;
	case "adjacentAllyOrSelf": return (adjacent && !isFoe) || isSelf;
	case "adjacentFoe": return adjacent && isFoe;
	case "any": return !isSelf;
	}
	return false;
}

function legalChoices(battle, side) {
	const req = side.activeRequest;
	if (!req || req.wait) return [];
	if (req.teamPreview) {
		const out = [];
		const n = side.pokemon.length;
		for (let a = 0; a < n; a++) for (let b = 0; b < n; b++) {
			if (b === a) continue;
			const rest = [...Array(n).keys()].filter(x => x !== a && x !== b);
			for (let i = 0; i < rest.length; i++) for (let j = i + 1; j < rest.length; j++) {
				out.push(`team ${[a, b, rest[i], rest[j]].map(x => x + 1).join("")}`);
			}
		}
		return out;
	}
	const bench = side.pokemon.map((p, i) => i).filter(i => i >= 2 && !side.pokemon[i].fainted);
	const slotOptions = [0, 1].map(slot => {
		if (req.forceSwitch) {
			const opts = req.forceSwitch[slot] ? bench.map(i => ({s: `switch ${i + 1}`, sw: i})) : [];
			return [...opts, {s: "pass", pass: true, forced: !!req.forceSwitch[slot]}];
		}
		const r = req.active[slot];
		const pokemon = side.active[slot];
		if (!r || !pokemon || pokemon.fainted) return [{s: "pass", pass: true}];
		const opts = [];
		const struggle = r.moves.length === 1 && r.moves[0].id === "struggle";
		// Imprison disables "hidden": the last active's request shows the move
		// as usable, but Showdown refuses it, unless every move is disabled,
		// when any of them becomes Struggle.
		const hidden = m => pokemon.moveSlots.find(s => s.id === m.id)?.disabled === "hidden";
		const realExists = r.moves.some(m => !m.disabled && !hidden(m));
		r.moves.forEach((m, j) => {
			if (m.disabled || (realExists && hidden(m))) return;
			const targets = TARGETED.has(m.target) && !struggle ?
				[1, 2, -1, -2].filter(l => validTargetLoc(l, slot, m.target)) : [0];
			for (const t of targets) {
				const base = `move ${j + 1}${t ? " " + t : ""}`;
				opts.push({s: base});
				if (r.canMegaEvo) opts.push({s: base + " mega", mega: true});
			}
		});
		// Shadow Tag traps "hidden" (not in the last active's request), but
		// Showdown refuses the switch.
		if (!r.trapped && !pokemon.trapped) for (const i of bench) opts.push({s: `switch ${i + 1}`, sw: i});
		return opts;
	});
	const out = [];
	const need = req.forceSwitch ? req.forceSwitch.filter(Boolean).length : 0;
	const passesNeeded = Math.max(0, need - bench.length);
	for (const x of slotOptions[0]) for (const y of slotOptions[1]) {
		if (x.sw !== undefined && x.sw === y.sw) continue;
		if (x.mega && y.mega) continue;
		if (req.forceSwitch && [x, y].filter(o => o.forced).length !== passesNeeded) continue;
		out.push(`${x.s}, ${y.s}`);
	}
	return out;
}

/** Effect states as {id: remaining duration}. */
function durations(states) {
	return Object.fromEntries(Object.entries(states).map(([id, s]) => [id, s.duration ?? null]));
}

function battleSnapshot(battle) {
	return {
		turn: battle.turn,
		requests: battle.sides.map(side => {
			const r = side.activeRequest;
			if (battle.ended || !r || r.wait) return "wait";
			if (r.teamPreview) return "teampreview";
			if (r.forceSwitch) return {switch: [0, 1].map(i => !!r.forceSwitch[i])};
			return "move";
		}),
		outcome: battle.ended ? (battle.winner ? battle.sides.findIndex(s => s.name === battle.winner) : "tie") : null,
		weather: battle.field.weather,
		terrain: battle.field.terrain,
		weatherTurns: battle.field.weather ? battle.field.weatherState.duration ?? null : null,
		terrainTurns: battle.field.terrain ? battle.field.terrainState.duration ?? null : null,
		pseudoWeather: durations(battle.field.pseudoWeather),
		sides: battle.sides.map(side => ({
			totalFainted: side.totalFainted,
			sideConditions: durations(side.sideConditions),
			pokemon: side.pokemon.map(p => ({
				species: p.species.id, hp: p.hp, maxhp: p.maxhp, status: p.fainted ? "fnt" : p.status,
				active: p.isActive,
				boosts: ["atk", "def", "spa", "spd", "spe", "accuracy", "evasion"].map(b => p.boosts[b]),
				item: p.item, ability: p.ability, pp: p.moveSlots.map(m => m.pp),
				volatiles: Object.keys(p.volatiles).sort(),
			})),
		})),
	};
}

function battleFixtures(perPolicy, seed) {
	const out = [];
	const rand = rng(seed);
	for (const threshold of [0.5, 0, 1.01]) {
		for (let n = 0; n < perPolicy; n++) {
			const teams = [supportedTeam(rand), supportedTeam(rand)];
			if (!teams[0] || !teams[1]) { n--; continue; }
			const battle = new Battle({formatid: FORMAT});
			battle.prng = policyPrng(threshold);
			battle.setPlayer("p1", {name: "p1", team: teams[0]});
			battle.setPlayer("p2", {name: "p2", team: teams[1]});
			const steps = [];
			for (let step = 0; step < 80 && !battle.ended; step++) {
				const snapshot = battleSnapshot(battle);
				const choices = [null, null];
				const counts = [null, null];
				for (const [i, side] of battle.sides.entries()) {
					const legal = legalChoices(battle, side);
					if (!legal.length) continue;
					counts[i] = legal.length;
					choices[i] = pick(rand, legal);
				}
				steps.push({snapshot, counts, choices});
				for (const [i, side] of battle.sides.entries()) {
					if (choices[i] === null) continue;
					if (!battle.choose(side.id, choices[i])) {
						throw new Error(`Showdown rejected ${choices[i]}: ${side.choice.error}`);
					}
				}
			}
			out.push({threshold, teams: teams.map(t => Teams.export(t)), steps, final: battleSnapshot(battle)});
		}
	}
	return out;
}

const stats = statFixtures();
console.log(`${write("stats.json", stats)}: ${stats.length} cases`);
const damage = damageFixtures(Number(process.env.DAMAGE_CASES || 4000), 20261003);
console.log(`${write("damage.json", damage)}: ${damage.length} cases`);
const teams = {parse: parseFixtures(), validate: validationFixtures(600, 7)};
console.log(`${write("teams.json", teams)}: ${teams.parse.length} repo teams, ${teams.validate.length} validation cases`);
const battles = battleFixtures(Number(process.env.BATTLES_PER_POLICY || 40), 99);
console.log(`${write("battles.json", battles)}: ${battles.length} battles, ` +
	`${battles.reduce((n, b) => n + b.steps.length, 0)} decisions`);
