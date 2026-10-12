// Generate the Showdown client's protocol fixtures by running Showdown's own
// simulator.
//
//   node selfplay/tools/showdown/protocol_fixtures.js /path/to/pokemon-showdown
//
// Writes selfplay/tests/fixtures/protocol.json.gz: random-play battles
// between the corpus teams the engine supports, each with everything each
// player's client receives (the battle log as that player sees it, with its
// requests) and, at each of its decisions, Showdown's true state in the
// shape of gen_fixtures.js's battle snapshots. tests/test_showdown.py
// follows the log with the client's tracker, rebuilds the position in the
// engine and compares it with the truth. Half the battles use Open Team
// Sheets.
"use strict";
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");

const showdown = path.resolve(process.argv[2] || process.env.SHOWDOWN || "");
if (!fs.existsSync(path.join(showdown, "dist", "sim"))) {
	console.error("usage: node protocol_fixtures.js /path/to/built/pokemon-showdown");
	process.exit(1);
}
const {Battle, Teams, TeamValidator, toID} = require(path.join(showdown, "dist", "sim"));
const {extractChannelMessages} = require(path.join(showdown, "dist", "sim", "battle"));
const {legalChoices} = require("./choices");
const FORMAT = "gen9championsvgc2026regmc";
const ROOT = path.join(__dirname, "..", "..");
const OUT = process.env.OUT || path.join(ROOT, "tests", "fixtures", "protocol.json.gz");
const BATTLES = Number(process.env.BATTLES || 60);
const SEED = Number(process.env.SEED || 20261012);

// mulberry32, as in gen_fixtures.js.
function rng(seed) {
	return () => {
		seed |= 0; seed = seed + 0x6D2B79F5 | 0;
		let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
		t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
		return ((t ^ t >>> 14) >>> 0) / 4294967296;
	};
}
const pick = (rand, list) => list[Math.floor(rand() * list.length)];

const support = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "support.json")));
const supportedMoves = new Set(support.moves);
const supportedAbilities = new Set(support.abilities);
const supportedItems = new Set(support.items);

function loadCorpus() {
	const dir = path.join(ROOT, "data", "corpus");
	const validator = TeamValidator.get(FORMAT);
	const ok = s => (!s.item || supportedItems.has(toID(s.item))) &&
		supportedAbilities.has(toID(s.ability)) && s.moves.every(m => supportedMoves.has(toID(m)));
	const teams = [];
	for (const file of fs.readdirSync(dir).sort()) {
		const text = fs.readFileSync(path.join(dir, file), "utf8");
		const sets = Teams.import(text);
		// The validator also normalises sets as the server does (a Mega forme
		// holding its stone becomes the base species).
		if (!sets || sets.length !== 6 || validator.validateTeam(sets) || !sets.every(ok)) continue;
		teams.push({file, text: Teams.export(sets)});
	}
	return teams;
}

const durations = states => Object.fromEntries(Object.entries(states).map(([id, s]) => [id, s.duration ?? s.layers ?? null]));
const sideDurations = states => Object.fromEntries(Object.entries(states).map(([id, s]) => [id, s.duration ?? s.layers ?? true]));

// gen_fixtures.js's battleSnapshot, plus what the tracker should also get
// right: status turns, volatile durations, PP and how far each Pokemon has
// been seen.
function truth(battle) {
	return {
		turn: battle.turn,
		requests: battle.sides.map(side => {
			const r = side.activeRequest;
			if (battle.ended || !r || r.wait) return "wait";
			if (r.teamPreview) return "teampreview";
			if (r.forceSwitch) return {switch: [0, 1].map(i => !!r.forceSwitch[i])};
			return "move";
		}),
		outcome: null,
		weather: battle.field.weather,
		terrain: battle.field.terrain,
		weatherTurns: battle.field.weather ? battle.field.weatherState.duration ?? null : null,
		terrainTurns: battle.field.terrain ? battle.field.terrainState.duration ?? null : null,
		pseudoWeather: durations(battle.field.pseudoWeather),
		sides: battle.sides.map(side => ({
			totalFainted: side.totalFainted,
			sideConditions: sideDurations(side.sideConditions),
			pokemon: side.pokemon.map(p => ({
				species: p.species.id, hp: p.hp, maxhp: p.maxhp, status: p.fainted ? "fnt" : p.status,
				active: p.isActive,
				boosts: ["atk", "def", "spa", "spd", "spe", "accuracy", "evasion"].map(b => p.boosts[b]),
				item: p.item, ability: p.ability, pp: p.moveSlots.map(m => m.pp),
				moves: p.moveSlots.map(m => m.id),
				volatiles: Object.keys(p.volatiles).sort(),
				volatileTurns: durations(p.volatiles),
				statusTime: p.statusState.time ?? null,
				statusStage: p.statusState.stage ?? null,
			})),
		})),
	};
}

