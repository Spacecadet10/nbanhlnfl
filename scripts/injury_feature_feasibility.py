"""Feasibility study (read-only): can time-stamped NBA injury reports be parsed, matched to players and turned into a
pregame availability measure? Writes nothing to the project database and fits no model.

Sample: SEASON_DATES random regular-season dates per season (seed below). For each date D two reports are read:
  A = 11:30 PM Eastern on D-1 (before the existing midnight forecast cutoff), B = 5:30 PM Eastern on D.
Needs `pypdf` (not a project dependency; run from a scratch environment) and the seven sportsdataverse player box
CSVs in BOX_DIR. Usage: python scripts/injury_feature_feasibility.py <box_dir> <pdf_cache_dir> <out.json>
"""
import collections
import csv
import json
import random
import re
import sqlite3
import statistics
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pypdf

DB = "data/nba.sqlite"
BASE = "https://ak-static.cms.nba.com/referee/injury/"
ET = ZoneInfo("America/New_York")
SEED, SEASON_DATES, MIN_GAMES = 20261002, 4, 6
QUARTER_NAMES_FROM = "2025-12-22"
STATUS = "Out|Questionable|Doubtful|Probable|Available"
W = r"[A-Z][A-Za-z'’.\-]*"
ENTRY = re.compile(rf"((?:{W}[ ]{{1,4}}){{0,2}}{W})[ ]*,[ ]*({W}(?:[ ]{W})?)[ ]{{2,}}({STATUS})\b")
LOOSE = re.compile(rf"[A-Za-z'’.\-]+[ ]*,[ ]*[A-Za-z'’.\-]+.*?[ ]{{2,}}({STATUS})\b")
HEADER = re.compile(r"Injury\s+Report:\s+(\d\d)/(\d\d)/(\d\d)\s+(\d\d):(\d\d)\s+(AM|PM)")
DATE = re.compile(r"\b(\d\d)/(\d\d)/(\d{4})\b")
ABSENT = ("Out", "Doubtful")


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def report_name(day, hour, minute):
    """File name of the report published at hour:minute Eastern on `day` (legacy hourly files appear at :30)."""
    h12, ampm = (hour % 12) or 12, "AM" if hour < 12 else "PM"
    return f"Injury-Report_{day}_{h12:02d}_{minute:02d}{ampm}.pdf" if day >= QUARTER_NAMES_FROM else \
        f"Injury-Report_{day}_{h12:02d}{ampm}.pdf"


def fetch(name, cache):
    path = cache / name
    meta = cache / (name + ".json")
    if path.exists() and meta.exists():
        return json.loads(meta.read_text())
    try:
        with urllib.request.urlopen(BASE + name, timeout=30) as r:
            body, info = r.read(), {"status": r.status, "last_modified": r.headers.get("Last-Modified")}
        path.write_bytes(body)
    except urllib.error.HTTPError as e:
        info = {"status": e.code, "last_modified": None}
    meta.write_text(json.dumps(info))
    time.sleep(1.0)   # the host asks for a 1 s crawl delay
    return info


def parse(path, team_names):
    """Entries [{game_date, matchup, team, last, first, status}], teams not yet submitted, and reconciliation counts."""
    reader = pypdf.PdfReader(str(path))
    producer = str((reader.metadata or {}).get("/Producer", ""))
    reverse = "GemBox" in producer     # this producer's text comes out bottom-to-top within each page
    team_re = re.compile("|".join(r"\s+".join(map(re.escape, t.split())) for t in sorted(team_names, key=len, reverse=True)))
    lines, header = [], None
    for page in reader.pages:
        page_lines = page.extract_text(extraction_mode="layout").splitlines()
        header = header or HEADER.search(" ".join(page.extract_text().split()))
        lines += reversed(page_lines) if reverse else page_lines
    entries, pending, loose = [], [], 0
    game_date = matchup = team = None
    for line in lines:
        if "Page" in line and " of" in line and not ENTRY.search(line):
            continue
        d = DATE.search(line)
        if d:
            game_date = f"{d.group(3)}-{d.group(1)}-{d.group(2)}"
        m = re.search(r"\b([A-Z]{2,3}@[A-Z]{2,3})\b", line)
        if m:
            matchup = m.group(1)
        t = team_re.search(line)
        if t:
            team = " ".join(t.group(0).split())
        if "NOT YET SUBMITTED" in line.upper().replace("  ", " "):
            pending.append({"game_date": game_date, "matchup": matchup, "team": team})
        loose += bool(LOOSE.search(line))
        for e in ENTRY.finditer(line):
            entries.append({"game_date": game_date, "matchup": matchup, "team": team,
                            "last": re.sub(r"\s+", " ", e.group(1)), "first": e.group(2), "status": e.group(3)})
    published = None
    if header:
        mm, dd, yy, hh, mi, ap = header.groups()
        hour = int(hh) % 12 + (12 if ap == "PM" else 0)
        published = datetime(2000 + int(yy), int(mm), int(dd), hour, int(mi), tzinfo=ET).astimezone(timezone.utc)
    return {"entries": entries, "not_submitted": pending, "published": published, "producer": producer,
            "lines_with_a_name_and_status": loose, "pages": len(reader.pages)}


