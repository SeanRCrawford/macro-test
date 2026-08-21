# My teams

Drop a Showdown-export pokepaste here as a `.txt` file (one per team) and it
shows up everywhere a saved team does -- Lead/Back, Battle Viewer, Vs Team,
`tools/lead_sweep.py --team "..."`, `counter_table.py --team "..."` -- the
same way `data/teams/*.txt` already does. This folder is for your own teams;
`data/teams/` is the shared/built-in library.

Format: paste pokepast.es's "Export" text, blank line between each of the
six Pokemon (see `data/teams/sand.txt` for a worked example). The file name
becomes the team's display name (underscores/hyphens become spaces,
title-cased) -- `my_gholdengo_team.txt` shows up as "My Gholdengo Team".

You can also paste a pokepaste directly into the Streamlit app (Vs Team's
"Our side" -> "Paste a pokepaste") and save it here from there, without
leaving the browser.
