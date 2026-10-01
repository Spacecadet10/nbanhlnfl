import pypdf, re, csv, unicodedata, collections, json, sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")
STATUS = "Out|Questionable|Doubtful|Probable|Available"
W = r"[A-Z][A-Za-z'’.\-]*"
ENTRY = re.compile(rf"((?:{W}[ ]{{1,4}}){{0,2}}{W})[ ]*,[ ]*({W}(?:[ ]{W})?)[ ]{{2,}}({STATUS})\b")
def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)
db = sqlite3.connect("file:/Users/andry/Desktop/nba-predictor-sprint1/data/nba.sqlite?mode=ro&immutable=1", uri=True)
tip = {r[0]: r[1] for r in db.execute("select game_id, tipoff_at from games")}
box = {y: list(csv.DictReader(open(f"player_box_{y}.csv"))) for y in range(2020, 2027)}
names_by_season = {y: collections.defaultdict(set) for y in box}
for y, rows in box.items():
    for r in rows:
        names_by_season[y][norm(r["athlete_display_name"])].add(r["athlete_id"])
files = {"Injury-Report_2019-12-01_05PM.pdf": (2020, "2019-12-01 17:30"), "Injury-Report_2023-01-10_05PM.pdf": (2023, "2023-01-10 17:30"),
         "Injury-Report_2025-12-01_05PM.pdf": (2026, "2025-12-01 17:30"), "Injury-Report_2026-03-01_05_00PM.pdf": (2026, "2026-03-01 17:00")}
out = {}
for f, (season, when) in files.items():
    report_at = datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=ET).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    day = when[:10]
    text = "\n".join(p.extract_text(extraction_mode="layout") for p in pypdf.PdfReader(f).pages)
    entries = [(re.sub(r"\s+", " ", m.group(1)), m.group(2), m.group(3)) for m in ENTRY.finditer(text)]
    def resolve(l, fn):
        toks = l.split(" ")
        for scope, seasons in (("same_season", [season]), ("other_season", [y for y in box if y != season])):
            for i in range(len(toks)):
                k = norm(fn + " ".join(toks[i:]))
                ids = set().union(*[names_by_season[y].get(k, set()) for y in seasons])
                if ids:
                    return scope, k, ids
        return "unresolved", None, set()
    cats = collections.Counter(); unresolved = []; ambiguous = []
    rows = box[season]
    by_player = collections.defaultdict(list)
    for r in rows:
        by_player[norm(r["athlete_display_name"])].append(r)
    check = collections.defaultdict(lambda: [0, 0]); skipped_started = skipped_no_game = 0
    for l, fn, st in entries:
        scope, k, ids = resolve(l, fn)
        cats[scope] += 1
        if scope == "unresolved":
            unresolved.append(f"{l}, {fn} ({st})")
        if len(ids) > 1:
            ambiguous.append(f"{l}, {fn}")
        if scope != "same_season":
            continue
        # Team = the team of this player's box row closest in date; then that team's game on the report date, if any.
        nearest = min(by_player[k], key=lambda r: abs((datetime.strptime(r["game_date"], "%Y-%m-%d") - datetime.strptime(day, "%Y-%m-%d")).days))
        team_games = {r["game_id"] for r in rows if r["team_id"] == nearest["team_id"] and r["game_date"] == day}
        if not team_games:
            skipped_no_game += 1
            continue
        gid = next(iter(team_games))
        if tip.get("espn:" + gid, "") <= report_at:   # game had already started when the report was published
            skipped_started += 1
            continue
        played = any(r["game_id"] == gid and r["did_not_play"].lower() == "false" and r["minutes"] not in ("", "NA", "0", "0.0")
                     for r in by_player[k])
        check[st][0] += 1
        check[st][1] += played
    out[f] = {"report_published_utc": report_at, "entries": len(entries), "mapping": dict(cats), "unresolved": unresolved,
              "ambiguous_names": ambiguous, "pregame_check": {s: {"listed": a, "played": b} for s, (a, b) in check.items()},
              "excluded_game_already_started": skipped_started, "excluded_team_not_playing_that_day": skipped_no_game}
    print("==", f, json.dumps(out[f], indent=0)[:1500])
json.dump(out, open("parse_check2.json", "w"), indent=1)
tot = collections.Counter(); chk = collections.defaultdict(lambda: [0, 0])
for v in out.values():
    tot.update(v["mapping"])
    for s, c in v["pregame_check"].items():
        chk[s][0] += c["listed"]; chk[s][1] += c["played"]
print("TOTAL mapping", dict(tot), "pregame check", dict(chk))
