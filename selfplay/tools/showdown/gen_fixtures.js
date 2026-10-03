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

const stats = statFixtures();
console.log(`${write("stats.json", stats)}: ${stats.length} cases`);
