"""Proposes the ESPN national-team -> history team-name map (app/intl_teams.json). Run from backend/ with PYTHONPATH=.:
    NBA_DATA_DIR=<store dir> python ../docs/evidence/international-2026-11-08/build_team_map.py <out.json>
Rule: an ESPN name equal to a history team name (exact string) is proposed; everything else is listed UNRESOLVED with
history names sharing a word, for a human to decide (alias or none). Nothing is matched at run time."""
import json
import sys

from app import db, intl_live

conn = db.connect_readonly("soccer")
hist = sorted({n for m in intl_live.history() for n in (m.home, m.away)})
names = set(hist)
espn = {}
for r in intl_live.espn_rows(conn, "1900-01-01"):
    espn[r["home_team_id"]] = r["home_name"]
    espn[r["away_team_id"]] = r["away_name"]
mapping, unresolved = {}, []
for team, name in sorted(espn.items(), key=lambda x: x[1]):
    if name in names:
        mapping[team] = name
    else:
        words = {w for w in name.lower().replace("-", " ").split() if len(w) > 2}
        near = [h for h in hist if words & {w for w in h.lower().replace("-", " ").split()}][:6]
        unresolved.append([team, name, near])
json.dump({"mapping": mapping, "unresolved": unresolved}, open(sys.argv[1], "w"), indent=1, ensure_ascii=False)
print(json.dumps({"espn_teams": len(espn), "exact": len(mapping), "unresolved": len(unresolved)}))