function play(teamTexts, seed, ots, rand) {
	// Each player's stream, and its decisions.
	const lines = [[], []];
	const decisions = [[], []];
	const battle = new Battle({
		formatid: FORMAT,
		seed: `${seed},${seed * 7 + 1},${seed * 13 + 2},${seed * 31 + 3}`,
		send(type, data) {
			if (Array.isArray(data)) data = data.join("\n");
			if (type === "update") {
				const channels = extractChannelMessages(data, [1, 2]);
				lines[0].push(...channels[1]);
				lines[1].push(...channels[2]);
			} else if (type === "sideupdate") {
				const [side, rest] = [data.slice(0, 2), data.slice(3)];
				if (side === "p1" || side === "p2") lines[side === "p1" ? 0 : 1].push(...rest.split("\n"));
			}
		},
	});
	const sets = teamTexts.map(t => Teams.import(t));
	battle.setPlayer("p1", {name: "p1", team: sets[0]});
	battle.setPlayer("p2", {name: "p2", team: sets[1]});
	// Showdown keeps each Pokemon's set; record which team slot it is.
	const slot = battle.sides.map(side => new Map(side.pokemon.map((p, i) => [p, i])));
	if (ots) battle.showOpenTeamSheets();
	battle.sendUpdates();
	for (let step = 0; step < 120 && !battle.ended; step++) {
		for (const [n, player] of battle.sides.entries()) {
			if (!player.activeRequest || player.activeRequest.wait) continue;
			const t = truth(battle);
			for (const [i, side] of battle.sides.entries()) {
				side.pokemon.forEach((p, k) => { t.sides[i].pokemon[k].teamIndex = slot[i].get(p); });
			}
			decisions[n].push({at: lines[n].length, truth: t, legal: legalChoices(battle, player)});
		}
		const choices = battle.sides.map(side => {
			const legal = legalChoices(battle, side);
			return legal.length ? pick(rand, legal) : null;
		});
		for (const [i, side] of battle.sides.entries()) {
			if (choices[i] === null) continue;
			if (!battle.choose(side.id, choices[i])) {
				throw new Error(`Showdown rejected ${choices[i]}: ${side.choice.error}`);
			}
		}
		battle.sendUpdates();
	}
	// The teams as played: Showdown reads a paste without EVs as all zero
	// (the engine would fill a usage spread), so spell them out.
	const exported = sets.map(team => team.map(set => {
		const text = Teams.export([set]);
		return /\nEVs: /.test(text) ? text : text.replace(/\n/, "\nEVs: 0 HP  \n");
	}).join(""));
	return {ots, teams: exported, lines: lines[0], decisions: decisions[0], lines_p2: lines[1],
		decisions_p2: decisions[1], winner: battle.winner || null};
}

const corpus = loadCorpus();
const rand = rng(SEED);
const battles = [];
for (let n = 0; n < BATTLES; n++) {
	const teams = [pick(rand, corpus), pick(rand, corpus)];
	battles.push(play(teams.map(t => t.text), n + 1, n % 2 === 0, rand));
}
fs.mkdirSync(path.dirname(OUT), {recursive: true});
fs.writeFileSync(OUT, zlib.gzipSync(JSON.stringify({format: FORMAT, battles})));
console.log(`${OUT}: ${battles.length} battles, ${battles.reduce((n, b) => n + b.decisions.length, 0)} decisions ` +
	`(${corpus.length} supported corpus teams)`);
