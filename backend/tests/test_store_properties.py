"""Property test (Hypothesis) of the adaptive cycle through the store, docs/ADAPTIVE_SYSTEM.md: every example copies a
synthetic NHL store (11 seasons with shots, the sealed decision, the 2027 game GID two hours out), writes generated
recent results, corrections and shot imports through the real ingest paths, some before and some after the training run,
then runs `adaptive.train`, the research cycle and `adaptive.record` as the scheduled jobs do. Checked:

- each online member's vector stored in `adaptive_forecasts` equals a fresh walk over the rows and shots the store holds
  at the forecast time (the SQL loaders), at the recorded line;
- a member is missing for a changed input only when something dated at or before the fit changed after it (a historical
  shot backfill alone makes the shot member missing), and then `inputs_changed_since_fit` names it and the run is due (a
  retrain is scheduled: no outage without a retrain);
- recording again creates nothing (idempotent).

Same member restrictions as test_adaptive's store (boosted members and most registry candidates left out for speed; they
are covered without the store by test_serving_properties). More examples: NBA_PROPERTY_EXAMPLES=200."""
import os
import shutil
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from app import adaptive, backtest, db, live, nhl_shots, research_live
from app.candidates import registry
from app.models import score_candidates
from test_adaptive import CYCLE_AT, SPECS, TRAIN_AT, _test_clock
from test_live_markets import add_upcoming, quote
from test_research_live import GID, run

EXAMPLES = max(3, int(os.environ.get("NBA_PROPERTY_EXAMPLES", "25")) // 5)
ONLINE = ("hockey_elo", "goals_poisson", "goals_poisson@market")
GUARDS = ("changed since the fit", "arrived after the fit")
FMT = "%Y-%m-%dT%H:%M:%SZ"
TEAMS = (6, 16, 8)


def _t(s):
    return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)


def _ts(d):
    return d.strftime(FMT)


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    from test_serving_properties import _nhl_store
    with pytest.MonkeyPatch.context() as mp:
        path = tmp_path_factory.mktemp("nhl-template")
        mp.setenv("NBA_DATA_DIR", str(path))
        c = _nhl_store(path, mp)
        add_upcoming(c, start="2026-10-12T19:00:00", at="2026-10-12T20:30:00Z")
        c.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES "
                  "('refresh', '2026-10-12T21:00:00Z', '2026-10-12T21:00:00Z', 'success')")
        quote(c, "2026-10-12T21:05:00Z", date="2026-10-12T23:00Z")
        mp.setattr(score_candidates, "MIN_HOCKEY_EXAMPLES", 100)
        mp.setattr(research_live, "STACK_MIN_TRAIN", 30)
        mp.setattr(registry, "MIN_TRAIN", {"nba": 200, "nhl": 100})
        mp.setattr(registry, "SPECS", tuple(registry.BY_NAME[s] for s in SPECS))
        mp.setattr(adaptive, "MIN_TRAIN", 30)
        mp.setattr(adaptive, "LEARNED", {**adaptive.LEARNED, "nhl": ("hockey_logistic", "hockey_logistic_xg", "goals_draw")})
        yield c
        c.close()


