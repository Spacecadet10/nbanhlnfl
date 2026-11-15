# Is a pregame goalie listing exposed by the free NHL API? 1 schedule request + 1 landing request for an upcoming game.
import requests, json, time
s = requests.get("https://api-web.nhle.com/v1/schedule/2026-10-07", timeout=20); print("schedule", s.status_code)
games = [g for d in s.json().get("gameWeek", []) for g in d.get("games", []) if g.get("gameState") in ("FUT","PRE")]
print("upcoming games found:", len(games))
if games:
    g = games[0]; print("game", g["id"], g.get("gameType"), g.get("startTimeUTC"), g["awayTeam"]["abbrev"], "@", g["homeTeam"]["abbrev"])
    r = requests.get(f"https://api-web.nhle.com/v1/gamecenter/{g['id']}/landing", timeout=20); print("landing", r.status_code)
    j = r.json(); print("top-level keys:", list(j.keys()))
    m = j.get("matchup", {}); print("matchup keys:", list(m.keys()))
    gc = m.get("goalieComparison") or {}
    print("goalieComparison:", json.dumps(gc)[:900])
