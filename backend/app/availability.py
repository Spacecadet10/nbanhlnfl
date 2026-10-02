"""NBA availability research (`avail_v1`, docs/EVALUATION_PLAN.md "NBA availability candidate"). Development research only:
never imported by the API or the scheduled jobs.

Data live in a research cache, `<data>/research/availability/`, apart from the production store, so the live injury collector's
request budget and attempt log are untouched:
- `player_box/player_box_<season>.csv`: sportsdataverse ESPN player box scores, each verified against GitHub's SHA-256 digest;
- `reports/<file>.pdf` + `<file>.pdf.json`: one 5:30 PM ET official injury report per game date, with its HTTP metadata;
- `attempts.jsonl`: every HTTP attempt, successful or not, appended (start time read before the request, completion after the body).
Seasons 2019-20 … 2023-24 only: the spent test seasons are never requested.
"""
import csv
import hashlib
import io
import json
import re
import sqlite3
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from . import backtest, config
from .db import utc_now
from .sources import nba_injury

ET = ZoneInfo("America/New_York")
SEASONS = (2020, 2021, 2022, 2023, 2024)          # 2019-20 … 2023-24; never 2025 or 2026 (spent test seasons)
SCORED = (2022, 2023, 2024)
REPORT_HOUR, REPORT_MINUTE = 17, 30                 # the 5:30 PM ET report
CUTOFF_HOUR, CUTOFF_MINUTE = 17, 45                 # forecast time
EARLIEST_TIPOFF = (18, 0)                           # games tipping off before 18:00 ET are excluded
LAST_MODIFIED_TOLERANCE = timedelta(minutes=2)
MAX_RETRIES = 2
REQUEST_SPACING = 1.2
PLAYER_BOX_RELEASE = "https://api.github.com/repos/sportsdataverse/sportsdataverse-data/releases/tags/espn_nba_player_boxscores"
PLAYER_BOX_DOWNLOAD = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_player_boxscores/{name}"
STATUS_WEIGHT = {"Out": 1.0, "Doubtful": 1.0, "Questionable": 0.5, "Probable": 0.0, "Available": 0.0}


class AvailabilityError(RuntimeError):
    pass


def research_dir() -> Path:
    return config.data_dir() / "research" / "availability"


def _log_attempt(entry: dict) -> None:
    path = research_dir() / "attempts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _check_season(season: int) -> None:
    if season not in SEASONS:
        raise AvailabilityError(f"Season {season} is outside the research window {SEASONS} (test seasons are never read)")


# --- Player box scores -------------------------------------------------------------------------------------------
def player_box_path(season: int) -> Path:
    return research_dir() / "player_box" / f"player_box_{season}.csv"


def download_player_box(season: int, get: Optional[Callable] = None) -> dict:
    """Downloads one season file and verifies it against the digest GitHub publishes; a mismatch stores nothing."""
    _check_season(season)
    import httpx
    get = get or (lambda url: httpx.get(url, timeout=120, follow_redirects=True))
    name = f"player_box_{season}.csv"
    release = get(PLAYER_BOX_RELEASE).json()
    digest = next((a.get("digest") for a in release.get("assets", []) if a["name"] == name), None)
    started = utc_now()
    resp = get(PLAYER_BOX_DOWNLOAD.format(name=name))
    completed = utc_now()
    body = resp.content
    sha = "sha256:" + hashlib.sha256(body).hexdigest()
    ok = resp.status_code == 200 and digest is not None and sha == digest
    _log_attempt({"kind": "player_box", "name": name, "started_at": started, "completed_at": completed,
                  "http_status": resp.status_code, "bytes": len(body), "sha256": sha, "published_digest": digest,
                  "outcome": "stored" if ok else "digest_mismatch" if resp.status_code == 200 else "failed"})
    if not ok:
        raise AvailabilityError(f"{name}: status {resp.status_code}, digest {sha} vs published {digest}")
    path = player_box_path(season)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return {"season": season, "bytes": len(body), "sha256": sha}


@dataclass
class Appearance:
    date: str            # ET calendar date of the game
    season: int
    team_id: str         # ESPN team id (our team ids are 'espn:<id>')
    minutes: float
    played: bool