@st.composite
def recent(draw):
    """Writes (write time, kind, args) around the training run, and which of them change what the fit consumed."""
    writes, causes = [], set()
    train, cycle = _t(TRAIN_AT), _t(CYCLE_AT)
    n = draw(st.integers(1, 5))
    late_i, corrected_i = draw(st.integers(-n, n - 1)), draw(st.integers(-n, n - 1))   # each in about half the examples
    for i in range(n):
        gid = 2026020100 + i
        home, away = draw(st.permutations(TEAMS))[:2]
        start = datetime(2026, 10, 10, 16, tzinfo=timezone.utc) + timedelta(hours=draw(st.integers(0, 51)))   # 12:00 ET on
        et = (start - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S")
        hs, vs = draw(st.integers(0, 6)), draw(st.integers(0, 6))
        hs += hs == vs
        game = {"gid": gid, "start": et, "home": home, "away": away}
        writes.append((start, "live", {**game, "state": 3, "hs": 0, "vs": 0, "period": 1, "at": _ts(start)}))
        fin = start + timedelta(minutes=draw(st.integers(150, 1500)))
        if fin > cycle:
            continue                                                              # not final by the forecast
        late = fin <= train and i == late_i                                         # written after the fit, stamped before it
        writes.append((train + timedelta(minutes=1) if late else fin, "final", {**game, "hs": hs, "vs": vs, "at": _ts(fin)}))
        causes |= {"results"} if late else set()
        if fin <= train and not late and i == corrected_i:                         # corrected after the fit
            corr = train + timedelta(minutes=draw(st.integers(1, 49)))
            writes.append((corr, "final", {**game, "hs": vs, "vs": hs + (hs + 1 == vs), "at": _ts(corr)}))
            causes.add("results")
        if draw(st.booleans()):                                                  # its shots
            shot_at = fin + timedelta(minutes=draw(st.integers(0, 3000)))
            backfill_late = shot_at > train and fin <= train and draw(st.booleans())
            if shot_at <= cycle or backfill_late:
                when = train + timedelta(minutes=2) if backfill_late else shot_at
                writes.append((when, "shots", {"gid": gid, "at": _ts(when)}))
                # imported over 24 h after the start, a historical backfill is dated at the result time (nhl_shots)
                historical = when - start > nhl_shots.OBSERVED_WINDOW
                causes |= {"events"} if (historical and when > train and fin <= train) else set()
    if draw(st.integers(0, 3)) == 0:      # the last 2026 game in progress at training, final after it (season boundary)
        game = {"gid": 2026021999, "start": "2026-10-12T14:00:00", "home": 6, "away": 16, "season": 2026}
        writes.append((_t("2026-10-12T19:30:00Z"), "live", {**game, "state": 3, "hs": 0, "vs": 0, "period": 1, "at": "2026-10-12T19:30:00Z"}))
        fin = train + timedelta(minutes=draw(st.integers(1, 49)))
        writes.append((fin, "final", {**game, "hs": 3, "vs": 1, "at": _ts(fin)}))
        causes.add("season")
    return sorted(writes, key=lambda w: (w[0], w[1] != "live")), causes


def _apply(conn, kind, a):
    from test_nhl_xg import fake_fetch
    if kind == "shots":
        g = conn.execute("SELECT game_id, tipoff_at, result_available_at FROM games WHERE game_id=?", (f"nhl:{a['gid']}",)).fetchone()
        if g is not None and g["result_available_at"] is not None:
            nhl_shots.import_game(conn, g, fake_fetch(conn)(g["game_id"]), a["at"])
        return
    state = a.get("state", 7)
    add_upcoming(conn, gid=a["gid"], start=a["start"], state=state, hs=a["hs"], vs=a["vs"], period=a.get("period", 3),
                 at=a["at"], home=a["home"], away=a["away"], season=a.get("season", 2027))


def _fresh(conn, name, market, line, cache):
    """The member walked from scratch over what the store holds at CYCLE_AT, forecasting GID as the cycle does."""
    eng = live.engine(conn)
    if name not in cache:
        m = next(x for x in adaptive.members(conn)[0] if x.name == name).obj
        rows = adaptive.available_rows(conn, CYCLE_AT)
        known = {r.game.game_id: r for r in rows}
        events = adaptive._events(conn, eng, rows, known, CYCLE_AT)
        backtest.run(m, rows, adaptive._events_for(m, eng, events), deliver_until=CYCLE_AT)
        cache[name] = m
    m = cache[name]
    game = replace(eng.pregame(conn.execute("SELECT * FROM games WHERE game_id=?", (GID,)).fetchone()), cutoff_at=CYCLE_AT)
    p = m.predict(game)
    if market == "winner":
        return adaptive._vec((p, 0.0, 1 - p))
    return adaptive._vec(m.distribution(game).market(market, line))


def _backfill_only():
    """A final consumed by the fit whose shots are a historical backfill imported after it (dated at the result time)."""
    game = {"gid": 2026020100, "start": "2026-10-10T16:00:00", "home": 6, "away": 16}
    start, fin, when = _t("2026-10-10T20:00:00Z"), _t("2026-10-10T23:00:00Z"), _t(TRAIN_AT) + timedelta(minutes=2)
    return ([(start, "live", {**game, "state": 3, "hs": 0, "vs": 0, "period": 1, "at": _ts(start)}),
             (fin, "final", {**game, "hs": 3, "vs": 1, "at": _ts(fin)}),
             (when, "shots", {"gid": game["gid"], "at": _ts(when)})], {"events"})


@settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
@given(h=recent())
@example(h=_backfill_only())         # the generator draws a shot backfill alone rarely (with a results cause it is masked)
def test_the_stored_cycle_serves_fresh_walk_forecasts_and_every_guarded_outage_schedules_a_retrain(template, h):
    import json
    writes, causes = h
    with tempfile.TemporaryDirectory() as d, pytest.MonkeyPatch.context() as mp:
        mp.setenv("NBA_DATA_DIR", d)
        mp.setattr(adaptive, "utc_now", _test_clock())
        conn = db.connect(Path(d) / "nhl.sqlite")
        template.backup(conn)
        raw = Path(template.execute("PRAGMA database_list").fetchone()["file"]).parent / "raw"
        if raw.exists():                                  # the raw payloads the store's rows point to (shot features)
            shutil.copytree(raw, Path(d) / "raw")
        for when, kind, a in writes:
            if _ts(when) <= TRAIN_AT:
                _apply(conn, kind, a)
        t = adaptive.train(conn, now=TRAIN_AT)
        assert t["status"] in ("success", "partial"), t.get("errors")
        for when, kind, a in writes:
            if TRAIN_AT < _ts(when) <= CYCLE_AT:
                _apply(conn, kind, a)
        run(conn, CYCLE_AT)
        first = adaptive.record(conn, game_ids=[GID], now=CYCLE_AT)
        assert first["status"] != "failed", first.get("error")
        again = adaptive.record(conn, game_ids=[GID], now=CYCLE_AT)
        assert again["created"] == again["unavailable"] == 0                                # idempotent
        assert again["already_recorded"] == first["created"] + first["unavailable"]

        stored = conn.execute("SELECT market, line, members, missing FROM adaptive_forecasts WHERE game_id=?", (GID,)).fetchall()
        assert stored, first
        guarded, cache, reasons = set(), {}, {}
        for row in stored:
            members = {m["name"]: m["p"] for m in json.loads(row["members"])}
            for name, reason in json.loads(row["missing"]).items():
                reasons[name] = reason
                assert "failed" not in reason, (name, reason)                  # no member fails on the copied store
                if any(g in reason for g in GUARDS):
                    guarded.add(name)
                    kind = "season" if "arrived after the fit" in reason else ("results" if "results changed" in reason else "events")
                    assert kind in causes, (name, reason, causes)
            for name in ONLINE:
                if name in members:
                    assert members[name] == pytest.approx(list(_fresh(conn, name, row["market"], row["line"], cache)), abs=1e-8), name
        if not causes:
            assert not guarded, guarded                                       # no outage without a cause
        if causes == {"events"}:                                              # a cause: the shot member is out
            assert "box/shot inputs changed" in reasons.get("hockey_logistic_xg@adaptive", ""), reasons
        if guarded:                                                           # ... and none without a retrain scheduled
            changed = adaptive.inputs_changed_since_fit(conn, "nhl", adaptive.definition(conn), CYCLE_AT)
            assert guarded <= {c.split(" (")[0] for c in changed}, (guarded, changed)
            assert adaptive.due(conn, CYCLE_AT)["due"] is True
        conn.close()
