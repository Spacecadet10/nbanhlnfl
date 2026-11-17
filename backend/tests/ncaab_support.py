"""Synthetic NCAAB store and a stand-in for the model agent's `app/models/college.py` (no network, no live data).

The scratch store is created by the shared migrations and bound to 'ncaab' (store_identity), then filled with rows shaped
like the data agent's import: teams `ncaab:<id>`, games `ncaab:<event id>`, quotes in `market_quotes`.
"""
import sys
import types

from app import db

NOW = "2026-11-17T22:30:00Z"            # one hour before GAME tips off
REFRESHED = "2026-11-17T22:20:00Z"
GAME = "ncaab:401"                      # team 1 v 2, 2026-11-17T23:30Z, a scheduled D-I game
GAME_START = "2026-11-17T23:30:00Z"
RAW = "2026-11-16T12:00:00Z"
TEAMS = {"1": ("DUKE", "Duke Blue Devils"), "2": ("UNC", "North Carolina Tar Heels"), "3": ("KU", "Kansas Jayhawks"),
         "4": ("UK", "Kentucky Wildcats"), "5": ("TINY", "Tiny State Hawks")}


def make_store(tmp_path, monkeypatch, name="ncaab.sqlite"):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    # Offline: a job step that falls back to the real ESPN fetcher gets an empty scoreboard, never the network.
    from app.sources import espn_ncaab
    monkeypatch.setattr(espn_ncaab, "fetch_day", lambda yyyymmdd, timeout=60: b'{"events": []}')
    conn = db.connect(tmp_path / name)
    db.migrate(conn)
    db.claim_store(conn, "ncaab")
    for tid, (abbr, full) in TEAMS.items():
        conn.execute("INSERT INTO teams (team_id, abbr, name, first_seen_at) VALUES (?, ?, ?, ?)", (f"ncaab:{tid}", abbr, full, RAW))
    conn.execute("INSERT INTO raw_responses (raw_id, source, endpoint, request_key, content_hash, byte_size, path, importer_version, "
                 "first_retrieved_at) VALUES (1, 'espn_ncaab', 'scoreboard', 'dates=20261116', 'h', 1, 'x', 'v', ?)", (RAW,))
    conn.execute("INSERT INTO raw_fetches (fetch_id, raw_id, retrieved_at) VALUES (1, 1, ?)", (RAW,))
    conn.commit()
    return conn


def add_game(conn, event, tipoff, home, away, status="scheduled", hs=None, aws=None, season_type="regular", neutral=0,
             season=2027, available=None):
    final = status == "final"
    conn.execute(
        "INSERT INTO games (game_id, season, season_type, tipoff_at, home_team_id, away_team_id, neutral_site, status, home_score, "
        "away_score, first_seen_at, result_first_seen_at, result_available_at, result_availability_basis, last_raw_id, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (f"ncaab:{event}", season, season_type, tipoff, f"ncaab:{home}", f"ncaab:{away}", neutral, status, hs, aws, RAW,
         (available or tipoff) if final else None, (available or tipoff) if final else None,
         "observed_retrieval" if final else None, RAW))
    conn.commit()


def add_quote(conn, game, market, line, price_a, price_b, at, provider="DraftKings", label="current"):
    pa = _implied(price_a)
    pb = _implied(price_b)
    fetch = conn.execute("INSERT INTO raw_fetches (raw_id, retrieved_at) VALUES (1, ?)", (at,)).lastrowid
    conn.execute(
        "INSERT INTO market_quotes (sport, game_id, provider, market, price_label, line, price_a, price_b, p_a_devig, p_b_devig, "
        "quote_time, retrieved_at, fetch_id, raw_id) VALUES ('ncaab', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 1)",
        (game, provider, market, label, line, price_a, price_b, pa / (pa + pb), pb / (pa + pb), at, fetch))
    conn.commit()


def _implied(price):
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def refreshed(conn, at=REFRESHED):
    conn.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES ('refresh', ?, ?, 'success')", (at, at))
    conn.commit()


def seed(conn):
    """Two earlier finals, a D-I game tonight (GAME), a non-D-I game tonight ('other'), and a game tomorrow."""
    add_game(conn, "300", "2026-11-15T23:00:00Z", "1", "3", "final", 80, 70)
    add_game(conn, "301", "2026-11-16T23:00:00Z", "2", "4", "final", 66, 71)
    add_game(conn, "401", GAME_START, "1", "2")
    add_game(conn, "402", "2026-11-17T23:00:00Z", "3", "5", season_type="other")
    add_game(conn, "403", "2026-11-18T23:00:00Z", "4", "3")
    refreshed(conn)


class FakeCollege(types.ModuleType):
    """`college.predict(conn, game_id, *, cutoff[, lines])`: home probability from the stored results available at `cutoff`
    (a result known only later never moves it), markets at their own lines, other lines priced by a simple shift."""

    MODEL_VERSION = "ncaab-fake-college-v1"

    def __init__(self, with_lines=True):
        super().__init__("app.models.college")
        self.calls = []
        if not with_lines:
            self.predict = self._predict_without_lines

    def predict(self, conn, game_id, *, cutoff, lines=None):
        self.calls.append((game_id, cutoff, dict(lines or {})))
        wins = conn.execute("SELECT COUNT(*) FROM games WHERE status='final' AND result_available_at <= ? AND "
                            "((home_team_id=(SELECT home_team_id FROM games WHERE game_id=?) AND home_score>away_score) OR "
                            "(away_team_id=(SELECT home_team_id FROM games WHERE game_id=?) AND away_score>home_score))",
                            (cutoff, game_id, game_id)).fetchone()[0]
        p = round(0.55 + 0.03 * wins, 6)
        out = {"model_version": self.MODEL_VERSION, "home_win_prob": p, "markets": {}}
        for market, line in (("spread", -3.5), ("total", 141.5), ("team_total_home", 72.5), ("team_total_away", 69.5)):
            line = (lines or {}).get(market, line)
            shift = (line - {"spread": -3.5, "total": 141.5, "team_total_home": 72.5, "team_total_away": 69.5}[market]) * 0.02
            pa = min(0.95, max(0.05, round(0.52 - shift, 6)))
            out["markets"][market] = {"line": line, "probs": (pa, 0.0, round(1 - pa, 6))}
        return out

    def _predict_without_lines(self, conn, game_id, *, cutoff):
        return FakeCollege.predict(self, conn, game_id, cutoff=cutoff)


def install_college(monkeypatch, with_lines=True) -> FakeCollege:
    import app.models
    fake = FakeCollege(with_lines)
    monkeypatch.setitem(sys.modules, "app.models.college", fake)
    monkeypatch.setattr(app.models, "college", fake, raising=False)
    # the frozen decision file belongs to the model agent; a stand-in keeps `market_model` usable in tests
    from app import ncaab_app
    monkeypatch.setattr(ncaab_app, "decision", lambda: {"model_version": FakeCollege.MODEL_VERSION})
    return fake
