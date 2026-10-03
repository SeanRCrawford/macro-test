// Export Showdown's Champions-mod data to selfplay/data/dex.json.
//
// Showdown is the rules authority for the format the bot plays, so the engine's
// static data comes from its "champions" mod (the base gen9 data with the
// Champions changes merged in), not from Scarlet/Violet data.
//
//   node selfplay/tools/showdown/export_dex.js /path/to/pokemon-showdown
//
// The Showdown checkout must be built (`node build`). Functions (event
// handlers) can't be serialised, so each entry instead lists the names of its
// handlers under "handlers": that's where the engine needs hand-written code.
"use strict";
const fs = require("fs");
const path = require("path");
const {execSync} = require("child_process");

const showdown = path.resolve(process.argv[2] || process.env.SHOWDOWN || "");
if (!fs.existsSync(path.join(showdown, "dist", "sim"))) {
	console.error("usage: node export_dex.js /path/to/built/pokemon-showdown");
	process.exit(1);
}
const {Dex} = require(path.join(showdown, "dist", "sim"));
const dex = Dex.mod("champions");
const data = dex.data;

const OUT = path.join(__dirname, "..", "..", "data", "dex.json");
const STATS = ["hp", "atk", "def", "spa", "spd", "spe"];
const DROP = new Set(["desc", "shortDesc", "contestType", "spritenum", "color", "eggGroups",
	"heightm", "evos", "prevo", "evoLevel", "evoType", "evoCondition", "evoItem", "evoMove",
	"evoRegion", "canHatch", "genderRatio", "gender", "mother", "tags", "cosmeticFormes",
	"formeOrder", "maxHP", "canGigantamax", "cannotDynamax", "gmaxUnreleased", "zMove",
	"maxMove", "isZ", "zMoveEffect", "zMoveBoost", "zMovePower", "realMove"]);

// Plain data with function-valued keys replaced by a sorted "handlers" list.
function plain(entry) {
	const out = {};
	const handlers = [];
	for (const [k, v] of Object.entries(entry)) {
		if (DROP.has(k)) continue;
		if (typeof v === "function") handlers.push(k);
		else if (v && typeof v === "object" && !Array.isArray(v)) {
			const inner = {};
			for (const [ik, iv] of Object.entries(v)) {
				if (typeof iv === "function") handlers.push(`${k}.${ik}`);
				else inner[ik] = iv;
			}
			out[k] = inner;
		} else out[k] = v;
	}
	if (handlers.length) out.handlers = handlers.sort();
	return out;
}

function sortedObject(obj) {
	return Object.fromEntries(Object.keys(obj).sort().map(k => [k, obj[k]]));
}

// num <= 0 is MissingNo., CAP fakemons and similar.
const species = {};
for (const [id, s] of Object.entries(data.Pokedex)) {
	if (!s.baseStats || !(s.num > 0)) continue;
	const e = plain(s);
	e.baseStats = STATS.map(k => s.baseStats[k]);
	species[id] = e;
}
const moves = {};
for (const [id, m] of Object.entries(data.Moves)) {
	if (m.num > 0 || id === "struggle") moves[id] = plain(m);
}
const items = {};
for (const [id, it] of Object.entries(data.Items)) {
	if (it.num > 0) items[id] = plain(it);
}
const abilities = {};
for (const [id, a] of Object.entries(data.Abilities)) {
	if (a.num > 0) abilities[id] = plain(a);
}
const natures = {};
for (const [id, n] of Object.entries(data.Natures)) {
	natures[id] = {plus: n.plus || null, minus: n.minus || null};
}
// damageTaken codes: 0 neutral, 1 weak (2x), 2 resist (0.5x), 3 immune.
// TypeChart also holds Stellar and non-type keys (brn, sandstorm, ...): keep
// only the 18 real types.
const types = Object.keys(data.TypeChart).filter(t => t !== "stellar")
	.map(t => t[0].toUpperCase() + t.slice(1)).sort();
const typechart = {};
for (const d of types) {
	const taken = data.TypeChart[d.toLowerCase()].damageTaken;
	typechart[d] = Object.fromEntries(types.map(a => [a, taken[a]]));
}

const commit = execSync("git rev-parse HEAD", {cwd: showdown}).toString().trim();
const out = {
	source: `pokemon-showdown ${commit} mod champions`,
	types,
	typechart,
	natures: sortedObject(natures),
	species: sortedObject(species),
	moves: sortedObject(moves),
	items: sortedObject(items),
	abilities: sortedObject(abilities),
};
fs.writeFileSync(OUT, JSON.stringify(out, null, 1) + "\n");
console.log(`wrote ${OUT}: ${Object.keys(species).length} species, ${Object.keys(moves).length} moves, ` +
	`${Object.keys(items).length} items, ${Object.keys(abilities).length} abilities`);
