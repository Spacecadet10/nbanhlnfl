"""Proposes the ESPN team -> openfootball club map for the live soccer forecasts (app/soccer_live_clubs.json).

Run once from backend/ on a soccer store holding the openfootball history and the ESPN 2026-27 season:
    NBA_DATA_DIR=<store dir> ../.venv/bin/python -I ../docs/evidence/soccer-live-2026-11-08/build_club_map.py <out.json>
(-I is not usable with the app import; run with PYTHONPATH=. from backend/.)

Proposal rule (for a human to review; nothing is matched at run time):
- league clubs: candidates are the clubs of that league in openfootball 2024-25 .. 2026-27 schedules; a proposal needs
  the ESPN name and exactly one candidate to have the same comparison form (soccer_clubs._key) or one form to contain
  the other's words entirely, with a unique result;
- UEFA clubs: an ESPN team already mapped through a league keeps that club; otherwise candidates are every UEFA club id
  of 2020-21 .. 2025-26, same rule;
- anything else is listed as UNRESOLVED with the nearest candidates, to be decided by hand (or as a new club).
"""
import json
import sys
from collections import defaultdict

from app import db, soccer_clubs, soccer_live

conn = db.connect_readonly("soccer")
rows = soccer_live.season_rows(conn, (*soccer_live.LAUNCH, *soccer_live.INPUT_ONLY))
espn = defaultdict(dict)                                   # competition -> {team_id: name}
for r in rows:
    espn[r["competition_id"]][r["home_team_id"]] = r["home_name"]
    espn[r["competition_id"]][r["away_team_id"]] = r["away_name"]


def candidates(competitions, seasons):
    q = ",".join("?" * len(competitions))
    out = set()
    for r in conn.execute(f"SELECT DISTINCT home_club_id, away_club_id FROM soccer_hist_matches WHERE competition_id IN ({q}) "
                          f"AND season BETWEEN ? AND ?", (*competitions, *seasons)):
        out.update(r)
    return out


def words(name):
    return set(soccer_clubs._key(name).split())


def propose(name, cands):
    w = words(name)
    exact = [c for c in cands if words(c.split(":", 1)[1]) == w and w]
    if len(exact) == 1:
        return exact[0], "same comparison form"
    contained = [c for c in cands if w and (words(c.split(":", 1)[1]) <= w or w <= words(c.split(":", 1)[1]))
                 and words(c.split(":", 1)[1])]
    if len(contained) == 1:
        return contained[0], "word subset (review)"
    return None, f"{len(exact) or len(contained)} candidates: {sorted(exact or contained)[:4]}"


mapping, review, unresolved = {}, [], []
for comp in soccer_clubs.LEAGUE_COUNTRY:
    cands = candidates((comp,), (2024, 2026))
    for team, name in sorted(espn.get(comp, {}).items()):
        club, why = propose(name, cands)
        if club:
            mapping[team] = club
            review.append([comp, team, name, club, why])
        else:
            unresolved.append([comp, team, name, why])
uefa = candidates(("uefa.champions", "uefa.europa"), (2020, 2025)) | candidates(tuple(soccer_clubs.LEAGUE_COUNTRY), (2024, 2026))
for comp in ("uefa.champions", "uefa.europa"):
    for team, name in sorted(espn.get(comp, {}).items()):
        if team in mapping:
            continue
        club, why = propose(name, uefa)
        if club:
            mapping[team] = club
            review.append([comp, team, name, club, why])
        else:
            unresolved.append([comp, team, name, why])
json.dump({"mapping": mapping, "review": review, "unresolved": unresolved}, open(sys.argv[1], "w"), indent=1,
          ensure_ascii=False)
print(json.dumps({"mapped": len(mapping), "needs_review": sum(1 for r in review if r[4] != "same comparison form"),
                  "unresolved": len(unresolved)}))
