// Every legal choice string for a side's current request, the way Showdown
// accepts them (shared by gen_fixtures.js and protocol_fixtures.js).
"use strict";

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
			// Revival Blessing: the slot picks a fainted Pokemon to revive.
			const reviving = req.forceSwitch[slot] && side.slotConditions[slot]?.revivalblessing;
			const opts = !req.forceSwitch[slot] ? [] : reviving ?
				side.pokemon.map((p, i) => i).filter(i => side.pokemon[i].fainted).map(i => ({s: `switch ${i + 1}`, rev: i})) :
				bench.map(i => ({s: `switch ${i + 1}`, sw: i}));
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
	// clearChoice's forced switches and passes, used up slot by slot as
	// chooseSwitch/choosePass do (a revive takes a switch if one is left).
	const need = req.forceSwitch ? req.forceSwitch.filter(Boolean).length : 0;
	const forcedOk = (x, y) => {
		let switches = Math.min(need, bench.length);
		let passes = need - switches;
		for (const o of [x, y]) {
			if (o.forced) {
				if (!passes) return false;
				passes--;
			} else if (o.rev !== undefined) {
				switches = Math.max(0, switches - 1);
			} else if (o.sw !== undefined) {
				if (!switches) return false;
				switches--;
			}
		}
		return switches === 0;
	};
	for (const x of slotOptions[0]) for (const y of slotOptions[1]) {
		if (x.sw !== undefined && x.sw === y.sw) continue;
		if (x.mega && y.mega) continue;
		if (req.forceSwitch && !forcedOk(x, y)) continue;
		out.push(`${x.s}, ${y.s}`);
	}
	return out;
}

module.exports = {legalChoices, validTargetLoc};
