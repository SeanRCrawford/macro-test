"""
Level 50 stat calculator.

House rule for this format (Champions M-B, confirmed by user): EVs are NOT
the standard /4-divided investment. Instead, each EV "point" in the sheet
is a flat +1 added directly to the stat, applied AFTER the normal Lv50
calculation (base/IV/level/nature). i.e.:

    normal_stat = standard_lv50_formula(base, iv=31, level=50, nature_mult, ev=0)
    final_stat  = normal_stat + ev_points   (flat, no /4, no nature on the flat part)
"""

LEVEL = 50
DEFAULT_IV = 31


def calc_stat(base, iv, ev_points, level, is_hp, nature_mult=1.0):
    """ev_points here is a FLAT bonus, added post-formula (Champions M-B house rule)."""
    inner = (2 * base + iv) * level // 100
    if is_hp:
        if base == 1:  # Shedinja edge case, not relevant here but safe
            return 1
        normal = inner + level + 10
    else:
        normal = int((inner + 5) * nature_mult)
    return normal + ev_points


def compute_stats(base_stats: dict, nature: dict, evs: dict, ivs: dict | None = None,
                   level: int = LEVEL) -> dict:
    """
    base_stats: {'hp','atk','def','spa','spd','spe'}
    nature: showdown nature dict e.g. {'atk':1.1,'def':1,'spa':0.9,'spd':1,'spe':1}
    evs: {'hp','atk','def','spa','spd','spe'} -- flat bonus points (Champions M-B rule)
    ivs: optional per-stat IV override, defaults to 31 all
    """
    ivs = ivs or {k: DEFAULT_IV for k in ["hp", "atk", "def", "spa", "spd", "spe"]}
    out = {}
    for stat in ["hp", "atk", "def", "spa", "spd", "spe"]:
        is_hp = stat == "hp"
        mult = 1.0 if is_hp else nature.get(stat, 1.0)
        out[stat] = calc_stat(base_stats[stat], ivs[stat], evs[stat], level, is_hp, mult)
    return out


if __name__ == "__main__":
    from species_data import build_merged_dataset, load_showdown_static

    merged, _, _, natures, _ = build_merged_dataset()
    garchomp = merged["Garchomp"]
    nature = natures[garchomp["nature"].lower()]
    stats = compute_stats(garchomp["base_stats"], nature, garchomp["evs"])
    print("Garchomp (Jolly, 2/32/0/0/0/32 EVs) @ Lv50:")
    print(stats)
    # Champions M-B rule, hand-verified: normal (0 EV) Jolly Garchomp @ Lv50
    # is HP 183 / Atk 150 / Def 115 / SpA 90 / SpD 105 / Spe 134, then flat
    # EVs (hp2/atk32/spe32) add directly -> HP 185, Atk 182, Def 115, SpA 90, SpD 105, Spe 166
