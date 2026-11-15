"""Read-only feasibility probe for player-availability data (audit 2026-10-01). Writes nothing to the project database.

Part 1 (this script): the NBA's official injury report PDFs at ak-static.cms.nba.com/referee/injury/.
HEAD requests only: which report times exist on sampled game dates, per season, in both file-name formats, and how the
server's Last-Modified time compares with the time in the file name.

Usage: python3 scripts/probe_availability_sources.py out.json   (standard library only; about 500 HEAD requests)
"""
import json
import random
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

DB = "data/nba.sqlite"
BASE = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{date}_{label}.pdf"
ET = ZoneInfo("America/New_York")
SEED, DATES_PER_SEASON = 20261001, 10
HOURS = (13, 17, 20)   # 1 PM, 5 PM, 8 PM Eastern


def head(url):
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.headers.get("Last-Modified"), r.headers.get("Content-Length")
    except urllib.error.HTTPError as e:
        return e.code, None, None
    except Exception as e:  # network error: reported, not retried
        return f"ERR {type(e).__name__}", None, None


def labels(hour, minute=0):
    h12, ampm = (hour % 12) or 12, "AM" if hour < 12 else "PM"
    return {"hourly": f"{h12:02d}{ampm}", "quarter": f"{h12:02d}_{minute:02d}{ampm}"}


def probe(date, hour, minute=0):
    out = {"date": date, "hour": hour, "minute": minute}
    for kind, label in labels(hour, minute).items():
        if kind == "hourly" and minute:
            continue
        status, modified, size = head(BASE.format(date=date, label=label))
        rec = {"status": status}
        if modified:
            named = datetime.strptime(f"{date} {hour}:{minute}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
            lag = (parsedate_to_datetime(modified) - named.astimezone(timezone.utc)).total_seconds() / 60
            rec.update(last_modified=modified, minutes_after_named_time=round(lag, 1), bytes=int(size or 0))
        out[kind] = rec
        time.sleep(0.15)
    return out


def main(path):
    conn = sqlite3.connect(f"file:{DB}?mode=ro&immutable=1", uri=True)  # read-only; never touches the WAL
    rng, result = random.Random(SEED), {"by_season": {}, "samples": []}
    for season in range(2020, 2027):
        days = sorted({datetime.strptime(r[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(ET)
                       .strftime("%Y-%m-%d") for r in conn.execute(
            "SELECT tipoff_at FROM games WHERE season=? AND status='final' AND season_type IN ('regular','postseason')",
            (season,))})
        picked = sorted(rng.sample(days, DATES_PER_SEASON))
        rows = [probe(d, h) for d in picked for h in HOURS]
        result["samples"] += [{"season": season, **r} for r in rows]
        ok = lambda r: any(r.get(k, {}).get("status") == 200 for k in ("hourly", "quarter"))  # noqa: E731
        dates_ok = sum(any(ok(r) for r in rows if r["date"] == d) for d in picked)
        result["by_season"][season] = {
            "dates_sampled": len(picked), "dates_with_any_report": dates_ok,
            "reports_found": sum(ok(r) for r in rows), "reports_checked": len(rows),
            "hourly_format": sum(r["hourly"]["status"] == 200 for r in rows),
            "quarter_format": sum(r["quarter"]["status"] == 200 for r in rows),
            "missing": [f"{r['date']} {r['hour']}h" for r in rows if not ok(r)]}
        print(season, result["by_season"][season], flush=True)
    # One full day in each format era: every hour (old) and every quarter hour (new).
    result["full_day_hourly_2023-01-10"] = [probe("2023-01-10", h) for h in range(24)]
    result["full_day_quarter_2026-03-10"] = [probe("2026-03-10", h, m) for h in range(24) for m in (0, 15, 30, 45)]
    # When did the file-name format change? Bisect on the 5 PM report between two known dates.
    lo, hi = datetime(2025, 12, 1), datetime(2026, 3, 1)
    trail = []
    while (hi - lo).days > 1:
        mid = lo + (hi - lo) / 2
        mid = datetime(mid.year, mid.month, mid.day)
        p = probe(mid.strftime("%Y-%m-%d"), 17)
        trail.append({"date": p["date"], "hourly": p["hourly"]["status"], "quarter": p["quarter"]["status"]})
        if p["quarter"]["status"] == 200:
            hi = mid
        elif p["hourly"]["status"] == 200:
            lo = mid
        else:   # no 5 PM report that day (no games): step forward one day
            lo = lo + timedelta(days=1) if (hi - lo).days > 2 else hi
    result["format_change"] = {"last_hourly_seen": lo.strftime("%Y-%m-%d"), "first_quarter_seen": hi.strftime("%Y-%m-%d"),
                               "trail": trail}
    result["before_our_data"] = [probe(d, 17) for d in ("2018-12-01", "2017-12-01", "2016-12-01")]
    result["off_season_and_preseason"] = [probe(d, 17) for d in ("2026-07-15", "2026-09-30", "2025-10-10")]
    json.dump(result, open(path, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1])
