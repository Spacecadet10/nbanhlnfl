"""Read-only step-1 probe: ESPN /nba/summary coverage of possession stats. Writes nothing to the project DB."""
import gzip, hashlib, json, random, sqlite3, time, sys
import httpx
DB = "/Users/andry/Desktop/nba-predictor-sprint1/data/nba.sqlite"
OUT = sys.argv[1]
URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
rng = random.Random(20260930)
NEED = ("totalTurnovers", "offensiveRebounds", "defensiveRebounds", "fieldGoalsMade-fieldGoalsAttempted",
        "freeThrowsMade-freeThrowsAttempted")
results = []
for season in range(2020, 2027):
    rows = conn.execute("""SELECT g.game_id, g.tipoff_at, g.season_type, g.home_score, g.away_score,
        hs.fga h_fga, as_.fga a_fga FROM games g
        LEFT JOIN team_game_stats hs ON hs.game_id=g.game_id AND hs.is_home=1
        LEFT JOIN team_game_stats as_ ON as_.game_id=g.game_id AND as_.is_home=0
        WHERE g.season=? AND g.status='final' AND g.season_type IN ('regular','play_in','postseason')""", (season,)).fetchall()
    for r in rng.sample(rows, 30):
        eid = r["game_id"].split(":")[1]
        t0 = time.time()
        try:
            resp = httpx.get(URL, params={"event": eid}, timeout=30)
            status, body = resp.status_code, resp.content
        except Exception as e:
            status, body = f"ERR {type(e).__name__}", b""
        rec = {"season": season, "game_id": r["game_id"], "type": r["season_type"], "status": status,
               "bytes": len(body), "gz_bytes": len(gzip.compress(body)) if body else 0, "secs": round(time.time()-t0, 2)}
        if status == 200:
            j = json.loads(body)
            teams = j.get("boxscore", {}).get("teams", [])
            rec["teams"] = len(teams)
            ok = len(teams) == 2
            for t in teams:
                stats = {s.get("name"): s.get("displayValue") for s in t.get("statistics", [])}
                side = t.get("homeAway")
                rec[f"{side}_stats"] = {k: stats.get(k) for k in NEED}
                ok = ok and all(stats.get(k) not in (None, "", "--") for k in NEED)
                fga = stats.get("fieldGoalsMade-fieldGoalsAttempted")
                db_fga = r["h_fga"] if side == "home" else r["a_fga"]
                if fga and db_fga is not None:
                    rec[f"{side}_fga_matches_scoreboard"] = int(fga.split("-")[1]) == db_fga
            rec["complete"] = ok
            comp = (j.get("header", {}).get("competitions") or [{}])[0]
            rec["timestamps"] = {"header.date": comp.get("date"), "wallclockAvailable": j.get("wallclockAvailable"),
                                 "meta": j.get("meta")}
            rec["sha256"] = hashlib.sha256(body).hexdigest()
        results.append(rec)
        time.sleep(1.0)
json.dump(results, open(OUT, "w"), indent=1)
print("done", len(results))
