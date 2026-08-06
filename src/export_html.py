"""
Self-contained HTML report -- double-click to open in a browser, no server
needed. Gives a visual, readable version of the same data as the Excel
export's Gameplan sheets: a turn-by-turn playback of each recommended
composition's battle against its team's toughest lead.
"""
import html

STYLE = """
<style>
body { font-family: -apple-system, Segoe UI, Arial, sans-serif; background: #f4f6f8; margin: 0; padding: 24px; color: #1a1a1a; }
h1 { font-size: 22px; margin-bottom: 4px; }
.subtitle { color: #666; margin-bottom: 24px; }
table.summary { border-collapse: collapse; width: 100%; margin-bottom: 32px; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
table.summary th { background: #2C3E50; color: white; padding: 10px; text-align: left; font-size: 13px; }
table.summary td { padding: 10px; border-bottom: 1px solid #eee; font-size: 13px; }
table.summary tr.win td { background: #eafaf1; }
table.summary tr.loss td { background: #fdedec; }
.team-section { background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 20px; overflow: hidden; }
.team-header { padding: 14px 18px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; background: #34495e; color: white; }
.team-header:hover { background: #2c3e50; }
.badge { padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: bold; }
.badge.win { background: #27ae60; }
.badge.loss { background: #c0392b; }
.turn-log { padding: 16px 20px; display: none; }
.turn-log.open { display: block; }
.turn-block { margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px dashed #ddd; }
.turn-num { font-weight: bold; color: #2C3E50; font-size: 13px; margin-bottom: 6px; }
.event-row { font-size: 13px; padding: 3px 0 3px 12px; }
.dmg { color: #c0392b; font-weight: 600; }
.eff-super { color: #c0392b; }
.eff-not { color: #7f8c8d; }
.faint { color: #922b21; font-weight: bold; }
.field { color: #7d3c98; font-style: italic; }
.hp-bar-wrap { display: inline-block; width: 80px; height: 8px; background: #ecf0f1; border-radius: 4px; overflow: hidden; vertical-align: middle; margin-left: 6px; }
.hp-bar { height: 100%; background: #27ae60; }
.meta { color: #888; font-size: 12px; margin-top: 4px; }
</style>
"""

TOGGLE_JS = """
<script>
function toggleTeam(id) {
  const el = document.getElementById(id);
  el.classList.toggle('open');
}
</script>
"""


def _event_line(ev):
    t = ev.get("event")
    actor = html.escape(str(ev.get("actor", ev.get("out", ""))))
    if t == "leads":
        return f'<div class="event-row field">Leads: {" / ".join(ev["p1"])} vs {" / ".join(ev["p2"])}' + \
               (f' (weather: {ev["weather_after"]})' if ev.get("weather_after") else "") + '</div>'
    if t == "switch":
        mega = " (Mega Evolved!)" if ev.get("mega_evolved") else ""
        return f'<div class="event-row field">{ev["side"]}: {actor} → {html.escape(ev["incoming"])}{mega}</div>'
    if t == "move_damage":
        eff = ev.get("type_eff", 1.0)
        eff_class = "eff-super" if eff > 1 else ("eff-not" if eff < 1 else "")
        eff_txt = f'<span class="{eff_class}">({eff}x)</span>' if eff != 1.0 else ""
        target = html.escape(ev.get("target", ""))
        hp_after, hp_max = ev.get("target_hp_after", 0), ev.get("target_max_hp", 1)
        pct = max(0, min(100, 100 * hp_after / hp_max)) if hp_max else 0
        faint = ' <span class="faint">[FAINTED]</span>' if ev.get("fainted") else ""
        return (f'<div class="event-row">{actor} uses <b>{html.escape(ev.get("move",""))}</b> on {target}: '
                f'<span class="dmg">{ev.get("damage",0)} dmg</span> {eff_txt} → {hp_after}/{hp_max} HP'
                f'<span class="hp-bar-wrap"><span class="hp-bar" style="width:{pct}%"></span></span>{faint}</div>')
    if t == "flinched":
        return f'<div class="event-row">{actor} flinched and couldn\'t move!</div>'
    if t == "protect":
        return f'<div class="event-row">{actor} protects itself!</div>'
    if t in ("wide_guard", "quick_guard"):
        return f'<div class="event-row">{actor} sets up {t.replace("_"," ").title()}!</div>'
    if t == "trick_room":
        state = "activated" if ev.get("trick_room_now") else "ended"
        return f'<div class="event-row field">{actor} {state} Trick Room!</div>'
    if t == "tailwind":
        return f'<div class="event-row field">{actor}\'s side gets Tailwind!</div>'
    return f'<div class="event-row">{html.escape(str(ev))}</div>'