def main(box_dir, cache, out_path):
    cache.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(f"file:{DB}?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    team_name = {r["team_id"]: r["name"] for r in db.execute("SELECT team_id, name FROM teams") if r["team_id"].split(":")[1].isdigit()
                 and 1 <= int(r["team_id"].split(":")[1]) <= 30}
    name_team = {v: k for k, v in team_name.items()}
    games = collections.defaultdict(list)      # ET date -> games
    for g in db.execute("SELECT game_id, season, tipoff_at, home_team_id, away_team_id FROM games WHERE status='final' "
                        "AND season_type='regular'"):
        tip = datetime.strptime(g["tipoff_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        games[tip.astimezone(ET).strftime("%Y-%m-%d")].append({**dict(g), "tip": tip})

    # Player box scores: athlete -> appearances in date order; name -> athlete ids.
    apps, ids_by_name, rows_by_game = collections.defaultdict(list), collections.defaultdict(set), collections.defaultdict(list)
    for y in range(2020, 2027):
        with open(box_dir / f"player_box_{y}.csv", newline="") as f:
            for r in csv.DictReader(f):
                if r["athlete_id"] in ("", "NA"):
                    continue
                played = r["did_not_play"].lower() == "false" and r["minutes"] not in ("", "NA", "0", "0.0")
                rec = {"date": r["game_date"], "season": y, "team": "espn:" + r["team_id"], "game": "espn:" + r["game_id"],
                       "min": float(r["minutes"]) if played else 0.0, "played": played, "id": r["athlete_id"]}
                apps[r["athlete_id"]].append(rec)
                rows_by_game[rec["game"]].append(rec)
                ids_by_name[norm(r["athlete_display_name"])].add(r["athlete_id"])
    for v in apps.values():
        v.sort(key=lambda a: a["date"])

    def resolve(last, first):
        toks = last.split(" ")
        for i in range(len(toks)):
            ids = ids_by_name.get(norm(first + " ".join(toks[i:])))
            if ids:
                return ids
        return set()

    def expected_minutes(pid, day, season):
        """Mean minutes over the player's last 10 games played before `day`: this season if at least 3, else the previous one."""
        cur = [a["min"] for a in apps[pid] if a["played"] and a["season"] == season and a["date"] < day][-10:]
        if len(cur) >= 3:
            return statistics.mean(cur), "season"
        prev = [a["min"] for a in apps[pid] if a["played"] and a["season"] == season - 1][-10:]
        if prev:
            return statistics.mean(prev), "previous_season"
        return (statistics.mean(cur), "season_short") if cur else (0.0, "no_history")

    rng = random.Random(SEED)
    sample = []
    for season in range(2020, 2027):
        days = sorted(d for d, gs in games.items() if gs[0]["season"] == season and len(gs) >= MIN_GAMES)
        first = datetime.strptime(days[0], "%Y-%m-%d")
        eligible = [d for d in days if datetime.strptime(d, "%Y-%m-%d") - first >= timedelta(days=30)]
        sample += [(season, d) for d in sorted(rng.sample(eligible, SEASON_DATES))]

    out = {"seed": SEED, "sample_dates": [d for _, d in sample], "reports": [], "team_games": []}
    totals = collections.Counter()
    for season, day in sample:
        prev_day = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        reports = {}
        for label, (rday, hour) in {"A_night_before": (prev_day, 23), "B_game_day": (day, 17)}.items():
            name = report_name(rday, hour, 30)
            info = fetch(name, cache)
            rec = {"date": day, "season": season, "timing": label, "file": name, "status": info["status"]}
            if info["status"] == 200:
                p = parse(cache / name, set(name_team))
                named = datetime.strptime(f"{rday} {hour}:30", "%Y-%m-%d %H:%M").replace(tzinfo=ET).astimezone(timezone.utc)
                modified = parsedate_to_datetime(info["last_modified"]) if info["last_modified"] else None
                todays = [e for e in p["entries"] if e["game_date"] == day]
                rec.update({
                    "producer": p["producer"][:24], "pages": p["pages"], "entries": len(p["entries"]),
                    "lines_with_a_name_and_status": p["lines_with_a_name_and_status"],
                    "header_time_utc": p["published"].strftime("%Y-%m-%dT%H:%M:%SZ") if p["published"] else None,
                    "header_matches_expected_time": bool(p["published"]) and p["published"] == named,
                    "minutes_last_modified_after_header": round((modified - p["published"]).total_seconds() / 60, 2)
                    if modified and p["published"] else None,
                    "entries_for_sample_date": len(todays),
                    "entries_without_team_or_date": sum(not e["team"] or not e["game_date"] for e in p["entries"]),
                    "unknown_team_names": sorted({e["team"] for e in p["entries"] if e["team"] and e["team"] not in name_team})})
                reports[label] = (p, rec)
            out["reports"].append(rec)

        for g in games[day]:
            for team in (g["home_team_id"], g["away_team_id"]):
                box = [r for r in rows_by_game[g["game_id"]] if r["team"] == team]
                played_ids = {r["id"] for r in box if r["played"]}
                tg = {"date": day, "season": season, "game_id": g["game_id"], "team": team_name[team]}
                for label, (p, rec) in reports.items():
                    after_report = g["tip"] > p["published"] if p["published"] else False
                    mine = [e for e in p["entries"] if e["game_date"] == day and e["team"] == team_name[team]]
                    not_submitted = any(n["game_date"] == day and n["team"] == team_name[team] for n in p["not_submitted"])
                    listed_minutes = absent_played = absent_n = 0.0
                    for e in mine:
                        ids = resolve(e["last"], e["first"])
                        scope = "unresolved" if not ids else "ambiguous" if len(ids) > 1 else "resolved"
                        totals[f"{label}:mapping:{scope}"] += 1
                        if scope != "resolved":
                            if e["status"] in ABSENT:
                                totals[f"{label}:absent_unresolved"] += 1
                            continue
                        pid = next(iter(ids))
                        mins, basis = expected_minutes(pid, day, season)
                        totals[f"{label}:minutes_basis:{basis}"] += 1
                        near = [a for a in apps[pid] if abs((datetime.strptime(a["date"], "%Y-%m-%d")
                                                             - datetime.strptime(day, "%Y-%m-%d")).days) <= 7]
                        if near:
                            totals[f"{label}:team_check:{'agree' if any(a['team'] == team for a in near) else 'disagree'}"] += 1
                        if e["status"] in ABSENT:
                            listed_minutes += mins
                            absent_n += 1
                            absent_played += pid in played_ids
                    tg[label] = {"usable_before_tipoff": after_report, "team_not_yet_submitted": not_submitted,
                                 "entries": len(mine), "listed_absent": int(absent_n),
                                 "listed_absent_who_played": int(absent_played),
                                 "expected_minutes_listed_absent": round(listed_minutes, 1)}
                # Audit only (uses who actually played): rotation players who missed the game.
                roster = {}
                for pid, alist in apps.items():
                    hist = [a for a in alist if a["season"] == season and a["date"] < day]
                    if hist and hist[-1]["team"] == team and hist[-1]["date"] >= (datetime.strptime(day, "%Y-%m-%d")
                                                                                 - timedelta(days=14)).strftime("%Y-%m-%d"):
                        mins, _ = expected_minutes(pid, day, season)
                        if mins >= 10 and sum(a["played"] for a in hist[-5:]) >= 1:
                            roster[pid] = mins
                missed = {pid: m for pid, m in roster.items() if pid not in played_ids}
                tg["audit_rotation_minutes_missed"] = round(sum(missed.values()), 1)
                for label, (p, rec) in reports.items():
                    listed = set()
                    for e in p["entries"]:
                        if e["game_date"] == day and e["team"] == team_name[team] and e["status"] in ABSENT:
                            ids = resolve(e["last"], e["first"])
                            if len(ids) == 1:
                                listed |= ids
                    tg[label]["audit_missed_minutes_already_listed"] = round(sum(m for pid, m in missed.items() if pid in listed), 1)
                out["team_games"].append(tg)
    out["totals"] = dict(totals)
    json.dump(out, open(out_path, "w"), indent=1)
    print("reports", collections.Counter((r["timing"], r["status"]) for r in out["reports"]), "team-games", len(out["team_games"]))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
