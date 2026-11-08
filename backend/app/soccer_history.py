"""Soccer history from openfootball (CC0 1.0), offline research only: download pinned files -> raw cache -> import.

Sources (S1 report, licence read in every repo): football.json league files for the five domestic leagues and the
champions-league repo's football.txt files for the UEFA Champions League (cl.txt) and Europa League (el.txt) main stages.
Qualifying rounds (clq/elq, two seasons only) and the Conference League are out of scope.

Every file is downloaded at a pinned git commit; the bytes go through raw_cache (content hash, importer version, one
raw_fetches row per retrieval) and soccer_source_files records repo, commit and path. Import reads only cached bytes, so
`replay` rebuilds the same rows from the cache without the network.

What the rows may claim (docs/evidence/soccer-training-2026-11-08/SOCCER_TRAINING_REPORT.md):
- local date and time exactly as listed; the zone is not stated and is never converted; a missing time stays NULL;
- the 90-minute score as listed; extra time and shoot-outs only as the source notes them;
- grading: only a final match that cannot have gone to extra time (domestic leagues, UEFA group / league phase, first
  legs) and has no extra-time or shoot-out note is 'gradable'. Every UEFA deciding match (second legs, single-match
  knockouts, finals) is excluded from 90-minute grading: the source's a.e.t. notes are known to be incomplete (S1:
  Inter-Barcelona 2025-05-06 listed 4-3 without a.e.t.) and no independent second source is in scope;
- result availability is an assumption (next day 06:00 UTC), labelled as such: the source publishes no time.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from . import raw_cache, soccer_clubs, sports
from .db import StoreMismatch, has_table, store_sport, transaction, utc_now

SOURCE = "openfootball"
IMPORTER_VERSION = "openfootball-1"
USER_AGENT = "courtside-soccer-research/1 (offline evaluation; openfootball CC0)"
REPOS = ("football.json", "champions-league")
LEAGUE_FILES = {"en.1.json": "eng.1", "es.1.json": "esp.1", "de.1.json": "ger.1", "it.1.json": "ita.1",
                "fr.1.json": "fra.1"}
UEFA_FILES = {"cl.txt": "uefa.champions", "el.txt": "uefa.europa"}
SEASON_DIR = re.compile(r"^(\d{4})-(\d{2})$")
AVAILABILITY_BASIS = "assumed_next_day_0600Z"

Fetch = Callable[[str], bytes]


def http_get(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:       # noqa: S310 - fixed https hosts only
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        return resp.read()


def commit_url(repo: str, ref: str) -> str:
    return f"https://api.github.com/repos/openfootball/{repo}/commits/{ref}"


def tree_url(repo: str, sha: str) -> str:
    return f"https://api.github.com/repos/openfootball/{repo}/git/trees/{sha}?recursive=1"


def file_url(repo: str, sha: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/openfootball/{repo}/{sha}/{path}"


class CacheMismatch(RuntimeError):
    """Downloaded bytes differ from the git blob the pinned tree lists for that path."""


class MapVersionChanged(RuntimeError):
    """The store's rows were resolved with another club map version."""


def git_blob_sha(body: bytes) -> str:
    """Git's blob id of the bytes: sha1(b'blob <size>\\0' + bytes), as listed in a tree."""
    return hashlib.sha1(b"blob %d\x00" % len(body) + body).hexdigest()


def _require_soccer(conn: sqlite3.Connection) -> None:
    current = store_sport(conn)
    if current != sports.SOCCER.id:
        raise StoreMismatch(f"This store holds {current or 'unlabelled'!r} data, not 'soccer'")


def _season(dirname: str) -> Optional[int]:
    m = SEASON_DIR.match(dirname)
    if not m or (int(m.group(1)) + 1) % 100 != int(m.group(2)):
        return None
    return int(m.group(1))


def results_files(repo: str, tree: dict) -> list[tuple[str, int, str]]:
    """(competition, season, path) for every in-scope results file of a git tree listing, sorted."""
    if tree.get("truncated"):
        raise RuntimeError(f"{repo} tree listing is truncated; refusing an incomplete file list")
    names = LEAGUE_FILES if repo == "football.json" else UEFA_FILES
    out = []
    for e in tree["tree"]:
        if e.get("type") != "blob" or e["path"].count("/") != 1:
            continue
        folder, name = e["path"].split("/")
        season = _season(folder)
        if season is not None and name in names:
            out.append((names[name], season, e["path"]))
    return sorted(out)


