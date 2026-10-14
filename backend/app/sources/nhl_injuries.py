"""Historical ESPN injury snapshots. Capture time, not injury/return date, gates features.

Raw archives are immutable and resumable under NBA_DATA_DIR/research/nhl_players.
No predictions, training or live database mutations occur during collection.
"""
import gzip
import hashlib
import html
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .. import config
from ..db import utc_now

VERSION = "nhl-availability-archive-1"
CDX = "https://web.archive.org/cdx/search/cdx"
PAGE = "http://www.espn.com/nhl/injuries"
WEIGHTS = {"injured reserve": 1., "ir": 1., "out": 1., "day-to-day": .5,
           "day to day": .5, "dtd": .5, "questionable": .5, "probable": .25,
           "active": 0., "healthy": 0.}


def root():
    return config.data_dir() / "research" / "nhl_players"


def parse(body: bytes) -> tuple[str, list[dict], list[str]]:
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    h = body.decode("utf-8")
    match = re.search(r"window\[['\"]__espnfitt__['\"]\]\s*=\s*", h)
    rows, teams = [], set()
    if match:
        data, _ = json.JSONDecoder().raw_decode(h[match.end():])
        for team in data["page"]["content"]["injuries"]:
            name = team["displayName"]
            teams.add(name)
            for item in team["items"]:
                rows.append({"team": name, "player": item["athlete"]["name"],
                             "source_player_id": str(item["athlete"].get("id") or ""),
                             "status": item.get("statusDesc", ""), "comment": item.get("description", ""),
                             "estimated_return": item.get("date")})
        layout = "json"
    else:
        team = None
        for attrs, body in re.findall(r"<tr\b([^>]*)>(.*?)</tr>", h, re.S | re.I):
            cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                     for c in re.findall(r"<td\b[^>]*>(.*?)</td>", body, re.S | re.I)]
            if "stathead" in attrs and cells:
                team = cells[0]
                teams.add(team)
            elif ("oddrow" in attrs or "evenrow" in attrs) and team and len(cells) == 3:
                rows.append({"team": team, "player": cells[0], "status": cells[1],
                             "source_date": cells[2], "comment": ""})
            elif len(cells) == 1 and rows and "Comment:" in cells[0]:
                rows[-1]["comment"] = cells[0].replace("Comment:", "").strip()
        layout = "table"
    if not teams or not rows:
        raise ValueError("unrecognized or empty injury page; never infer a healthy league")
    # Old ESPN tables are update histories in newest-first order. Preserve the raw body for audit.
    unique = {}
    for row in rows:
        row["weight"] = WEIGHTS.get(row["status"].strip().lower())
        unique.setdefault((row["team"], row["player"]), row)
    return layout, list(unique.values()), sorted(teams)


def capture_time(stamp):
    return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def actual_capture(url, requested):
    """Reject archive replay redirects to a different capture or a different source."""
    parsed = urlparse(str(url))
    m = re.fullmatch(r"/web/(\d{14})id_/https?://(?:www\.)?espn.com/nhl/injuries/?", parsed.path)
    if parsed.hostname != "web.archive.org" or not m or m[1] != requested:
        raise ValueError(f"archive replay did not return the requested raw capture: {url}")
    return capture_time(m[1])


def save_capture(response, stamp, directory):
    response.raise_for_status()
    captured = actual_capture(response.url, stamp)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(response.content).hexdigest()
    raw = directory / f"{stamp}-{digest}.html"
    if not raw.exists():
        with raw.open("xb") as f:
            f.write(response.content)
    # Keep evidence even when a source layout cannot be parsed; no usable metadata is published.
    layout, rows, teams = parse(response.content)
    meta = {"version": VERSION, "source": PAGE, "requested_capture": stamp, "captured_at": captured,
            "response_url": str(response.url), "retrieved_at": utc_now(), "sha256": digest,
            "raw_file": raw.name, "layout": layout, "rows": rows, "teams": teams}
    path = directory / f"{stamp}.json"
    if path.exists():
        old = json.loads(path.read_text())
        if old["sha256"] != digest:
            raise ValueError("capture content changed; kept original metadata and both raw bodies")
    else:
        with path.open("x") as f:
            json.dump(meta, f)
    return meta


def collect(start: int, end: int, *, limit=None, delay=2., directory=None, log=print):
    """One latest capture per UTC date; coverage must be measured from successfully parsed captures."""
    import httpx
    directory = Path(directory or root() / "injuries")
    directory.mkdir(parents=True, exist_ok=True)
    counts = {"stored": 0, "cached": 0, "failed": 0, "errors": []}
    with httpx.Client(timeout=45, follow_redirects=True) as client:
        response = client.get(CDX, params={"url": "espn.com/nhl/injuries", "from": start, "to": end,
            "output": "json", "fl": "timestamp", "filter": "statuscode:200"})
        response.raise_for_status()
        index = response.json()
        if not index or index[0] != ["timestamp"]:
            raise ValueError("unexpected CDX index schema")
        daily = {}
        for row in index[1:]:
            capture_time(row[0])
            daily[row[0][:8]] = max(row[0], daily.get(row[0][:8], row[0]))
        stamps = sorted(daily.values())
        if limit is not None:
            stamps = stamps[:limit]
        manifest = {"retrieved_at": utc_now(), "from": start, "to": end, "captures": stamps}
        (directory / f"index-{hashlib.sha256(response.content).hexdigest()[:16]}.json").write_text(json.dumps(manifest))
        for stamp in stamps:
            if (directory / f"{stamp}.json").exists():
                counts["cached"] += 1
                continue
            time.sleep(max(1., delay))
            try:
                save_capture(client.get(f"https://web.archive.org/web/{stamp}id_/{PAGE}"), stamp, directory)
                counts["stored"] += 1
            except Exception as exc:
                counts["failed"] += 1
                counts["errors"].append({"capture": stamp, "error": str(exc)})
                log(json.dumps(counts["errors"][-1]))
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (403, 429):
                    break  # respect access/rate limits; resume later from immutable cache
            if (counts["stored"] + counts["failed"]) % 25 == 0:
                log(json.dumps({k: v for k, v in counts.items() if k != "errors"}))
    (directory / "last-run.json").write_text(json.dumps(counts, indent=2))
    return counts
