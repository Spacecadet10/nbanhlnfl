"""NBA player ratings and injuries, research study `players-v1` (docs/PLAYER_RATINGS_STUDY.md). Exploratory research only:
never imported by the API, the scheduled jobs or any live definition.

Game-by-game player ratings (points of team margin per 48 minutes) are updated only after a result is delivered by the
walk-forward runner; team strength at the 17:45 ET cutoff is the rating of the expected roster (players who recently played for
the team, at their expected minutes), optionally after removing players listed on the 5:30 PM injury report.

`app/availability.py` and the other files hashed into live definitions are imported, never edited: changing them would restart
live research records. Season-restricted helpers there are re-implemented here with an explicit season list.
"""
import bisect
import csv
import dataclasses
import hashlib
import json
import math
import random
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import availability as av
from . import backtest, config
from .db import utc_now
from .models.base import PregameGame, Result
from .sources import nba_injury

STUDY = "players-v1"
ALL_SEASONS = (2020, 2021, 2022, 2023, 2024, 2025, 2026)
DEV_SEASONS = (2020, 2021, 2022, 2023, 2024)          # loadable by the development walk (period "validation")
DEV_SCORED = (2022, 2023, 2024)
RECENT_SCORED = (2025, 2026)                          # previously reserved test seasons: retrospective, logged access
K_GRID = (0.01, 0.02, 0.04)
K_SELECTION_SEASON = 2021                             # warm-up season used once to choose K
NEWCOMER_PRIOR = -1.0
SEASON_CARRY = 0.75
HOME_EDGE = 2.5
ROSTER_RECENT_GAMES = 5
TEAM_MINUTES = 240.0
PRIMARY_ALPHA = 0.05 / 2                                # Bonferroni over the two primary comparisons
THRESHOLDS = (0.60, 0.65, 0.70, 0.75)


class PlayersError(RuntimeError):
    pass


# --- Data (research copy only) ----------------------------------------------------------------------------------------
def download_player_box(season: int, get: Optional[Callable] = None) -> dict:
    """availability.download_player_box without its research-window restriction; same digest check and attempt log."""
    if season not in ALL_SEASONS:
        raise PlayersError(f"season {season} is outside {ALL_SEASONS}")
    import httpx
    get = get or (lambda url: httpx.get(url, timeout=120, follow_redirects=True))
    name = f"player_box_{season}.csv"
    release = get(av.PLAYER_BOX_RELEASE).json()
    digest = next((a.get("digest") for a in release.get("assets", []) if a["name"] == name), None)
    started = utc_now()
    resp = get(av.PLAYER_BOX_DOWNLOAD.format(name=name))
    completed = utc_now()
    body = resp.content
    sha = "sha256:" + hashlib.sha256(body).hexdigest()
    ok = resp.status_code == 200 and digest is not None and sha == digest
    av._log_attempt({"kind": "player_box", "study": STUDY, "name": name, "started_at": started, "completed_at": completed,
                     "http_status": resp.status_code, "bytes": len(body), "sha256": sha, "published_digest": digest,
                     "outcome": "stored" if ok else "digest_mismatch" if resp.status_code == 200 else "failed"})
    if not ok:
        raise PlayersError(f"{name}: status {resp.status_code}, digest {sha} vs published {digest}")
    path = av.player_box_path(season)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return {"season": season, "bytes": len(body), "sha256": sha}