def load_player_box(season: int) -> tuple[dict, dict]:
    """(appearances by athlete id in date order, {(athlete id): display name}). Rows without an athlete id are skipped."""
    _check_season(season)
    apps: dict[str, list[Appearance]] = defaultdict(list)
    names: dict[str, str] = {}
    with player_box_path(season).open(newline="") as f:
        for r in csv.DictReader(f):
            pid = r.get("athlete_id")
            if not pid or pid == "NA":
                continue
            dnp = str(r.get("did_not_play", "")).lower() == "true"
            raw_min = r.get("minutes") or ""
            try:
                minutes = 0.0 if dnp or raw_min in ("", "NA") else float(raw_min)
            except ValueError:
                minutes = 0.0
            tip = datetime.fromisoformat(r["game_date_time"].replace("Z", "+00:00")) if r.get("game_date_time") \
                else datetime.fromisoformat(r["game_date"])
            day = (tip.astimezone(ET) if tip.tzinfo else tip).strftime("%Y-%m-%d")
            apps[pid].append(Appearance(day, season, str(r.get("team_id", "")), minutes, minutes > 0))
            names[pid] = r.get("athlete_display_name") or names.get(pid, "")
    for v in apps.values():
        v.sort(key=lambda a: a.date)
    return apps, names


# --- Reports ------------------------------------------------------------------------------------------------------
def report_name(day: str) -> str:
    """5:30 PM ET report for an ET date. Legacy hourly names (before 2025-12-22) say '05PM' for the 5:30 report."""
    if day >= "2025-12-22":
        return nba_injury.file_name(datetime.fromisoformat(f"{day}T17:30:00").replace(tzinfo=ET))
    return f"Injury-Report_{day}_05PM.pdf"