def build_html(team_reports: dict, our_pool: list, out_path: str, mode: str):
    rows = []
    sections = []
    for i, (team_name, rep) in enumerate(team_reports.items()):
        best = rep["all_combos"][0]
        wins = sum(1 for _, w, _ in rep["all_combos"] if w == "p1")
        is_win = best[1] == "p1"
        rows.append(f'<tr class="{"win" if is_win else "loss"}">'
                    f'<td>{html.escape(team_name)}</td>'
                    f'<td>{" / ".join(rep["toughest_lead"])}</td>'
                    f'<td>{" / ".join(best[0][:2])}</td>'
                    f'<td>{" / ".join(best[0][2:])}</td>'
                    f'<td><span class="badge {"win" if is_win else "loss"}">{best[1].upper()}</span></td>'
                    f'<td>{best[2]}</td>'
                    f'<td>{wins}/{len(rep["all_combos"])}</td></tr>')

        section_id = f"team{i}"

        def _turns_html(events):
            by_turn = {}
            for ev in events:
                by_turn.setdefault(ev.get("turn", 0), []).append(ev)
            return "".join(
                f'<div class="turn-block"><div class="turn-num">Turn {t}</div>'
                + "".join(_event_line(ev) for ev in evs) + "</div>"
                for t, evs in sorted(by_turn.items())
            )

        per_lead = rep.get("per_lead") or {}
        if per_lead:
            # Comprehensive mode: one collapsible block per possible enemy lead.
            sub = []
            for j, (enemy_lead, d) in enumerate(per_lead.items()):
                b = d["best"]
                sid = f"{section_id}_{j}"
                won = b[1] == "p1"
                sub.append(f'''
<div class="team-section" style="margin:8px 0 8px 18px;">
  <div class="team-header" style="background:#4a6076;" onclick="toggleTeam('{sid}')">
    <span>vs {html.escape(" / ".join(enemy_lead))} &nbsp;→&nbsp; our lead {html.escape(" / ".join(b[0][:2]))}, back {html.escape(" / ".join(b[0][2:]))}</span>
    <span class="badge {"win" if won else "loss"}">{b[1].upper()} (turn {b[2]}) · {d["wins"]}/{len(d["all_combos"])} combos</span>
  </div>
  <div class="turn-log" id="{sid}">{_turns_html(d["gameplan_events"])}</div>
</div>''')
            body = "".join(sub)
            sections.append(f'''
<div class="team-section">
  <div class="team-header" onclick="toggleTeam('{section_id}')">
    <span>{html.escape(team_name)} — all {len(per_lead)} enemy leads</span>
    <span class="badge {"win" if is_win else "loss"}">click to expand</span>
  </div>
  <div class="turn-log" id="{section_id}">{body}</div>
</div>''')
        else:
            sections.append(f'''
<div class="team-section">
  <div class="team-header" onclick="toggleTeam('{section_id}')">
    <span>{html.escape(team_name)} — lead {" / ".join(best[0][:2])}, back {" / ".join(best[0][2:])}</span>
    <span class="badge {"win" if is_win else "loss"}">{best[1].upper()} (turn {best[2]})</span>
  </div>
  <div class="turn-log" id="{section_id}">{_turns_html(rep["gameplan_events"])}</div>
</div>''')

    doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>VGC Champions M-B — Search Results</title>{STYLE}</head>
<body>
<h1>VGC Champions M-B — Team/Lead/Back Search Results</h1>
<div class="subtitle">Pool: {html.escape(", ".join(our_pool))} &nbsp;|&nbsp; Mode: {html.escape(mode)}</div>

<table class="summary">
<tr><th>Enemy Team</th><th>Their Toughest Lead</th><th>Our Lead</th><th>Our Back</th>
<th>Result</th><th>Turns</th><th>Combos That Win</th></tr>
{"".join(rows)}
</table>

<h2 style="font-size:16px;color:#2C3E50;">Turn-by-turn gameplans (click a team to expand)</h2>
{"".join(sections)}

{TOGGLE_JS}
</body></html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path