def date_seasons(conn: sqlite3.Connection, seasons: tuple) -> dict[str, int]:
    """availability.date_seasons for an explicit season list: {ET date: season} of evening (>= 18:00 ET) final games."""
    days: dict[str, int] = {}
    for r in conn.execute(f"SELECT tipoff_at, season FROM games WHERE status = 'final' AND season IN ({', '.join('?' * len(seasons))}) "
                          f"AND season_type IN ({', '.join('?' * len(backtest.SEASON_TYPES))})", (*seasons, *backtest.SEASON_TYPES)):
        local = datetime.strptime(r[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(av.ET)
        if (local.hour, local.minute) >= av.EARLIEST_TIPOFF:
            days[local.strftime("%Y-%m-%d")] = r[1]
    return days


def download_reports(conn: sqlite3.Connection, seasons: tuple, *, fetch: Callable = nba_injury.fetch,
                     sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = lambda _m: None) -> dict:
    """The 5:30 PM report for each evening date of `seasons`, with availability.download_reports' rules: at most one stored
    report per date, already-stored dates make no request, every attempt logged, 1.2 s between requests."""
    out = {"dates": 0, "already_stored": 0, "stored": 0, "not_available": 0, "failed": 0, "requests": 0}
    rdir = av.research_dir() / "reports"
    rdir.mkdir(parents=True, exist_ok=True)
    for day in sorted(date_seasons(conn, seasons)):
        out["dates"] += 1
        name = av.report_name(day)
        if (rdir / name).exists():
            out["already_stored"] += 1
            continue
        for attempt in range(1 + av.MAX_RETRIES):
            if out["requests"]:
                sleep(av.REQUEST_SPACING)
            started = utc_now()
            out["requests"] += 1
            try:
                resp = fetch(name)
                completed = utc_now()
            except Exception as exc:
                av._log_attempt({"kind": "report", "study": STUDY, "name": name, "started_at": started,
                                 "completed_at": utc_now(), "outcome": "failed", "error": f"{type(exc).__name__}: {exc}"})
                if attempt == av.MAX_RETRIES:
                    out["failed"] += 1
                continue
            if resp.status in (403, 404):
                av._log_attempt({"kind": "report", "study": STUDY, "name": name, "started_at": started,
                                 "completed_at": completed, "http_status": resp.status, "outcome": "not_available"})
                out["not_available"] += 1
                break
            problem = nba_injury.problem(resp) if resp.status == 200 else f"HTTP {resp.status}"
            if problem:
                av._log_attempt({"kind": "report", "study": STUDY, "name": name, "started_at": started,
                                 "completed_at": completed, "http_status": resp.status, "outcome": "failed", "error": problem})
                if attempt == av.MAX_RETRIES:
                    out["failed"] += 1
                continue
            meta = {"name": name, "date": day, "http_status": resp.status, "last_modified": resp.last_modified,
                    "etag": resp.etag, "bytes": len(resp.body), "sha256": hashlib.sha256(resp.body).hexdigest(),
                    "retrieved_started_at": started, "retrieved_at": completed}
            (rdir / name).write_bytes(resp.body)
            (rdir / (name + ".json")).write_text(json.dumps(meta, indent=1))
            av._log_attempt({"kind": "report", "study": STUDY, "name": name, "started_at": started, "completed_at": completed,
                             "http_status": resp.status, "outcome": "stored", "sha256": meta["sha256"]})
            out["stored"] += 1
            break
        if out["requests"] and out["requests"] % 50 == 0:
            log(json.dumps(out))
    return out


def _box_rows(season: int) -> list[dict]:
    with av.player_box_path(season).open(newline="") as f:
        return list(csv.DictReader(f))


@dataclasses.dataclass
class Box:
    """Player box scores for a set of seasons: availability's per-player appearances, plus per-game minutes by team."""
    seasons: tuple
    apps: dict            # {season: (appearances by athlete id, names)} as availability.parse_player_rows
    games: dict           # {store game id: {espn team id: [(athlete id, minutes), ...]}} (players with minutes > 0)
    digest: str


def load_box(seasons: tuple, loader: Callable[[int], list] = _box_rows) -> Box:
    apps, games, h = {}, defaultdict(lambda: defaultdict(list)), hashlib.sha256()
    for season in seasons:
        rows = loader(season)
        apps[season] = av.parse_player_rows(rows, season)
        for r in rows:
            pid = r.get("athlete_id")
            if not pid or pid == "NA" or str(r.get("did_not_play", "")).lower() == "true":
                continue
            try:
                minutes = float(r.get("minutes") or 0)
            except ValueError:
                continue
            if minutes > 0:
                games[f"espn:{r['game_id']}"][str(r.get("team_id", ""))].append((pid, minutes))
        if loader is _box_rows:
            h.update(av.player_box_path(season).read_bytes())
    return Box(seasons, apps, {g: dict(t) for g, t in games.items()}, h.hexdigest()[:16])


# --- Report facts with per-player absences ---------------------------------------------------------------------------
@dataclasses.dataclass
class PlayerFacts(av.ReportFacts):
    """availability.ReportFacts plus the weighted absent players per team ({team: {athlete id: weight}})."""
    players: dict = dataclasses.field(default_factory=dict)
    entries: dict = dataclasses.field(default_factory=dict)    # {team: [(athlete id, weight)]}, one per matched entry


def build_facts(conn: sqlite3.Connection, box: Box, seasons: tuple) -> tuple[dict, dict]:
    """availability.build_report_facts for `seasons`, also keeping each matched absent player and his status weight.
    The `absent` minutes are computed by the same rules, so `avail_v1` sees identical features (checked in tests)."""
    team_names = {r["name"]: r["team_id"].split(":", 1)[1] for r in conn.execute(
        "SELECT team_id, name FROM teams WHERE CAST(substr(team_id, 6) AS INTEGER) BETWEEN 1 AND 30")}
    idx = av.PlayerIndex(box.apps, av.load_aliases())
    hist = av.MinutesHistory(box.apps)
    cov: dict = defaultdict(int)
    match_levels: dict = defaultdict(lambda: defaultdict(int))
    facts = {}
    rdir = av.research_dir() / "reports"
    days = date_seasons(conn, seasons)
    for day in sorted(days):
        cov["dates"] += 1
        name = av.report_name(day)
        path = rdir / name
        if not path.exists():
            facts[day] = PlayerFacts(False, "missing", None)
            cov["report_missing"] += 1
            continue
        meta = json.loads((rdir / (name + ".json")).read_text())
        try:
            parsed = av.parse_report(path.read_bytes(), name, list(team_names))
        except Exception as exc:
            facts[day] = PlayerFacts(False, f"parse_error:{type(exc).__name__}", None)
            cov["parse_error"] += 1
            continue
        pub, basis = av.publication_time(parsed, meta.get("last_modified"))
        cov[f"publication_{basis}"] += 1
        cutoff = datetime.fromisoformat(f"{day}T{av.CUTOFF_HOUR:02d}:{av.CUTOFF_MINUTE:02d}:00").replace(tzinfo=av.ET)
        if pub is None or not parsed.reconciled or pub > cutoff:
            reason = basis if pub is None else "unreconciled" if not parsed.reconciled else "published_after_cutoff"
            facts[day] = PlayerFacts(False, reason, None if pub is None else pub.strftime("%Y-%m-%dT%H:%M:%SZ"))
            cov[f"excluded_{reason}"] += 1
            continue
        f = PlayerFacts(True, "ok", pub.strftime("%Y-%m-%dT%H:%M:%SZ"))
        for ns in parsed.not_submitted:
            if ns["game_date"] == day and ns["team"] in team_names:
                f.unsubmitted.add(team_names[ns["team"]])
        season = days[day]
        for e in parsed.entries:
            if e["game_date"] != day:
                continue
            if e["team"] not in team_names:
                cov["entries_without_team"] += 1
                continue
            team = team_names[e["team"]]
            f.teams_on_report.add(team)
            w = av.STATUS_WEIGHT[e["status"]]
            pid, level = idx.match(e["last"], e["first"], team, season)
            match_levels[season][level] += 1
            if w == 0:
                continue
            slot = f.absent.setdefault(team, {"new": 0.0, "all": 0.0, "unresolved": 0, "no_history": 0})
            if pid is None:
                slot["unresolved"] += 1
                continue
            minutes, basis_m = hist.expected(pid, day, season)
            if basis_m == "no_history":
                slot["no_history"] += 1
            slot["all"] += w * minutes
            if hist.played_last_team_game(pid, team, day):
                slot["new"] += w * minutes
            f.entries.setdefault(team, []).append((pid, w))
            team_players = f.players.setdefault(team, {})
            team_players[pid] = max(team_players.get(pid, 0.0), w)
        facts[day] = f
        cov["usable"] += 1
    return facts, {"coverage": dict(cov), "match_levels": {s: dict(v) for s, v in match_levels.items()}}


def avail_parity(conn: sqlite3.Connection, box: Box, facts: dict) -> dict:
    """On the development seasons, build_facts must give availability.build_report_facts' exact facts (so B+I is avail_v1)."""
    ref, _ = av.build_report_facts(conn, box.apps)
    keys = ("usable", "reason", "published_at", "absent", "unsubmitted", "teams_on_report")
    bad = sorted(d for d in set(ref) | set(facts)
                 if d not in ref or d not in facts or any(getattr(ref[d], k) != getattr(facts[d], k) for k in keys))
    return {"dates": len(ref), "identical": not bad, "mismatched_dates": bad[:20]}


# --- Ratings ------------------------------------------------------------------------------------------------------------
class PlayerRatings:
    """Online player ratings. `update` is called only with a delivered result and that game's (post-game) minutes."""

    def __init__(self, k: float):
        self.k = k
        self.r: dict[str, float] = {}
        self.season: dict[str, int] = {}

    def get(self, pid: str, season: int) -> float:
        if pid not in self.r:
            return NEWCOMER_PRIOR
        return self.r[pid] * SEASON_CARRY if self.season[pid] < season else self.r[pid]

    def strength(self, players: list[tuple[str, float]], season: int) -> float:
        return sum(m / 48 * self.get(pid, season) for pid, m in players)

    def update(self, season: int, home: list[tuple[str, float]], away: list[tuple[str, float]], margin: float,
               neutral: bool) -> float:
        """margin = home minus away points. Returns the residual used."""
        e = margin - (self.strength(home, season) - self.strength(away, season) + (0.0 if neutral else HOME_EDGE))
        for side, sign in ((home, 1.0), (away, -1.0)):
            for pid, m in side:
                self.r[pid] = self.get(pid, season) + sign * self.k * (m / 48) * e
                self.season[pid] = season
        return e


class Rosters:
    """Pregame expected rosters built only from delivered games: `add` is called with a game's box when the walk-forward
    runner delivers its result, so a delayed result's minutes and lineup stay unseen until then (not merely until its date)."""

    def __init__(self):
        self.apps: dict[str, list[tuple]] = defaultdict(list)        # pid -> sorted [(date, order, season, team, minutes)]
        self.team_dates: dict[str, list[str]] = defaultdict(list)    # team -> sorted delivered game dates
        self.team_game: dict[tuple, set] = defaultdict(set)          # (team, date) -> ids with minutes > 0

    def add(self, day: str, season: int, teams: dict) -> None:
        for team, players in teams.items():
            if (team, day) not in self.team_game:
                bisect.insort(self.team_dates[team], day)
            for pid, m in players:
                self.team_game[(team, day)].add(pid)
                bisect.insort(self.apps[pid], (day, 0, season, team, m))

    @classmethod
    def from_apps(cls, apps: dict) -> "Rosters":
        """Every box appearance gated only by date (availability.MinutesHistory's view); used by the timing audit."""
        ros = cls()
        for season, (by_pid, _) in sorted(apps.items()):
            for pid, rows in by_pid.items():
                for a in rows:                         # order keeps MinutesHistory's same-date order (All-Star weekend)
                    key = (a.team_id, a.date)
                    if key not in ros.team_game:
                        bisect.insort(ros.team_dates[a.team_id], a.date)
                        ros.team_game[key] = set()
                    if a.played:
                        ros.team_game[key].add(pid)
                        ros.apps[pid].append((a.date, len(ros.apps[pid]), season, a.team_id, a.minutes))
        for rows in ros.apps.values():
            rows.sort()
        return ros

    @classmethod
    def from_games(cls, box: Box, games: list) -> "Rosters":
        """Every scored-universe game's box gated only by date (no delivery gate); separates timing from game coverage."""
        ros = cls()
        for g in games:
            teams = box.games.get(g.game_id, {})
            h, a = g.home_team_id.split(":", 1)[1], g.away_team_id.split(":", 1)[1]
            if h in teams and a in teams:
                ros.add(et_day(g.tipoff_at), g.season, {h: teams[h], a: teams[a]})
        return ros

    def absent(self, entries: list, team: str, day: str, season: int) -> dict:
        """availability's absent minutes for one team's report entries: {"new": .., "all": ..} (weight × expected minutes;
        "new" only for players who played the team's last game before `day`)."""
        dates = self.team_dates.get(team, [])
        i = bisect.bisect_left(dates, day)
        last = self.team_game.get((team, dates[i - 1]), set()) if i else set()
        out = {"new": 0.0, "all": 0.0}
        for pid, w in entries:
            m = self.minutes(pid, day, season)
            out["all"] += w * m
            if pid in last:
                out["new"] += w * m
        return out

    def last_team(self, pid: str, day: str) -> Optional[str]:
        rows = self.apps.get(pid, [])
        i = bisect.bisect_left(rows, (day,))
        return rows[i - 1][3] if i else None

    def minutes(self, pid: str, day: str, season: int) -> float:
        """Last-10 mean minutes before `day` (availability.MinutesHistory's rule): this season if ≥ 3, else previous, else 0."""
        rows = self.apps.get(pid, [])
        before = rows[:bisect.bisect_left(rows, (day,))]
        this = [m for _, _, s, _, m in before if s == season][-10:]
        if len(this) >= 3:
            return sum(this) / len(this)
        prev = [m for _, _, s, _, m in before if s == season - 1][-10:]
        return sum(prev) / len(prev) if prev else 0.0

    def expected(self, team: str, day: str, season: int) -> list[tuple[str, float]]:
        """[(athlete id, expected minutes)] scaled to 240: players whose latest delivered appearance before `day` was for
        `team` and who played in one of the team's last 5 delivered games before `day`."""
        dates = self.team_dates.get(team, [])
        recent = dates[:bisect.bisect_left(dates, day)][-ROSTER_RECENT_GAMES:]
        pids = set().union(*(self.team_game[(team, d)] for d in recent)) if recent else set()
        out = []
        for pid in sorted(pids):
            if self.last_team(pid, day) != team:
                continue
            m = self.minutes(pid, day, season)
            if m > 0:
                out.append((pid, m))
        return scale(out)


def scale(players: list[tuple[str, float]], total: float = TEAM_MINUTES) -> list[tuple[str, float]]:
    s = sum(m for _, m in players)
    return [(p, m * total / s) for p, m in players] if s > 0 else []


def without_absent(players: list[tuple[str, float]], absent: dict[str, float]) -> list[tuple[str, float]]:
    """Remove reported absences (weight 1 removes, 0.5 halves the minutes), then rescale to 240."""
    return scale([(p, m * (1.0 - absent.get(p, 0.0))) for p, m in players if absent.get(p, 0.0) < 1.0])


def et_day(tipoff_at: str) -> str:
    return datetime.strptime(tipoff_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(av.ET).strftime("%Y-%m-%d")


def gated_avail(rosters: "Rosters", f, g: PregameGame, usable: bool) -> dict:
    """avail_v1's three features with absent minutes from `rosters` (delivered games only, in PlayerState)."""
    if not usable:
        return {"new_absent_diff": 0.0, "all_absent_diff": 0.0, "report_missing": 1.0}
    day, h, a = et_day(g.tipoff_at), g.home_team_id.split(":", 1)[1], g.away_team_id.split(":", 1)[1]
    hh = rosters.absent(f.entries.get(h, []), h, day, g.season) if h in f.absent else {"new": 0.0, "all": 0.0}
    aa = rosters.absent(f.entries.get(a, []), a, day, g.season) if a in f.absent else {"new": 0.0, "all": 0.0}
    return {"new_absent_diff": (aa["new"] - hh["new"]) / 48, "all_absent_diff": (aa["all"] - hh["all"]) / 48,
            "report_missing": 0.0}


class PlayerState:
    """Ratings + rosters for one model instance. `features(g)` uses only state as of the call; `update(result)` consumes the
    delivered result and that game's minutes."""

    def __init__(self, box: Box, k: float, facts: Optional[dict] = None):
        self.box, self.facts = box, facts or {}
        self.ratings = PlayerRatings(k)
        self.rosters = Rosters()
        self.coverage: dict = defaultdict(int)

    def _team(self, team_id: str) -> str:
        return team_id.split(":", 1)[1]

    def diffs(self, g: PregameGame) -> dict:
        day = et_day(g.tipoff_at)
        h, a = self._team(g.home_team_id), self._team(g.away_team_id)
        rh, ra = self.rosters.expected(h, day, g.season), self.rosters.expected(a, day, g.season)
        full = self.ratings.strength(rh, g.season) - self.ratings.strength(ra, g.season)
        f = self.facts.get(day)
        usable = (f is not None and f.usable and h not in f.unsubmitted and a not in f.unsubmitted
                  and av.evening_cutoff(g.tipoff_at) is not None)
        if usable:
            players = getattr(f, "players", {})
            rep = (self.ratings.strength(without_absent(rh, players.get(h, {})), g.season)
                   - self.ratings.strength(without_absent(ra, players.get(a, {})), g.season))
        else:
            rep = full
        return {"player_diff_full": full, "player_diff_report": rep, "report_used": usable,
                "roster_home": len(rh), "roster_away": len(ra), **gated_avail(self.rosters, f, g, usable)}

    def update(self, r: Result) -> None:
        g = r.game
        teams = self.box.games.get(g.game_id)
        h, a = self._team(g.home_team_id), self._team(g.away_team_id)
        if not teams or h not in teams or a not in teams:
            self.coverage["result_without_box"] += 1
            return
        self.ratings.update(g.season, teams[h], teams[a], r.home_score - r.away_score, g.neutral_site)
        self.rosters.add(et_day(g.tipoff_at), g.season, {h: teams[h], a: teams[a]})
        self.coverage["rating_updates"] += 1


# --- Models ---------------------------------------------------------------------------------------------------------------
def models(elo_cfg, box: Box, facts: dict, k: float) -> dict:
    from .models.logistic import V2_FEATURES, V2_SCALED, LogisticV2

    class WithPlayers(LogisticV2):
        extra: tuple = ()
        scaled_extra: tuple = ()

        def __init__(self, elo_cfg):
            super().__init__(elo_cfg)
            self.players = PlayerState(box, k, facts)

        gated = False

        def _features(self, g):
            d = self.players.diffs(g)
            avail = {n: d[n] for n in av.AVAIL_FEATURES} if self.gated else av.game_features(facts, g)
            x = {**super()._features(g), **d, **avail}
            return {n: x[n] for n in self.feature_names}

        def update(self, r: Result) -> None:
            super().update(r)
            self.players.update(r)

        def config(self):
            return {**super().config(), "players": {"k": k, "prior": NEWCOMER_PRIOR, "carry": SEASON_CARRY,
                                                    "home_edge": HOME_EDGE, "roster_games": ROSTER_RECENT_GAMES}}

    class BR(WithPlayers):
        name = "players_full"
        feature_names = V2_FEATURES + ("player_diff_full",)
        scaled_names = V2_SCALED + ("player_diff_full",)

    class BRI(WithPlayers):
        name = "players_report"
        feature_names = V2_FEATURES + av.AVAIL_FEATURES + ("player_diff_report",)
        scaled_names = V2_SCALED + ("new_absent_diff", "all_absent_diff", "player_diff_report")

    class IGated(WithPlayers):
        """Research-only correction (Codex review): avail_v1's features with absent minutes from delivered games only."""
        name = "avail_gated"
        gated = True
        feature_names = V2_FEATURES + av.AVAIL_FEATURES
        scaled_names = V2_SCALED + ("new_absent_diff", "all_absent_diff")

    class BRIGated(BRI):
        name = "players_report_gated"
        gated = True

    return {"B": LogisticV2(elo_cfg), "B+I": av.avail_v1(elo_cfg, facts), "B+R": BR(elo_cfg), "B+R+I": BRI(elo_cfg),
            "B+I*": IGated(elo_cfg), "B+R+I*": BRIGated(elo_cfg)}


# --- K selection (warm-up only) ----------------------------------------------------------------------------------------
def select_k(rows: list, box: Box) -> dict:
    """Chooses K once by mean squared margin error of the pregame expected-roster margin on K_SELECTION_SEASON games."""
    out = {}
    for k in K_GRID:
        state = PlayerState(box, k)

        class Probe:
            name = f"k{k}"

            def predict(self, g):
                d = state.diffs(g)["player_diff_full"] + (0.0 if g.neutral_site else HOME_EDGE)
                self.last[g.game_id] = d
                return 0.5

            def update(self, r):
                state.update(r)

            def config(self):
                return {"k": k}
        probe = Probe()
        probe.last = {}
        warm = [r for r in rows if r.game.season <= K_SELECTION_SEASON]
        backtest.run(probe, warm)
        errs = [(probe.last[r.game.game_id] - (r.home_score - r.away_score)) ** 2 for r in warm
                if r.game.season == K_SELECTION_SEASON]
        out[k] = sum(errs) / len(errs)
    best = min(K_GRID, key=lambda k: (out[k], k))
    return {"k": best, "mse": out, "season": K_SELECTION_SEASON}


# --- Evaluation ---------------------------------------------------------------------------------------------------------
def _ll(p, y):
    p = min(1 - 1e-15, max(1e-15, p))
    return -math.log(p if y else 1 - p)


def _br(p, y):
    return (p - float(y)) ** 2


def metrics(games: list[dict], key: str) -> dict:
    n = len(games)
    probs, ys = [g[key] for g in games], [g["home_won"] for g in games]
    wins = sum((p >= 0.5) == y for p, y in zip(probs, ys))
    out = {"n": n, "accuracy": wins / n, "log_loss": sum(map(_ll, probs, ys)) / n, "brier": sum(map(_br, probs, ys)) / n}
    order = sorted(range(n), key=lambda i: probs[i])
    bins = []
    for b in range(10):
        idx = order[b * n // 10:(b + 1) * n // 10]
        if idx:
            bins.append({"mean_p": sum(probs[i] for i in idx) / len(idx), "home_rate": sum(ys[i] for i in idx) / len(idx),
                         "n": len(idx)})
    out["calibration"] = bins
    out["ece"] = sum(b["n"] * abs(b["mean_p"] - b["home_rate"]) for b in bins) / n
    out["thresholds"] = {}
    for t in THRESHOLDS:
        sel = [i for i in range(n) if max(probs[i], 1 - probs[i]) >= t]
        right = sum((probs[i] >= 0.5) == ys[i] for i in sel)
        out["thresholds"][str(t)] = {"games": len(sel), "coverage": len(sel) / n, "accuracy": right / len(sel) if sel else None}
    return out


def week_bootstrap(values: list[float], cutoffs: list[str], seed: int = backtest.BOOTSTRAP_SEED,
                   samples: int = backtest.BOOTSTRAP_SAMPLES) -> list[float]:
    """Sorted resampled means: the exact procedure of backtest.block_bootstrap (same seed, ISO-week blocks, draw order),
    returned whole so adjusted percentile intervals come from the samples themselves (tests check the 95% endpoints match)."""
    blocks: dict[tuple, list[float]] = {}
    for v, c in zip(values, cutoffs):
        iso = datetime.strptime(c, "%Y-%m-%dT%H:%M:%SZ").isocalendar()
        blocks.setdefault((iso[0], iso[1]), []).append(v)
    weeks = list(blocks.values())
    rng, stats = random.Random(seed), []
    for _ in range(samples):
        pick = [weeks[rng.randrange(len(weeks))] for _ in weeks]
        stats.append(sum(sum(w) for w in pick) / sum(len(w) for w in pick))
    return sorted(stats)


def percentile_interval(stats: list[float], alpha: float) -> list[float]:
    """Two-sided (1 - alpha) percentile interval, same index convention as backtest.block_bootstrap."""
    n = len(stats)
    return [stats[int(alpha / 2 * n)], stats[int((1 - alpha / 2) * n) - 1]]


def compare(games: list[dict], a: str, b: str, alpha: float = PRIMARY_ALPHA) -> dict:
    """Paired differences a − b with the week-block bootstrap: 95% interval and the Bonferroni-adjusted percentile interval
    (alpha = 0.05 / number of primary comparisons, i.e. the 1.25th and 98.75th percentiles for two)."""
    cutoffs = [g["cutoff_at"] for g in games]
    out = {}
    for metric, fn in (("log_loss", _ll), ("brier", _br)):
        diffs = [fn(g[a], g["home_won"]) - fn(g[b], g["home_won"]) for g in games]
        stats = week_bootstrap(diffs, cutoffs)
        out[metric] = {"mean": sum(diffs) / len(diffs), "ci95": percentile_interval(stats, 0.05),
                       "bonferroni_interval": percentile_interval(stats, alpha), "bonferroni_alpha": alpha,
                       "games": len(diffs), "seed": backtest.BOOTSTRAP_SEED, "samples": len(stats)}
    acc = [int((g[a] >= 0.5) == g["home_won"]) - int((g[b] >= 0.5) == g["home_won"]) for g in games]
    stats = week_bootstrap(acc, cutoffs)
    out["accuracy"] = {"mean": sum(acc) / len(acc), "ci95": percentile_interval(stats, 0.05), "games": len(acc)}
    return out


def walk(conn: sqlite3.Connection, period: str, seasons: tuple, scored: tuple, elo_cfg, k: Optional[float],
         log: Callable[[str], None]) -> dict:
    rows = backtest.load_rows(conn, period)
    if any(r.game.season not in seasons for r in rows):
        raise PlayersError(f"period {period!r} loaded seasons outside {seasons}")
    box = load_box(seasons)
    facts, coverage = build_facts(conn, box, seasons)
    if seasons == DEV_SEASONS:
        coverage["avail_v1_parity"] = avail_parity(conn, box, facts)
        if not coverage["avail_v1_parity"]["identical"]:
            raise PlayersError(f"report facts differ from avail_v1: {coverage['avail_v1_parity']}")
    k_choice = select_k(rows, box) if k is None else {"k": k, "given": True}
    rows = [dataclasses.replace(r, cutoff_at=av.evening_cutoff(r.game.tipoff_at) or r.cutoff_at) for r in rows]
    ids = {r.game.game_id for r in rows if r.game.season in scored and av.evening_cutoff(r.game.tipoff_at)}
    events = backtest.load_box_events(conn, period)
    built = models(elo_cfg, box, facts, k_choice["k"])
    by, extra = {}, {}
    for name, m in built.items():
        log(f"walk {period}: {name}")
        diag = (lambda mm, g: {"diffs": mm.players.diffs(g)}) if hasattr(m, "players") else None
        out = backtest.run(m, rows, events, extra=diag)
        by[name] = {p["game_id"]: p for p in out if p["game_id"] in ids}
    first = by["B"]
    order = sorted(first, key=lambda g: (first[g]["cutoff_at"], g))
    games = [{"game_id": g, "season": first[g]["season"], "cutoff_at": first[g]["cutoff_at"], "home_won": first[g]["home_won"],
              **{f"p_{n}": by[n][g]["p_home"] for n in built},
              "player_diff_full": by["B+R"][g]["diffs"]["player_diff_full"],
              "player_diff_report": by["B+R+I"][g]["diffs"]["player_diff_report"],
              "report_used": by["B+R+I"][g]["diffs"]["report_used"]} for g in order]
    report_ok = sum(1 for g in games if g["report_used"])
    audit = timing_audit(box, facts, [r.game for r in rows], [r.game for r in rows if r.game.game_id in ids], by["B+R+I*"])
    weights = {n: dict(zip(("intercept",) + built[n].feature_names, [round(w, 4) for w in built[n].weights]))
               for n in built if getattr(built[n], "weights", None)}
    player_cov = {n: dict(built[n].players.coverage) for n in built if hasattr(built[n], "players")}
    return {"games": games, "coverage": coverage, "k": k_choice, "box_digest": box.digest,
            "dataset_version": backtest.dataset_version(rows), "box_version": backtest.box_version(events),
            "injury_timing_audit": audit, "weights_last_fit": weights, "player_coverage": player_cov, "games_with_report_adjustment": report_ok,
            "versions": {n: backtest.model_version(built[n]) for n in built}}


def timing_audit(box: Box, facts: dict, all_games: list, scored: list, gated_preds: dict) -> dict:
    """Codex review: do avail_v1's absent minutes use a game whose result was not yet delivered at the cutoff?
    Three recomputations of the same features: (a) every box appearance dated before the game — must equal avail_v1
    exactly (proves the recomputation); (c) only games in the walk's game list, dated before (no delivery gate); (b) only
    games delivered by the cutoff (recorded during the walk). timing = (b) ≠ (c); game coverage = (c) ≠ (a), i.e. minutes
    from box games outside the game list (e.g. All-Star weekend) that the delivery-gated variants never see."""
    by_date, by_list = Rosters.from_apps(box.apps), Rosters.from_games(box, all_games)
    keys = ("new_absent_diff", "all_absent_diff", "report_missing")

    def feats(ros, g):
        ref = av.game_features(facts, g)
        return gated_avail(ros, facts.get(et_day(g.tipoff_at)), g, ref["report_missing"] == 0.0)

    def gap(x, y):
        return max(abs(x[k] - y[k]) for k in keys)

    mismatch, timing, coverage, worst = [], [], [], {"timing": 0.0, "coverage": 0.0}
    for g in scored:
        ref = av.game_features(facts, g)
        if gap(feats(by_date, g), ref) > 1e-9:
            mismatch.append(g.game_id)
        listed, delivered = feats(by_list, g), gated_preds[g.game_id]["diffs"]
        if gap(delivered, listed) > 1e-9:
            timing.append(g)
            worst["timing"] = max(worst["timing"], gap(delivered, listed))
        if gap(listed, ref) > 1e-9:
            coverage.append(g)
            worst["coverage"] = max(worst["coverage"], gap(listed, ref))
    if mismatch:
        raise PlayersError(f"absent-minutes recomputation differs from avail_v1 on {len(mismatch)} games: {mismatch[:5]}")
    seasons = sorted({g.season for g in scored})
    by_season = lambda gs: {s: sum(1 for g in gs if g.season == s) for s in seasons}
    affected = {g.game_id for g in timing} | {g.game_id for g in coverage}
    return {"scored_games": len(scored), "recomputation_identical": True, "affected_games": len(affected),
            "timing_games": len(timing), "timing_by_season": by_season(timing),
            "coverage_games": len(coverage), "coverage_by_season": by_season(coverage),
            "max_feature_change_per48": worst, "timing_examples": [g.game_id for g in timing[:20]],
            "coverage_examples": [g.game_id for g in coverage[:20]]}


def summarize(games: list[dict], seasons: tuple) -> dict:
    names = ("B", "B+I", "B+R", "B+R+I", "B+I*", "B+R+I*")
    out = {"games": len(games), "models": {n: metrics(games, f"p_{n}") for n in names},
           "primary": {"B+R minus B": compare(games, "p_B+R", "p_B"),
                       "B+R+I minus B+I": compare(games, "p_B+R+I", "p_B+I")},
           "secondary": {"B+R+I minus B": compare(games, "p_B+R+I", "p_B"),
                         "B+I minus B": compare(games, "p_B+I", "p_B")},
           # * = injury absent minutes from delivered games only (same availability rule as the rosters); Codex review
           "corrected": {"B+R+I* minus B+I*": compare(games, "p_B+R+I*", "p_B+I*"),
                         "B+I* minus B": compare(games, "p_B+I*", "p_B"),
                         "B+R+I* minus B": compare(games, "p_B+R+I*", "p_B"),
                         "B+I* minus B+I": compare(games, "p_B+I*", "p_B+I")},
           "by_season": {}}
    for s in seasons:
        sg = [g for g in games if g["season"] == s]
        if sg:
            out["by_season"][s] = {n: {k: v for k, v in metrics(sg, f"p_{n}").items() if k in ("n", "accuracy", "log_loss", "brier")}
                                   for n in names}
    out["reading"] = {c: ("adds information" if v["log_loss"]["bonferroni_interval"][1] < 0 else "no evidence of added information")
                      for c, v in {**out["primary"], "corrected primary 2: B+R+I* minus B+I*": out["corrected"]["B+R+I* minus B+I*"]}.items()}
    return out


def evaluate(conn: sqlite3.Connection, *, recent: bool = True, out_dir: Optional[Path] = None,
             log: Callable[[str], None] = print) -> dict:
    """Development walk (period 'validation', seasons 2019-20 … 2023-24) decides; the recent walk (period 'test', every season,
    access logged) is reported separately with the K chosen in the development walk."""
    _, elo_cfg = backtest.load_frozen_decision(conn)
    started = utc_now()
    dev = walk(conn, "validation", DEV_SEASONS, DEV_SCORED, elo_cfg, None, log)
    report = {"study": STUDY, "exploratory": True, "generated_at": started,
              "cutoff": "17:45 ET game day; report publication times assumed (header / Last-Modified)",
              "design": "docs/PLAYER_RATINGS_STUDY.md",
              "development": {**summarize(dev["games"], DEV_SCORED), **{k: v for k, v in dev.items() if k != "games"}}}
    if recent:
        backtest.log_test_access({"at": utc_now(), "study": STUDY, "purpose": "retrospective recent-season report (amendment)",
                                  "seasons": list(RECENT_SCORED)})
        rec = walk(conn, "test", ALL_SEASONS, RECENT_SCORED, elo_cfg, dev["k"]["k"], log)
        report["recent_retrospective"] = {"flag": "retrospective, previously reserved test seasons; not a final test",
                                          **summarize(rec["games"], RECENT_SCORED),
                                          **{k: v for k, v in rec.items() if k != "games"}}
    out_dir = Path(out_dir or config.data_dir() / "reports" / f"{STUDY}-{started.replace(':', '')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    games = {"development": dev["games"], **({"recent": rec["games"]} if recent else {})}
    (out_dir / "games.json").write_text(json.dumps(games, default=str))
    report["output_dir"] = str(out_dir)
    log(f"wrote {out_dir / 'report.json'}")
    return report