def _store(conn, *, url: str, request_key: str, body: bytes, retrieved_at: str, repo: str, sha: str, path: str,
           kind: str, competition: Optional[str] = None, season: Optional[int] = None,
           cache_dir: Optional[Path] = None) -> tuple[int, int]:
    raw_id, fetch_id = raw_cache.store_fetch(conn, source=SOURCE, endpoint=url, request_key=request_key, body=body,
                                             retrieved_at=retrieved_at, importer_version=IMPORTER_VERSION,
                                             cache_dir=cache_dir, suffix=".json" if path.endswith(".json") or kind != "results" else ".txt")
    conn.execute("INSERT INTO soccer_source_files (fetch_id, raw_id, repo, commit_sha, path, kind, competition_id, season,"
                 " retrieved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (fetch_id, raw_id, repo, sha, path, kind, competition, season, retrieved_at))
    return raw_id, fetch_id


def download(conn: sqlite3.Connection, *, pins: Optional[dict[str, str]] = None, fetch: Fetch = http_get,
             sleep: float = 0.5, cache_dir: Optional[Path] = None, now: Callable[[], str] = utc_now,
             only: Optional[set[str]] = None) -> dict:
    """Downloads every in-scope results file at one commit per repo (`pins[repo]`, else the default branch's current
    commit, resolved and cached first) into the raw cache. Imports nothing: `replay` imports from the cache."""
    _require_soccer(conn)
    pins = dict(pins or {})
    report = {"commits": {}, "files": []}
    for repo in REPOS:
        if only and repo not in only:
            continue
        sha = pins.get(repo)
        if sha is None:
            url = commit_url(repo, "HEAD")
            body = fetch(url)
            sha = json.loads(body)["sha"]
            with transaction(conn):
                _store(conn, url=url, request_key=f"repo={repo}&commit=HEAD", body=body, retrieved_at=now(), repo=repo,
                       sha=sha, path="", kind="commit", cache_dir=cache_dir)
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError(f"{repo}: commit must be a full 40-character sha, got {sha!r}")
        report["commits"][repo] = sha
        url = tree_url(repo, sha)
        body = fetch(url)
        tree = json.loads(body)
        with transaction(conn):
            _store(conn, url=url, request_key=f"repo={repo}&tree={sha}", body=body, retrieved_at=now(), repo=repo,
                   sha=sha, path="", kind="tree", cache_dir=cache_dir)
        blobs = {e["path"]: e.get("sha") for e in tree["tree"]}
        for competition, season, path in results_files(repo, tree):
            url = file_url(repo, sha, path)
            body = fetch(url)
            if blobs.get(path) != git_blob_sha(body):
                raise CacheMismatch(f"{repo}@{sha[:12]} {path}: downloaded bytes are not the blob the tree lists; nothing "
                                    "stored for this file")
            retrieved_at = now()
            with transaction(conn):
                raw_id, fetch_id = _store(conn, url=url, request_key=f"repo={repo}&path={path}", body=body,
                                          retrieved_at=retrieved_at, repo=repo, sha=sha, path=path, kind="results",
                                          competition=competition, season=season, cache_dir=cache_dir)
            report["files"].append({"repo": repo, "path": path, "competition": competition, "season": season,
                                    "raw_id": raw_id, "fetch_id": fetch_id, "bytes": len(body)})
            if sleep:
                time.sleep(sleep)
    return report


# --- Parsing: cached bytes -> matches as written. Nothing here reads the network or the store. ---
@dataclass
class Parsed:
    locator: str
    round: str
    stage: str
    home_name: str
    away_name: str
    home_country: str
    away_country: str
    local_date: str
    local_time: Optional[str]
    time_listed: Optional[int]
    status: str
    ft: Optional[tuple[int, int]] = None
    ht: Optional[tuple[int, int]] = None
    aet: Optional[tuple[int, int]] = None
    pen: Optional[tuple[int, int]] = None
    deciding: int = 0


@dataclass
class ParsedFile:
    matches: list[Parsed] = field(default_factory=list)
    rejections: list[tuple[str, str, str]] = field(default_factory=list)      # (locator, reason, detail)
    header_matches: Optional[int] = None


def _season_window(season: int, day: str) -> bool:
    """Loose sanity window for a season's dates: 1 July of the start year to 30 September of the next (the 2019-20
    seasons ended in August 2020)."""
    return f"{season}-07-01" <= day <= f"{season + 1}-09-30"


def _no_score_status(local_date: str, retrieved_day: str) -> str:
    return "result_missing" if local_date < retrieved_day else "scheduled"


def _pair(v) -> Optional[tuple[int, int]]:
    if isinstance(v, list) and len(v) == 2 and all(type(x) is int and x >= 0 for x in v):
        return (v[0], v[1])
    return None


def parse_json(body: bytes, competition: str, season: int, retrieved_day: str) -> ParsedFile:
    """football.json league file. Score shapes seen in the pinned files: {"ft": [h, a], "ht": [h, a]}, {"ft": [..]},
    a bare [h, a] list (2025-26+, = full time), {} or null or absent (no result); statuses 'cancelled'/'canceled'
    (no result) and 'awarded' (a desk result)."""
    out = ParsedFile()
    country = soccer_clubs.LEAGUE_COUNTRY[competition]
    doc = json.loads(body)
    if not isinstance(doc, dict) or not isinstance(doc.get("matches"), list):
        out.rejections.append(("file", "unparsed_file", "no 'matches' list"))
        return out
    for i, m in enumerate(doc["matches"]):
        loc = f"matches[{i}]"
        try:
            rnd, day, home, away = m["round"], m["date"], m["team1"], m["team2"]
            if not all(isinstance(x, str) and x.strip() for x in (rnd, day, home, away)):
                raise ValueError("empty or non-text round/date/team")
            date.fromisoformat(day)
        except (KeyError, TypeError, ValueError) as exc:
            out.rejections.append((loc, "unparsed_match", f"{type(exc).__name__}: {exc}"[:200]))
            continue
        extra = set(m) - {"round", "date", "time", "team1", "team2", "score", "status"}
        if extra:
            out.rejections.append((loc, "unexpected_fields", ",".join(sorted(extra))))
            continue
        t = m.get("time")
        local_time = t if isinstance(t, str) and re.fullmatch(r"[0-2]\d:[0-5]\d", t) else None
        if t is not None and local_time is None:
            out.rejections.append((loc, "unparsed_time", repr(t)[:60]))
            continue
        score, src_status = m.get("score"), m.get("status")
        ft = ht = None
        if isinstance(score, list):
            ft = _pair(score)
            if ft is None:
                out.rejections.append((loc, "unparsed_score", json.dumps(score)[:80]))
                continue
        elif isinstance(score, dict):
            if set(score) - {"ft", "ht"} or ("ft" in score and _pair(score["ft"]) is None) or \
                    ("ht" in score and _pair(score["ht"]) is None) or ("ht" in score and "ft" not in score):
                out.rejections.append((loc, "unparsed_score", json.dumps(score)[:80]))
                continue
            ft, ht = _pair(score.get("ft")), _pair(score.get("ht"))
        elif score is not None:
            out.rejections.append((loc, "unparsed_score", json.dumps(score)[:80]))
            continue
        if src_status in ("cancelled", "canceled"):
            status, ft, ht = "cancelled", None, None
        elif src_status == "awarded":
            if ft is None:
                out.rejections.append((loc, "awarded_without_score", ""))
                continue
            status, ht = "awarded", None
        elif src_status is not None:
            out.rejections.append((loc, "unknown_status", str(src_status)[:60]))
            continue
        else:
            status = "final" if ft else _no_score_status(day, retrieved_day)
        if not _season_window(season, day):
            out.rejections.append((loc, "date_outside_season", day))
            continue
        out.matches.append(Parsed(loc, rnd.strip(), "league", home.strip(), away.strip(), country, country, day,
                                  local_time, None if local_time is None else 1, status, ft, ht))
    # A match dated before the season began is a wrong date (football.json en.1 2019-20 lists the 66 matches played after
    # the June 2020 restart in July 2019): left in, a 2020 result would look known in 2019. Rejected, never re-dated.
    opening = sorted(p.local_date for p in out.matches if p.round == "Matchday 1")
    if opening:
        start = (date.fromisoformat(opening[len(opening) // 2]) - timedelta(days=7)).isoformat()
        early = [p for p in out.matches if p.local_date < start]
        for p in early:
            out.rejections.append((p.locator, "date_before_season_start",
                                   f"{p.local_date} {p.round} {p.home_name} v {p.away_name}; season starts ~{start}"))
        out.matches = [p for p in out.matches if p.local_date >= start]
    return out


TXT_TEAM = r"(?P<{0}>\S.*?) \((?P<{0}c>[A-Z]{{3}})\)"
TXT_MATCH = re.compile(r"^\s+(?:(?P<time>\d{1,2}[.:]\d{2})\s+)?" + TXT_TEAM.format("h") + r"\s+v\s+"
                       + TXT_TEAM.format("a") + r"(?:\s+(?P<score>\S.*?))?\s*$")
TXT_DATE = re.compile(r"^\s+(?P<dow>Mon|Tue|Wed|Thu|Fri|Sat|Sun) (?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
                      r" (?P<day>\d{1,2})(?: (?P<year>\d{4}))?\s*$")
TXT_ROUND = re.compile(r"^▪ (?P<round>\S.*?)\s*$")
TXT_HEADER_MATCHES = re.compile(r"^# Matches\s+(?P<n>\d+)\s*$")
TXT_SCORE = re.compile(r"^(?:(?P<p1>\d+)-(?P<p2>\d+) pen\. )?(?:(?P<e1>\d+)-(?P<e2>\d+) a\.e\.t\. "
                       r"\((?P<f1>\d+)-(?P<f2>\d+)(?:, (?P<h1>\d+)-(?P<h2>\d+))?\)"
                       r"|(?P<g1>\d+)-(?P<g2>\d+)(?: \((?P<i1>\d+)-(?P<i2>\d+)\))?)$")
MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def uefa_stage(round_label: str) -> str:
    r = round_label.lower()
    if r.startswith(("group", "gruppe")):
        return "group"
    if r.startswith("league"):
        return "league_phase"
    return "knockout"


def tie_round(round_label: str) -> str:
    """The knockout round a match belongs to, for pairing legs: 'Playoffs, Matchday 2' -> 'Playoffs'."""
    return re.sub(r", Matchday \d+$", "", round_label)


def _ints(m, a, b) -> Optional[tuple[int, int]]:
    return (int(m[a]), int(m[b])) if m[a] is not None else None


def parse_txt(body: bytes, competition: str, season: int, retrieved_day: str) -> ParsedFile:
    """champions-league football.txt file (cl.txt / el.txt). Layout: '▪ <round>' headers, date lines ('Tue Sep 19 2023';
    the year is written only sometimes; a date without one takes whichever of the season's two years makes the listed
    weekday right (exactly one can: a date's weekday differs between consecutive years); a written year must agree with
    the weekday too; older files list each group's whole calendar in turn, so dates are not monotonic), match lines '[time] Home (CTY) v Away (CTY) <score>'. A match line without a time
    continues the time of the line above within the same date block (local_time_listed = 0)."""
    out = ParsedFile()
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        out.rejections.append(("file", "unparsed_file", str(exc)[:200]))
        return out
    rnd = cur_date = cur_time = None
    for no, line in enumerate(text.splitlines(), 1):
        loc = f"line {no}"
        if not line.strip() or line.startswith("="):
            continue
        if line.startswith("#"):
            h = TXT_HEADER_MATCHES.match(line)
            if h:
                out.header_matches = int(h["n"])
            continue
        r = TXT_ROUND.match(line)
        if r:
            rnd, cur_date, cur_time = r["round"], None, None
            continue
        d = TXT_DATE.match(line)
        if d:
            month, dom = MONTHS[d["mon"]], int(d["day"])
            cur_time = None
            years = [int(d["year"])] if d["year"] else [season, season + 1]
            fits = []
            for y in years:
                try:
                    if DOW[date(y, month, dom).weekday()] == d["dow"]:
                        fits.append(date(y, month, dom))
                except ValueError:
                    pass
            if len(fits) != 1:                 # one calendar date never has the same weekday in consecutive years
                out.rejections.append((loc, "weekday_mismatch", f"{line.strip()}: no single year in {years} fits"))
                cur_date = None
                continue
            day = fits[0]
            if not _season_window(season, day.isoformat()):
                out.rejections.append((loc, "date_outside_season", day.isoformat()))
                cur_date = None
                continue
            cur_date = day.isoformat()
            continue
        m = TXT_MATCH.match(line)
        if not m:
            out.rejections.append((loc, "unparsed_line", line.strip()[:120]))
            continue
        if rnd is None or cur_date is None:
            out.rejections.append((loc, "no_round_or_valid_date", line.strip()[:120]))
            continue
        if m["time"]:
            hh, mm = re.split(r"[.:]", m["time"])
            if int(hh) > 23:
                out.rejections.append((loc, "unparsed_time", m["time"]))
                continue
            cur_time, listed = f"{int(hh):02d}:{mm}", 1
        else:
            listed = 0 if cur_time else None
        raw_score = re.sub(r"\s+", " ", m["score"] or "").strip()
        ft = ht = aet = pen = None
        if raw_score == "":
            status = _no_score_status(cur_date, retrieved_day)
        elif raw_score == "[cancelled]":
            status = "cancelled"
        else:
            awarded = raw_score.endswith(" [awarded]")
            s = TXT_SCORE.match(raw_score[:-len(" [awarded]")] if awarded else raw_score)
            if not s:
                out.rejections.append((loc, "unparsed_score", raw_score[:80]))
                continue
            if s["e1"] is not None:
                aet, ft, ht, pen = _ints(s, "e1", "e2"), _ints(s, "f1", "f2"), _ints(s, "h1", "h2"), _ints(s, "p1", "p2")
            else:
                ft, ht = _ints(s, "g1", "g2"), _ints(s, "i1", "i2")
            if awarded and (aet or pen):
                out.rejections.append((loc, "unparsed_score", raw_score[:80]))
                continue
            status = "awarded" if awarded else "final"
            if awarded:
                ht = None
        if pen is not None and pen[0] == pen[1]:
            out.rejections.append((loc, "level_shootout", raw_score[:80]))
            continue
        out.matches.append(Parsed(loc, rnd, uefa_stage(rnd), m["h"].strip(), m["a"].strip(), m["hc"], m["ac"], cur_date,
                                  cur_time if listed is not None else None, listed, status, ft, ht, aet, pen))
    _mark_deciding(out.matches)
    return out


def _mark_deciding(matches: list[Parsed]) -> None:
    """UEFA knockout: the last meeting of a pairing within a knockout round could go to extra time (second legs,
    single-match rounds, finals); earlier legs cannot. Pairing uses resolved clubs, so a renamed side still pairs."""
    ties: dict[tuple, list[Parsed]] = {}
    for p in matches:
        if p.stage != "knockout":
            continue
        pair = frozenset({soccer_clubs.club_id(p.home_country, p.home_name),
                          soccer_clubs.club_id(p.away_country, p.away_name)})
        ties.setdefault((tie_round(p.round), pair), []).append(p)
    for legs in ties.values():
        legs.sort(key=lambda p: p.local_date)            # stable: file order breaks a same-day tie
        legs[-1].deciding = 1


def parse(repo: str, body: bytes, competition: str, season: int, retrieved_day: str) -> ParsedFile:
    return (parse_json if repo == "football.json" else parse_txt)(body, competition, season, retrieved_day)


def grading(p: Parsed) -> str:
    if p.status == "cancelled":
        return "cancelled"
    if p.status == "awarded":
        return "awarded"
    if p.status != "final":
        return "not_final"
    if p.aet is not None or p.pen is not None:
        return "extra_time_noted"
    if p.deciding:
        return "deciding_match_unverified"
    return "gradable"


def assumed_available_at(local_date: str) -> str:
    return (date.fromisoformat(local_date) + timedelta(days=1)).isoformat() + "T06:00:00Z"


def hist_match_id(competition: str, season: int, rnd: str, home: str, away: str) -> str:
    digest = hashlib.sha256("\x1f".join((rnd, home, away)).encode()).hexdigest()[:16]
    return f"of:{competition}:{season}:{digest}"


# --- Import: one cached results file, as seen at its retrieval -> current rows + append-only revisions. ---
STATE = ("local_date", "local_time", "local_time_listed", "status", "ft_home", "ft_away", "ht_home", "ht_away",
         "aet_home", "aet_away", "pen_home", "pen_away", "deciding")
RESULT = ("status", "ft_home", "ft_away", "aet_home", "aet_away", "pen_home", "pen_away")


def _state(p: Parsed) -> dict:
    two = lambda v: (None, None) if v is None else v        # noqa: E731
    (fh, fa), (hh, ha), (eh, ea), (ph, pa) = two(p.ft), two(p.ht), two(p.aet), two(p.pen)
    return {"local_date": p.local_date, "local_time": p.local_time, "local_time_listed": p.time_listed,
            "status": p.status, "ft_home": fh, "ft_away": fa, "ht_home": hh, "ht_away": ha, "aet_home": eh,
            "aet_away": ea, "pen_home": ph, "pen_away": pa, "deciding": p.deciding}


def _newer(retrieved_at: str, fetch_id: int, row) -> bool:
    """Retrievals are ordered by (retrieval time, raw_fetches id); the same retrieval again (a replay) is not newer."""
    return (retrieved_at, fetch_id) > (row["last_seen_at"], row["last_seen_fetch_id"])


def _append_revision(conn, mid: str, state: dict, in_file: int, avail: Optional[str], basis: Optional[str],
                     retrieved_at: str, fetch_id: int, raw_id: int) -> None:
    n = conn.execute("SELECT IFNULL(MAX(revision), 0) + 1 FROM soccer_hist_revisions WHERE hist_match_id = ?",
                     (mid,)).fetchone()[0]
    cols = [k for k in STATE if k != "local_time_listed"]
    conn.execute(f"INSERT INTO soccer_hist_revisions (hist_match_id, revision, {', '.join(cols)}, in_file, "
                 "result_available_at, result_availability_basis, observed_at, fetch_id, raw_id) "
                 f"VALUES (?, ?, {', '.join('?' * len(cols))}, ?, ?, ?, ?, ?, ?)",
                 (mid, n, *(state[k] for k in cols), in_file, avail, basis, retrieved_at, fetch_id, raw_id))


def _ensure_club(conn, competition: str, source_name: str, country: str, name: str, retrieved_at: str) -> str:
    c, n = soccer_clubs.club(country, name)
    cid = f"{c}:{n}"
    conn.execute("INSERT OR IGNORE INTO soccer_clubs (club_id, country, name) VALUES (?, ?, ?)", (cid, c, n))
    conn.execute("INSERT OR IGNORE INTO soccer_club_names (competition_id, source_name, club_id, map_version, first_seen_at)"
                 " VALUES (?, ?, ?, ?, ?)", (competition, source_name, cid, soccer_clubs.MAP_VERSION, retrieved_at))
    return cid


def import_file(conn: sqlite3.Connection, fetch_id: int, cache_dir: Optional[Path] = None) -> dict:
    """Imports one cached results file as seen at its retrieval. Must run inside a transaction on a soccer store.
    Idempotent: a retrieval that is not newer than the last one containing a match changes nothing for it."""
    _require_soccer(conn)
    f = conn.execute("SELECT * FROM soccer_source_files WHERE fetch_id = ? AND kind = 'results'", (fetch_id,)).fetchone()
    if f is None:
        raise KeyError(f"fetch {fetch_id} is not a soccer history results file")
    comp, season, retrieved_at, raw_id = f["competition_id"], f["season"], f["retrieved_at"], f["raw_id"]
    parsed = parse(f["repo"], raw_cache.load(conn, raw_id, cache_dir), comp, season, retrieved_at[:10])
    stats = {"fetch_id": fetch_id, "competition": comp, "season": season, "parsed": len(parsed.matches),
             "inserted": 0, "changed": 0, "result_corrections": 0, "unchanged": 0, "stale": 0, "withdrawn": 0,
             "rejected": 0}
    other_map = conn.execute("SELECT map_version FROM soccer_hist_matches WHERE map_version <> ? LIMIT 1",
                             (soccer_clubs.MAP_VERSION,)).fetchone()
    if other_map:
        raise MapVersionChanged(f"this store's matches were resolved with club map {other_map[0]}, the code has "
                                f"{soccer_clubs.MAP_VERSION}: rebuild a fresh store (copy_provenance + replay) instead of "
                                "importing into this one")
    rejections = list(parsed.rejections)
    if parsed.header_matches is not None and parsed.header_matches != len(parsed.matches):
        rejections.append(("file", "header_count_mismatch", f"header {parsed.header_matches}, parsed "
                           f"{len(parsed.matches)}, rejected lines {len(parsed.rejections)}"))
    # A file of this competition-season retrieved before: a match first seen now was not listed (or not under these
    # names) then, so a result it carries is known only from this retrieval (S2 re-check, MEDIUM-2 rest).
    earlier_file = conn.execute("SELECT 1 FROM soccer_source_files WHERE kind = 'results' AND competition_id = ? AND "
                                "season = ? AND (retrieved_at < ? OR (retrieved_at = ? AND fetch_id < ?)) LIMIT 1",
                                (comp, season, retrieved_at, retrieved_at, fetch_id)).fetchone() is not None
    keys: dict[str, list[Parsed]] = {}
    for p in parsed.matches:
        keys.setdefault(hist_match_id(comp, season, p.round, p.home_name, p.away_name), []).append(p)
    seen: set[str] = set()
    for mid, group in keys.items():
        if len(group) > 1:
            for p in group:
                rejections.append((p.locator, "duplicate_identity", f"{p.round} | {p.home_name} v {p.away_name}"))
            continue
        p = group[0]
        home = _ensure_club(conn, comp, p.home_name + ("" if comp in soccer_clubs.LEAGUE_COUNTRY else f" ({p.home_country})"),
                            p.home_country, p.home_name, retrieved_at)
        away = _ensure_club(conn, comp, p.away_name + ("" if comp in soccer_clubs.LEAGUE_COUNTRY else f" ({p.away_country})"),
                            p.away_country, p.away_name, retrieved_at)
        if home == away:
            rejections.append((p.locator, "same_club_both_sides", f"{p.home_name} v {p.away_name} -> {home}"))
            continue
        seen.add(mid)
        state, grade = _state(p), grading(p)
        row = conn.execute("SELECT * FROM soccer_hist_matches WHERE hist_match_id = ?", (mid,)).fetchone()
        final = p.status in ("final", "awarded")
        if row is None:
            if not final:
                avail, basis = None, None
            elif earlier_file:
                avail, basis = max(retrieved_at, assumed_available_at(p.local_date)), "observed_retrieval"
            else:
                avail, basis = assumed_available_at(p.local_date), AVAILABILITY_BASIS
            conn.execute(
                f"INSERT INTO soccer_hist_matches (hist_match_id, competition_id, season, round, stage, home_name, away_name,"
                f" home_club_id, away_club_id, {', '.join(STATE)}, grading, result_available_at, result_availability_basis,"
                " in_latest_file, first_seen_at, last_seen_at, last_seen_fetch_id, last_raw_id, importer_version, map_version)"
                f" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, {', '.join('?' * len(STATE))}, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
                (mid, comp, season, p.round, p.stage, p.home_name, p.away_name, home, away, *state.values(), grade,
                 avail, basis, retrieved_at, retrieved_at, fetch_id, raw_id, IMPORTER_VERSION, soccer_clubs.MAP_VERSION))
            _append_revision(conn, mid, state, 1, avail, basis, retrieved_at, fetch_id, raw_id)
            stats["inserted"] += 1
            continue
        if not _newer(retrieved_at, fetch_id, row):
            stats["stale"] += 1
            continue
        changed = any(row[k] != v for k, v in state.items()) or row["in_latest_file"] == 0
        avail, basis = row["result_available_at"], row["result_availability_basis"]
        if changed:
            was_final = row["status"] in ("final", "awarded")
            observed = max(retrieved_at, assumed_available_at(p.local_date))
            if not final:
                avail, basis = None, None
            elif not was_final:
                # The match was listed without this result before: the source had it only from this retrieval.
                avail, basis = observed, "observed_retrieval"
            elif any(row[k] != state[k] for k in RESULT) or row["local_date"] != p.local_date:
                # A different result, or the same result on another date, than one already listed: known only from this
                # retrieval (never backdated, never before the day after the corrected date).
                avail, basis = observed, "observed_correction"
                stats["result_corrections"] += 1
            elif row["deciding"] != p.deciding or row["in_latest_file"] == 0:
                # Same result, but whether it may be graded/used changed (a second leg appeared, or the match is listed
                # again): that fact is known only from this retrieval (review S3 L8). A time-only change keeps it.
                avail, basis = observed, "observed_retrieval"
            _append_revision(conn, mid, state, 1, avail, basis, retrieved_at, fetch_id, raw_id)
            stats["changed"] += 1
        else:
            stats["unchanged"] += 1
        conn.execute(
            f"UPDATE soccer_hist_matches SET {', '.join(f'{k} = ?' for k in STATE)}, stage = ?, home_club_id = ?,"
            " away_club_id = ?, grading = ?, result_available_at = ?, result_availability_basis = ?, in_latest_file = 1,"
            " last_seen_at = ?, last_seen_fetch_id = ?, last_raw_id = ?, importer_version = ? WHERE hist_match_id = ?",
            (*state.values(), p.stage, home, away, grade, avail, basis, retrieved_at, fetch_id, raw_id, IMPORTER_VERSION,
             mid))
    # Matches of this competition-season that a newer file no longer lists: kept, marked, and a revision records it.
    for row in conn.execute("SELECT * FROM soccer_hist_matches WHERE competition_id = ? AND season = ? AND in_latest_file = 1",
                            (comp, season)).fetchall():
        if row["hist_match_id"] in seen or not _newer(retrieved_at, fetch_id, row):
            continue
        dropped_at = (max(retrieved_at, row["result_available_at"]) if row["result_available_at"] else None)
        _append_revision(conn, row["hist_match_id"], {k: row[k] for k in STATE}, 0, dropped_at,      # never backdated
                         "observed_retrieval" if dropped_at else None, retrieved_at, fetch_id, raw_id)
        conn.execute("UPDATE soccer_hist_matches SET in_latest_file = 0, last_seen_at = ?, last_seen_fetch_id = ?,"
                     " last_raw_id = ? WHERE hist_match_id = ?", (retrieved_at, fetch_id, raw_id, row["hist_match_id"]))
        stats["withdrawn"] += 1
    for loc, reason, detail in rejections:
        conn.execute("INSERT OR IGNORE INTO soccer_hist_rejections (fetch_id, raw_id, competition_id, season, locator, reason,"
                     " detail) VALUES (?, ?, ?, ?, ?, ?, ?)", (fetch_id, raw_id, comp, season, loc, reason, detail))
    stats["rejected"] = len(rejections)
    return stats


def results_fetches(conn: sqlite3.Connection) -> list[int]:
    return [r[0] for r in conn.execute("SELECT fetch_id FROM soccer_source_files WHERE kind = 'results' "
                                       "ORDER BY retrieved_at, fetch_id")]


def replay_missing(conn: sqlite3.Connection, cache_dir: Optional[Path] = None) -> list[int]:
    """raw_ids of soccer history files whose cached bytes are absent (read-only; none before migration 0036)."""
    if not has_table(conn, "soccer_source_files"):
        return []
    ids = [r[0] for r in conn.execute("SELECT DISTINCT raw_id FROM soccer_source_files")]
    return raw_cache.missing_raw_ids(conn, ids, cache_dir)


def replay(conn: sqlite3.Connection, cache_dir: Optional[Path] = None) -> dict:
    """Imports every cached results file in retrieval order (time, fetch id), each in its own transaction. Into the store
    that already holds them: no change. Into a store with only the provenance tables: the same rows (see digest)."""
    _require_soccer(conn)
    totals: dict[str, int] = {}
    for fid in results_fetches(conn):
        with transaction(conn):
            s = import_file(conn, fid, cache_dir)
        for k, v in s.items():
            if isinstance(v, int) and k not in ("fetch_id", "season"):
                totals[k] = totals.get(k, 0) + v
    return {"files": len(results_fetches(conn)), **totals}


HISTORY_TABLES = ("soccer_clubs", "soccer_club_names", "soccer_hist_matches", "soccer_hist_revisions",
                  "soccer_hist_rejections")


def digest(conn: sqlite3.Connection) -> dict[str, str]:
    """sha256 per history table over every column of every row in primary-key order: equal digests = identical rows."""
    out = {}
    for t in HISTORY_TABLES:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        pk = [r[1] for r in sorted(conn.execute(f"PRAGMA table_info({t})"), key=lambda r: r[5]) if r[5]] or cols
        h = hashlib.sha256()
        for row in conn.execute(f"SELECT {', '.join(cols)} FROM {t} ORDER BY {', '.join(pk)}"):
            h.update(json.dumps(list(row), ensure_ascii=False).encode() + b"\n")
        out[t] = h.hexdigest()
    return out


def copy_provenance(dst: sqlite3.Connection, src_path: Path, max_season: Optional[int] = None) -> int:
    """Copies the soccer history provenance (raw_responses / raw_fetches rows referenced by soccer_source_files, and
    soccer_source_files) from another soccer store file into `dst`, which must hold none yet; the raw files are read
    through dst's raw/ folder. `replay` then rebuilds every history row from the cached bytes. With `max_season`, results
    files of later seasons are left out entirely (a development copy that cannot read reserved seasons; commit and tree
    rows are kept: they list blob ids, not contents). Returns files copied."""
    _require_soccer(dst)
    if dst.execute("SELECT COUNT(*) FROM soccer_source_files").fetchone()[0]:
        raise ValueError("destination already has soccer history provenance")
    dst.execute("ATTACH DATABASE ? AS src", (str(Path(src_path).resolve()),))     # read by SELECT only
    try:
        with transaction(dst):
            keep = "(s.kind <> 'results' OR ? IS NULL OR s.season <= ?)"
            args = (max_season, max_season)
            dst.execute("INSERT INTO raw_responses SELECT r.* FROM src.raw_responses r WHERE r.raw_id IN "
                        f"(SELECT s.raw_id FROM src.soccer_source_files s WHERE {keep}) ORDER BY r.raw_id", args)
            dst.execute("INSERT INTO raw_fetches SELECT f.* FROM src.raw_fetches f WHERE f.fetch_id IN "
                        f"(SELECT s.fetch_id FROM src.soccer_source_files s WHERE {keep}) ORDER BY f.fetch_id", args)
            dst.execute(f"INSERT INTO soccer_source_files SELECT s.* FROM src.soccer_source_files s WHERE {keep} "
                        "ORDER BY s.fetch_id", args)
        return dst.execute("SELECT COUNT(*) FROM soccer_source_files").fetchone()[0]
    finally:
        dst.execute("DETACH DATABASE src")


def coverage(conn: sqlite3.Connection) -> dict:
    """Read-only coverage and consistency report regenerated from the store (soccer history rows only)."""
    _require_soccer(conn)
    seasons = []
    q = ("SELECT competition_id, season, COUNT(*) n, SUM(status='final') final, SUM(status='scheduled') scheduled,"
         " SUM(status='result_missing') result_missing, SUM(status='cancelled') cancelled, SUM(status='awarded') awarded,"
         " SUM(grading='gradable') gradable, SUM(grading='extra_time_noted') extra_time_noted,"
         " SUM(grading='deciding_match_unverified') deciding_unverified, SUM(local_time IS NULL) time_missing,"
         " SUM(local_time_listed = 0) time_carried, SUM(in_latest_file = 0) withdrawn, MIN(local_date) first_date,"
         " MAX(local_date) last_date, COUNT(DISTINCT home_club_id) home_clubs FROM soccer_hist_matches"
         " GROUP BY competition_id, season ORDER BY competition_id, season")
    for r in conn.execute(q):
        d = dict(r)
        f = conn.execute("SELECT repo, commit_sha, path, retrieved_at, fetch_id FROM soccer_source_files WHERE kind='results'"
                         " AND competition_id=? AND season=? ORDER BY retrieved_at DESC, fetch_id DESC LIMIT 1",
                         (d["competition_id"], d["season"])).fetchone()
        d["latest_file"] = dict(f) if f else None
        d["rejections"] = conn.execute("SELECT COUNT(*) FROM soccer_hist_rejections WHERE competition_id=? AND season=?",
                                       (d["competition_id"], d["season"])).fetchone()[0]
        if d["competition_id"] in soccer_clubs.LEAGUE_COUNTRY:
            per_club = [x[0] for x in conn.execute(
                "SELECT COUNT(*) FROM (SELECT home_club_id c FROM soccer_hist_matches WHERE competition_id=? AND season=?"
                " AND in_latest_file=1 UNION ALL SELECT away_club_id FROM soccer_hist_matches WHERE competition_id=? AND"
                " season=? AND in_latest_file=1) GROUP BY c", (d["competition_id"], d["season"]) * 2)]
            d["league_clubs"], d["matches_per_club"] = len(per_club), sorted(set(per_club))
        seasons.append(d)
    clubs = {r[0] for r in conn.execute("SELECT club_id FROM soccer_clubs")}
    anomalies = []
    # A single-match tie (no other meeting in its round) that went to extra time must be level after 90 minutes.
    for r in conn.execute("SELECT hist_match_id, competition_id, season, round, home_name, away_name, ft_home, ft_away,"
                          " aet_home, aet_away FROM soccer_hist_matches WHERE aet_home IS NOT NULL AND ft_home <> ft_away"):
        legs = conn.execute("SELECT COUNT(*) FROM soccer_hist_matches WHERE competition_id=? AND season=? AND ((home_name=?"
                            " AND away_name=?) OR (home_name=? AND away_name=?))",
                            (r["competition_id"], r["season"], r["home_name"], r["away_name"], r["away_name"],
                             r["home_name"])).fetchone()[0]
        if legs == 1:
            anomalies.append({"hist_match_id": r["hist_match_id"], "issue": "single match after extra time not level at 90",
                              **{k: r[k] for k in ("competition_id", "season", "round", "home_name", "away_name")}})
    # Extra time can only follow a level aggregate (or a level single match): the 90-minute score must not exceed the
    # after-extra-time score.
    for r in conn.execute("SELECT hist_match_id, competition_id, season, home_name, away_name FROM soccer_hist_matches WHERE "
                          "aet_home IS NOT NULL AND (aet_home < ft_home OR aet_away < ft_away)"):
        anomalies.append({"hist_match_id": r["hist_match_id"], "issue": "after-extra-time score below 90-minute score",
                          **dict(r)})
    return {"source": SOURCE, "importer_version": IMPORTER_VERSION, "club_map_version": soccer_clubs.MAP_VERSION,
            "matches": conn.execute("SELECT COUNT(*) FROM soccer_hist_matches").fetchone()[0],
            "revisions": conn.execute("SELECT COUNT(*) FROM soccer_hist_revisions").fetchone()[0],
            "rejections": [dict(r) for r in conn.execute("SELECT competition_id, season, reason, COUNT(*) n FROM "
                                                         "soccer_hist_rejections GROUP BY 1, 2, 3 ORDER BY 1, 2, 3")],
            "clubs": len(clubs),
            "aliased_source_names": conn.execute("SELECT COUNT(DISTINCT n.competition_id || n.source_name) FROM "
                                                 "soccer_club_names n JOIN soccer_clubs c USING (club_id) WHERE "
                                                 "n.source_name <> c.name AND n.source_name NOT LIKE c.name || ' (%)'"
                                                 ).fetchone()[0],
            "possible_duplicate_clubs": [list(p) for p in soccer_clubs.possible_duplicates(clubs)],
            "same_name_other_country": soccer_clubs.same_name_other_country(clubs),
            "anomalies": anomalies, "seasons": seasons}