def report_dates(conn: sqlite3.Connection) -> list[str]:
    """ET dates with at least one eligible game tipping off at or after 18:00 ET, in the research seasons."""
    days = set()
    for r in conn.execute(f"SELECT tipoff_at FROM games WHERE status = 'final' AND season IN ({', '.join('?' * len(SEASONS))}) "
                          f"AND season_type IN ({', '.join('?' * len(backtest.SEASON_TYPES))})",
                          (*SEASONS, *backtest.SEASON_TYPES)):
        local = datetime.strptime(r[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(ET)
        if (local.hour, local.minute) >= EARLIEST_TIPOFF:
            days.add(local.strftime("%Y-%m-%d"))
    return sorted(days)


def download_reports(conn: sqlite3.Connection, *, fetch: Callable[[str], nba_injury.Response] = nba_injury.fetch,
                     sleep: Callable[[float], None] = time.sleep, limit: Optional[int] = None,
                     log: Callable[[str], None] = lambda _m: None) -> dict:
    """At most one stored report per date; already-stored dates make no request. Each attempt is logged."""
    out = {"dates": 0, "already_stored": 0, "stored": 0, "not_available": 0, "failed": 0, "requests": 0}
    rdir = research_dir() / "reports"
    rdir.mkdir(parents=True, exist_ok=True)
    for day in report_dates(conn):
        if limit is not None and out["requests"] >= limit:
            break
        out["dates"] += 1
        name = report_name(day)
        if (rdir / name).exists():
            out["already_stored"] += 1
            continue
        for attempt in range(1 + MAX_RETRIES):
            if out["requests"]:
                sleep(REQUEST_SPACING)
            started = utc_now()
            out["requests"] += 1
            try:
                resp = fetch(name)
                completed = utc_now()   # read only after the whole body has arrived
            except Exception as exc:
                _log_attempt({"kind": "report", "name": name, "started_at": started, "completed_at": utc_now(),
                              "outcome": "failed", "error": f"{type(exc).__name__}: {exc}"})
                if attempt == MAX_RETRIES:
                    out["failed"] += 1
                continue
            if resp.status in (403, 404):
                _log_attempt({"kind": "report", "name": name, "started_at": started, "completed_at": completed,
                              "http_status": resp.status, "outcome": "not_available"})
                out["not_available"] += 1
                break
            problem = nba_injury.problem(resp) if resp.status == 200 else f"HTTP {resp.status}"
            if problem:
                _log_attempt({"kind": "report", "name": name, "started_at": started, "completed_at": completed,
                              "http_status": resp.status, "outcome": "failed", "error": problem})
                if attempt == MAX_RETRIES:
                    out["failed"] += 1
                continue
            meta = {"name": name, "date": day, "http_status": resp.status, "last_modified": resp.last_modified,
                    "etag": resp.etag, "bytes": len(resp.body), "sha256": hashlib.sha256(resp.body).hexdigest(),
                    "retrieved_started_at": started, "retrieved_at": completed}
            (rdir / name).write_bytes(resp.body)
            (rdir / (name + ".json")).write_text(json.dumps(meta, indent=1))
            _log_attempt({"kind": "report", "name": name, "started_at": started, "completed_at": completed,
                          "http_status": resp.status, "outcome": "stored", "sha256": meta["sha256"]})
            out["stored"] += 1
            break
        if out["requests"] % 50 == 0:
            log(json.dumps(out))
    return out


# --- Parser (both producers) ----------------------------------------------------------------------------------------
STATUSES = "Out|Questionable|Doubtful|Probable|Available"
_W = r"[A-Z][A-Za-z'’.\-]*"
ENTRY = re.compile(rf"((?:{_W}[ ]{{1,4}}){{0,2}}{_W})[ ]*,[ ]*({_W}(?:[ ]{_W})?)[ ]{{2,}}({STATUSES})\b")
LOOSE = re.compile(rf"[A-Za-z'’.\-]+[ ]*,[ ]*[A-Za-z'’.\-]+.*?[ ]{{2,}}({STATUSES})\b")
HEADER = re.compile(r"Injury\s+Report:\s+(\d\d)/(\d\d)/(\d\d)\s+(\d\d):(\d\d)\s+(AM|PM)")
GAME_DATE = re.compile(r"\b(\d\d)/(\d\d)/(\d{4})\b")


@dataclass
class ParsedReport:
    name: str
    header_published: Optional[datetime]
    entries: list[dict] = field(default_factory=list)          # game_date, team, last, first, status
    not_submitted: list[dict] = field(default_factory=list)    # game_date, team
    producer: str = ""
    loose_lines: int = 0

    @property
    def reconciled(self) -> bool:
        return len(self.entries) == self.loose_lines


COLUMNS = ("Game Date", "Game Time", "Matchup", "Team", "Player Name", "Category", "Reason", "Current Status",
           "Previous Status")
STATUS_SET = set(STATUSES.split("|"))
NAME = re.compile(r"^([^,]+?)\s*,\s*(.+)$")


def _columns(header: str) -> dict[str, int]:
    """Start position of each column label found in a header row (layout text keeps positions)."""
    found = {}
    for label in COLUMNS:
        i = header.find(label)
        if i >= 0 and not (label == "Status" and "Current Status" in header):
            found[label] = i
    if "Current Status" not in found:   # later layouts label the column just "Status"? keep both possibilities
        i = re.search(r"(?<!Previous )\bStatus\b", header)
        if i:
            found["Current Status"] = i.start()
    return found


COLUMN_MARGIN = 3   # data rows can start a character or two left of their header label


def _field(line: str, cols: dict[str, int], label: str) -> str:
    if label not in cols:
        return ""
    starts = sorted(cols.values())
    a = cols[label]
    later = [x for x in starts if x > a]
    return line[max(0, a - COLUMN_MARGIN):(later[0] - COLUMN_MARGIN) if later else None].strip()


def parse_report(pdf: bytes, name: str, team_names: list[str]) -> ParsedReport:
    """Column-position parser for both producers (text extraction here; the line logic is in parse_layout)."""
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    producer = str((reader.metadata or {}).get("/Producer", ""))
    reverse = "GemBox" in producer    # this producer's page text comes out bottom-to-top
    pages = []
    for page in reader.pages:
        lines = page.extract_text(extraction_mode="layout").splitlines()
        pages.append((" ".join(page.extract_text().split()), list(reversed(lines)) if reverse else lines))
    out = parse_layout(pages, name, team_names)
    out.producer = producer
    return out


def parse_layout(pages: list[tuple[str, list[str]]], name: str, team_names: list[str]) -> ParsedReport:
    """`pages` = (plain page text for the header time, layout lines in reading order). Each page's header row gives the
    column starts; game date and team carry down from the rows above. `loose_lines` counts rows with a valid status in the
    status column (reconciliation: every such row must yield a parsed name)."""
    teams_by_key = {" ".join(t.split()).lower(): t for t in team_names}
    out = ParsedReport(name=name, header_published=None)
    game_date = team = None
    for plain, lines in pages:
        header = HEADER.search(plain)
        if header and out.header_published is None:
            mm, dd, yy, hh, mi, ap = header.groups()
            hour = int(hh) % 12 + (12 if ap == "PM" else 0)
            out.header_published = datetime(2000 + int(yy), int(mm), int(dd), hour, int(mi), tzinfo=ET).astimezone(timezone.utc)
        cols: dict[str, int] = {}
        for line in lines:
            if "Player Name" in line and "Status" in line:
                cols = _columns(line)
                continue
            if not cols or not line.strip():
                continue
            d = GAME_DATE.search(_field(line, cols, "Game Date"))
            if d:
                game_date = f"{d.group(3)}-{d.group(1)}-{d.group(2)}"
            tm = " ".join(_field(line, cols, "Team").split())
            if tm and tm.lower() in teams_by_key:
                team = teams_by_key[tm.lower()]
            if "NOT YET SUBMITTED" in " ".join(line.upper().split()):
                out.not_submitted.append({"game_date": game_date, "team": team})
                continue
            status_text = _field(line, cols, "Current Status")
            status = status_text.split(" ")[0] if status_text else ""
            if status not in STATUS_SET:
                continue
            out.loose_lines += 1
            m = NAME.match(" ".join(_field(line, cols, "Player Name").split()))
            if m:
                out.entries.append({"game_date": game_date, "team": team, "last": m.group(1), "first": m.group(2),
                                    "status": status})
    return out


def publication_time(parsed: ParsedReport, last_modified: Optional[str]) -> tuple[Optional[datetime], str]:
    """Audit gate 4: header time (ET); if Last-Modified differs by more than 2 minutes, the later one, flagged; without a
    header time or Last-Modified the report is excluded (None)."""
    if parsed.header_published is None:
        return None, "no_header_time"
    if not last_modified:
        return None, "no_last_modified"
    lm = parsedate_to_datetime(last_modified).astimezone(timezone.utc)
    if abs(lm - parsed.header_published) > LAST_MODIFIED_TOLERANCE:
        return max(lm, parsed.header_published), "flagged_last_modified_differs"
    return parsed.header_published, "header"


# --- Player matching ----------------------------------------------------------------------------------------------
ALIAS_PATH = Path(__file__).parent / "reference" / "nba_player_aliases.csv"
_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?")


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = _SUFFIX.sub("", s)
    return re.sub(r"[^a-z]", "", s)


def load_aliases(path: Path = ALIAS_PATH) -> dict[str, str]:
    with path.open(newline="") as f:
        return {norm_name(r["report_name"]): r["athlete_id"] for r in csv.DictReader(f) if r.get("athlete_id")}


class PlayerIndex:
    """Report name -> ESPN athlete id, in the preregistered order; ambiguity or no match is 'unresolved'."""

    def __init__(self, box: dict[int, tuple[dict, dict]], aliases: dict[str, str]):
        self.by_team_season: dict[tuple, dict[str, set]] = defaultdict(lambda: defaultdict(set))
        self.by_team: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
        self.by_season: dict[int, dict[str, set]] = defaultdict(lambda: defaultdict(set))
        for season, (apps, names) in box.items():
            for pid, rows in apps.items():
                key = norm_name(names.get(pid, ""))
                if not key:
                    continue
                self.by_season[season][key].add(pid)
                for a in rows:
                    self.by_team_season[(a.team_id, season)][key].add(pid)
                    self.by_team[a.team_id][key].add(pid)
        self.aliases = aliases

    def match(self, last: str, first: str, team_id: str, season: int) -> tuple[Optional[str], str]:
        key = norm_name(f"{first} {last}")
        for level, ids in (("team_season", self.by_team_season[(team_id, season)].get(key, set())),
                           ("team_any_season", self.by_team[team_id].get(key, set())),
                           ("league_unique", self.by_season[season].get(key, set()) | self.by_season[season - 1].get(key, set()))):
            if len(ids) == 1:
                return next(iter(ids)), level
            if len(ids) > 1:
                return None, "ambiguous"
        if key in self.aliases:
            return self.aliases[key], "alias"
        return None, "unresolved"


# --- Features ---------------------------------------------------------------------------------------------------------
def season_of(day: str) -> int:
    return int(day[:4]) + (1 if int(day[5:7]) >= 8 else 0)


def evening_cutoff(tipoff_at: str) -> Optional[str]:
    """17:45 ET on the game's ET date, or None if the game tips off before 18:00 ET (excluded from scoring)."""
    tip = datetime.strptime(tipoff_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(ET)
    if (tip.hour, tip.minute) < EARLIEST_TIPOFF:
        return None
    return tip.replace(hour=CUTOFF_HOUR, minute=CUTOFF_MINUTE, second=0, microsecond=0).astimezone(timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")


class MinutesHistory:
    def __init__(self, box: dict[int, tuple[dict, dict]]):
        self.apps: dict[str, list[Appearance]] = defaultdict(list)
        self.team_dates: dict[str, list[str]] = defaultdict(list)     # team -> sorted game dates
        self.team_game: dict[tuple, set] = defaultdict(set)           # (team, date) -> ids with minutes > 0
        for _, (apps, _) in sorted(box.items()):
            for pid, rows in apps.items():
                self.apps[pid].extend(rows)
                for a in rows:
                    if a.played:
                        self.team_game[(a.team_id, a.date)].add(pid)
                    self.team_dates[a.team_id].append(a.date)
        for v in self.apps.values():
            v.sort(key=lambda a: a.date)
        for t in self.team_dates:
            self.team_dates[t] = sorted(set(self.team_dates[t]))

    def expected(self, pid: str, day: str) -> tuple[float, str]:
        """Last-10 mean minutes (games played) before `day`: this season if ≥ 3, else previous season, else 0."""
        season = season_of(day)
        before = [a for a in self.apps.get(pid, []) if a.date < day and a.played]
        this = [a.minutes for a in before if a.season == season][-10:]
        if len(this) >= 3:
            return sum(this) / len(this), "this_season"
        prev = [a.minutes for a in before if a.season == season - 1][-10:]
        if prev:
            return sum(prev) / len(prev), "previous_season"
        return 0.0, "no_history"

    def played_last_team_game(self, pid: str, team_id: str, day: str) -> bool:
        dates = [d for d in self.team_dates.get(team_id, []) if d < day]
        return bool(dates) and pid in self.team_game.get((team_id, dates[-1]), set())


@dataclass
class ReportFacts:
    usable: bool
    reason: str
    published_at: Optional[str]
    absent: dict = field(default_factory=dict)         # team_id -> {"new": minutes, "all": minutes}
    unsubmitted: set = field(default_factory=set)      # team ids
    teams_on_report: set = field(default_factory=set)


def build_report_facts(conn: sqlite3.Connection, box: dict, *, log: Callable[[str], None] = lambda _m: None) -> tuple[dict, dict]:
    """{ET date: ReportFacts} for every research date, plus coverage counts. Uses only the report and earlier box scores."""
    team_names = {r["name"]: r["team_id"].split(":", 1)[1] for r in conn.execute(
        "SELECT team_id, name FROM teams WHERE CAST(substr(team_id, 6) AS INTEGER) BETWEEN 1 AND 30")}
    idx = PlayerIndex(box, load_aliases())
    hist = MinutesHistory(box)
    cov = defaultdict(int)
    match_levels = defaultdict(lambda: defaultdict(int))
    facts = {}
    rdir = research_dir() / "reports"
    for day in report_dates(conn):
        cov["dates"] += 1
        name = report_name(day)
        path = rdir / name
        if not path.exists():
            facts[day] = ReportFacts(False, "missing", None)
            cov["report_missing"] += 1
            continue
        meta = json.loads((rdir / (name + ".json")).read_text())
        try:
            parsed = parse_report(path.read_bytes(), name, list(team_names))
        except Exception as exc:   # an unreadable file is excluded and counted, never guessed
            facts[day] = ReportFacts(False, f"parse_error:{type(exc).__name__}", None)
            cov["parse_error"] += 1
            continue
        pub, basis = publication_time(parsed, meta.get("last_modified"))
        cov[f"publication_{basis}"] += 1
        cutoff = datetime.fromisoformat(f"{day}T{CUTOFF_HOUR:02d}:{CUTOFF_MINUTE:02d}:00").replace(tzinfo=ET)
        if pub is None or not parsed.reconciled or pub > cutoff:
            reason = basis if pub is None else "unreconciled" if not parsed.reconciled else "published_after_cutoff"
            facts[day] = ReportFacts(False, reason, None if pub is None else pub.strftime("%Y-%m-%dT%H:%M:%SZ"))
            cov[f"excluded_{reason}"] += 1
            continue
        f = ReportFacts(True, "ok", pub.strftime("%Y-%m-%dT%H:%M:%SZ"))
        for ns in parsed.not_submitted:
            if ns["game_date"] == day and ns["team"] in team_names:
                f.unsubmitted.add(team_names[ns["team"]])
        season = season_of(day)
        for e in parsed.entries:
            if e["game_date"] != day:
                continue
            team = team_names[e["team"]]
            f.teams_on_report.add(team)
            w = STATUS_WEIGHT[e["status"]]
            pid, level = idx.match(e["last"], e["first"], team, season)
            match_levels[season][level] += 1
            if w == 0:
                continue
            slot = f.absent.setdefault(team, {"new": 0.0, "all": 0.0, "unresolved": 0, "no_history": 0})
            if pid is None:
                slot["unresolved"] += 1
                continue
            minutes, basis_m = hist.expected(pid, day)
            if basis_m == "no_history":
                slot["no_history"] += 1
            slot["all"] += w * minutes
            if hist.played_last_team_game(pid, team, day):
                slot["new"] += w * minutes
        facts[day] = f
        cov["usable"] += 1
    return facts, {"coverage": dict(cov), "match_levels": {s: dict(v) for s, v in match_levels.items()}}


def game_features(facts: dict, g) -> dict:
    """The three availability features for a PregameGame (positive favours home)."""
    day = datetime.strptime(g.tipoff_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(ET).strftime("%Y-%m-%d")
    f = facts.get(day)
    h, a = g.home_team_id.split(":", 1)[1], g.away_team_id.split(":", 1)[1]
    if f is None or not f.usable or h in f.unsubmitted or a in f.unsubmitted:
        return {"new_absent_diff": 0.0, "all_absent_diff": 0.0, "report_missing": 1.0}
    zero = {"new": 0.0, "all": 0.0}
    hh, aa = f.absent.get(h, zero), f.absent.get(a, zero)
    return {"new_absent_diff": (aa["new"] - hh["new"]) / 48, "all_absent_diff": (aa["all"] - hh["all"]) / 48,
            "report_missing": 0.0}


# --- Candidate and evaluation -----------------------------------------------------------------------------------------
AVAIL_FEATURES = ("new_absent_diff", "all_absent_diff", "report_missing")


def avail_v1(elo_cfg, facts: dict):
    from .models.logistic import V2_FEATURES, V2_SCALED, LogisticV2

    class AvailV1(LogisticV2):
        name = "avail_v1"
        feature_names = V2_FEATURES + AVAIL_FEATURES
        scaled_names = V2_SCALED + ("new_absent_diff", "all_absent_diff")

        def _features(self, g):
            return {**super()._features(g), **game_features(facts, g)}
    return AvailV1(elo_cfg)


def _pearson(a: list[float], b: list[float]) -> Optional[float]:
    n = len(a)
    if n < 2:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return None if va == 0 or vb == 0 else sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb) ** 0.5


def evaluate(conn: sqlite3.Connection, out_dir: Optional[Path] = None, log: Callable[[str], None] = print) -> dict:
    import dataclasses
    from . import metrics
    from .models.elo import Elo
    from .models.logistic import LogisticV2
    from .nhl_eval import _score, _subset_diff_bootstrap
    decision, elo_cfg = backtest.load_frozen_decision(conn)
    box = {s: load_player_box(s) for s in SEASONS}
    facts, coverage = build_report_facts(conn, box)
    rows = backtest.load_rows(conn, "validation")              # 2019-20 … 2023-24 only
    if any(r.game.season not in SEASONS for r in rows):
        raise AvailabilityError("Availability research must not read seasons outside 2019-20 … 2023-24")
    rows = [dataclasses.replace(r, cutoff_at=evening_cutoff(r.game.tipoff_at) or r.cutoff_at) for r in rows]
    scored_ids = {r.game.game_id for r in rows if r.game.season in SCORED and evening_cutoff(r.game.tipoff_at)}
    excluded_early = sum(1 for r in rows if r.game.season in SCORED and not evening_cutoff(r.game.tipoff_at))
    events = backtest.load_box_events(conn, "validation")
    models = {"elo": Elo(elo_cfg), "logistic_v2": LogisticV2(elo_cfg), "avail_v1": avail_v1(elo_cfg, facts)}
    preds = {}
    for n, m in models.items():
        out = backtest.run(m, rows, events)
        preds[n] = sorted((p for p in out if p["game_id"] in scored_ids), key=lambda p: (p["cutoff_at"], p["game_id"]))
    by_game = {n: {p["game_id"]: p for p in v} for n, v in preds.items()}
    ids = [p["game_id"] for p in preds["elo"]]
    g_by_id = {r.game.game_id: r.game for r in rows}
    missing = sum(game_features(facts, g_by_id[g])["report_missing"] for g in ids)

    def pk(name):
        return [{**by_game[name][g]} for g in ids]

    report = {"stage": "NBA availability development research (exploratory: these seasons are spent)",
              "generated_at": utc_now(), "scored_seasons": SCORED, "cutoff": "17:45 ET on game day (games from 18:00 ET)",
              "dataset_version": backtest.dataset_version(rows), "frozen_elo": decision["elo_config"],
              "scored_games": len(ids), "excluded_early_tipoffs": excluded_early,
              "scored_games_without_usable_report_or_unsubmitted_team": int(missing),
              "report_coverage": coverage,
              "model_versions": {n: backtest.model_version(m) for n, m in models.items()},
              "first_fit_season": models["avail_v1"].oos_from_season(),
              "avail_v1_weights_last_fit": dict(zip(("intercept",) + models["avail_v1"].feature_names,
                                                    [round(w, 4) for w in models["avail_v1"].weights])),
              "models": {n: {**_score(pk(n), "p_home"), "by_season": backtest.by_season(pk(n)),
                             "calibration": backtest.summarize(pk(n))["calibration"]} for n in models},
              "avail_minus_logistic_v2_brier": backtest.paired_bootstrap(pk("avail_v1"), pk("logistic_v2")),
              "avail_minus_elo_brier": backtest.paired_bootstrap(pk("avail_v1"), pk("elo")),
              "logistic_v2_minus_elo_brier": backtest.paired_bootstrap(pk("logistic_v2"), pk("elo")),
              "avail_minus_logistic_v2_accuracy": backtest.paired_accuracy_bootstrap(pk("avail_v1"), pk("logistic_v2")),
              "avail_minus_elo_accuracy": backtest.paired_accuracy_bootstrap(pk("avail_v1"), pk("elo"))}
    # log loss paired (difference of per-game log losses), week blocks
    import math

    def ll(p, y):
        p = min(1 - 1e-15, max(1e-15, p))
        return -math.log(p if y else 1 - p)
    for a, b in (("avail_v1", "logistic_v2"), ("avail_v1", "elo")):
        diffs = [ll(by_game[a][g]["p_home"], by_game[a][g]["home_won"]) - ll(by_game[b][g]["p_home"], by_game[b][g]["home_won"])
                 for g in ids]
        report[f"{a}_minus_{b}_log_loss"] = backtest.block_bootstrap(diffs, [by_game[a][g]["cutoff_at"] for g in ids])
    # combinations (fixed set)
    games = []
    for g in ids:
        e, v = by_game["elo"][g], by_game["avail_v1"][g]
        avg = (e["p_home"] + v["p_home"]) / 2
        y = e["home_won"]
        games.append({"game_id": g, "cutoff_at": e["cutoff_at"], "home_won": y, "p_elo": e["p_home"], "p_avail": v["p_home"],
                      "p_avg": avg, "c_elo": max(e["p_home"], 1 - e["p_home"]), "c_avail": max(v["p_home"], 1 - v["p_home"]),
                      "ok_elo": int((e["p_home"] >= 0.5) == y), "ok_avail": int((v["p_home"] >= 0.5) == y),
                      "ok_avg": int((avg >= 0.5) == y), "agree": (e["p_home"] > 0.5) == (v["p_home"] > 0.5)})
    avg_preds = [{"game_id": x["game_id"], "cutoff_at": x["cutoff_at"], "p_home": x["p_avg"], "home_won": x["home_won"]} for x in games]
    comb = {"average_elo_avail": {**_score(games, "p_avg"),
                                  "minus_elo_brier": backtest.paired_bootstrap(avg_preds, pk("elo")),
                                  "minus_avail_brier": backtest.paired_bootstrap(avg_preds, pk("avail_v1"))}}
    agree = [x for x in games if x["agree"]]

    def top(key, k):
        return sorted(games, key=lambda x: (-x[key], x["game_id"]))[:k]
    comb["agreement_elo_avail"] = {
        **_score(agree, "p_avg"), "coverage": len(agree) / len(games),
        "elo_on_subset": _score(agree, "p_elo"), "avail_on_subset": _score(agree, "p_avail"),
        "vs_elo_filter_same_coverage": {**_score(top("c_elo", len(agree)), "p_elo"),
                                        "diff": _subset_diff_bootstrap(agree, top("c_elo", len(agree)), "ok_avg", "ok_elo")},
        "vs_avail_filter_same_coverage": {**_score(top("c_avail", len(agree)), "p_avail"),
                                          "diff": _subset_diff_bootstrap(agree, top("c_avail", len(agree)), "ok_avg", "ok_avail")}}
    for share in (0.5, 0.25):
        k = int(round(share * len(games)))
        a_top, e_top = top("c_avail", k), top("c_elo", k)
        comb[f"top_{int(share * 100)}pct"] = {
            "avail_v1": _score(a_top, "p_avail"), "elo": _score(e_top, "p_elo"),
            "avail_minus_elo_accuracy": _subset_diff_bootstrap(a_top, e_top, "ok_avail", "ok_elo"),
            "elo_on_avail_subset": _score(a_top, "p_elo"), "avail_on_elo_subset": _score(e_top, "p_avail")}
    report["combinations"] = comb
    # diagnostics: correlation of per-game Brier losses, disagreement
    def losses(n):
        return [(by_game[n][g]["p_home"] - float(by_game[n][g]["home_won"])) ** 2 for g in ids]
    dis = [x for x in games if not x["agree"]]
    report["diagnostics"] = {
        "brier_loss_correlation": {"avail_vs_elo": _pearson(losses("avail_v1"), losses("elo")),
                                   "avail_vs_logistic_v2": _pearson(losses("avail_v1"), losses("logistic_v2"))},
        "disagreements_with_elo": {"n": len(dis), "share": len(dis) / len(games),
                                   "avail_right": sum(x["ok_avail"] for x in dis), "elo_right": sum(x["ok_elo"] for x in dis)}}
    passes = report["avail_minus_logistic_v2_brier"]["ci95"][1] < 0
    report["decision"] = {"rule": "EVALUATION_PLAN 'NBA availability candidate': upper 95% bound of Brier(avail_v1 - logistic_v2) < 0",
                          "availability_adds_value": passes}
    out_dir = Path(out_dir or config.data_dir() / "reports" / f"nba-availability-{report['generated_at'].replace(':', '')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    report["output_dir"] = str(out_dir)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    log(f"Report written to {out_dir}")
    return report
