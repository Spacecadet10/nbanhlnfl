"""Count-only coverage of the ESPN month history (no score, corner value or rate is read): per competition and season,
matches of the main season type (leagues) or per UEFA stage, finals decided in regulation, and how many have a corner row."""
import sqlite3, sys, json
from collections import defaultdict
c = sqlite3.connect(sys.argv[1])
LEAGUES = ("eng.1", "esp.1", "ger.1", "ita.1", "fra.1")
main = {}
for comp, season, t, n in c.execute("SELECT competition_id, season, season_type_id, COUNT(*) FROM soccer_matches GROUP BY 1,2,3"):
    if comp in LEAGUES and n > main.get((comp, season), (None, -1))[1]:
        main[(comp, season)] = (t, n)
out = defaultdict(dict)
stats = {r[0] for r in c.execute("SELECT DISTINCT match_id FROM soccer_match_stats")}
for comp, season, t, stage, mid, status, dec in c.execute(
        "SELECT competition_id, season, season_type_id, stage, match_id, status, decided_in FROM soccer_matches "
        "WHERE competition_id IN ('eng.1','esp.1','ger.1','ita.1','fra.1','uefa.champions','uefa.europa')"):
    key = "main" if comp in LEAGUES and main[(comp, season)][0] == t else ("other_type" if comp in LEAGUES else stage)
    d = out[f"{comp}:{season}"].setdefault(key, {"listed": 0, "final_regulation": 0, "with_corner_row": 0, "final_other": 0})
    d["listed"] += 1
    if status == "final" and dec == "regulation":
        d["final_regulation"] += 1
        d["with_corner_row"] += mid in stats
    elif status == "final":
        d["final_other"] += 1
print(json.dumps(out, indent=1, sort_keys=True))
