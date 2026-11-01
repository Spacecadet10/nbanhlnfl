"""Property tests (Hypothesis) of the adaptive walk members, docs/ADAPTIVE_SYSTEM.md, for every walk member
`adaptive.members` builds on the synthetic NBA and NHL stores (the stores of test_adaptive / test_live_markets):

1. Serving equals a fresh walk (members updated online): a member frozen at its training cutoff, thawed and brought up to
   date at serving (`adaptive._advance`) forecasts exactly as a fresh walk over what the store holds at serving, whenever
   the serving guards let it forecast (`adaptive._frozen_state_unusable`, then the season guard of `_record`).
2. The artifact loses nothing (every member, learned ones included): the thawed member, advanced, forecasts exactly as the
   in-memory member it was frozen from, advanced the same way.
3. No forecast depends on anything available after its cutoff (every member): changing every result, box score and shot
   list that becomes available after a time T leaves every forecast made at or before T unchanged.
And for 1-2: the guards never abstain when nothing dated at or before the cutoff changed after the fit.

Each example appends a generated tail (the last base season or the next one) to the store's history: results delivered
on time, ingested late (stamped before the cutoff, written after the fit), corrected after the fit, or of the previous season
after a fit that started the next one (the next game in a new season); box scores (NBA) and
shot lists (NHL) delivered on time or backfilled after the fit with an earlier date. Learned members are compared only
through 2-3: their weekly refits make a fresh walk differ from the frozen fit in force by design.
The player members (NBA players_injuries_v1 / players_injuries_points, NHL nhl_players_injuries) need research caches the
synthetic stores lack: they are built as adaptive.members builds them, with in-memory inputs generated per example (player
boxes; NHL injury snapshots through the production `Inputs.snapshot`), attached where serving re-reads its cache after a
thaw, and get `_record`'s `input_mismatch` guard. Their boxes are altered by result time in 3: the player-box cache has no
publication time (a known limitation, HANDOFF.md). NBA injury reports are not generated (the member forecasts without one).
Not covered here: registry candidates (their own fit path). The combiner: the last two tests. More examples:
NBA_PROPERTY_EXAMPLES=200."""
import copy
import json
import os
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app import adaptive, agreement, backtest, db, ingest, live, nhl_eval, nhl_shots, sports
from app import nhl_player_ratings as npr
from app import player_ratings as pr
from app.models.base import PregameGame
from app.models.efficiency import BoxEvent, BoxLine
from app.sources import nhl_pbp

EXAMPLES = int(os.environ.get("NBA_PROPERTY_EXAMPLES", "25"))
ONLINE = {("nba", "elo"), ("nba", "efficiency_margin"), ("nba", "points_normal"),
          ("nhl", "hockey_elo"), ("nhl", "goals_poisson"), ("nhl", "goals_poisson@market")}
LEARNED = {("nba", "logistic@adaptive"), ("nba", "logistic_v2@adaptive"), ("nba", "gbt_v1@adaptive"),
           ("nba", "ridge_points@adaptive"), ("nba", "gbt_points@adaptive"),
           ("nhl", "hockey_logistic@adaptive"), ("nhl", "hockey_gbt@adaptive"), ("nhl", "hockey_logistic_xg@adaptive"),
           ("nhl", "goals_draw@adaptive"), ("nhl", "goals_glm@adaptive")}
FORECAST_STATE = {("nba", "efficiency_margin")}   # online members whose forecast is part of their state (pinned below)
PLAYERS = {("nba", f"{pr.ADAPTIVE_MEMBER}@adaptive"), ("nba", f"{pr.SCORE_MEMBER}@adaptive"), ("nhl", f"{npr.MEMBER}@adaptive")}
ALL = sorted(ONLINE | LEARNED | PLAYERS)
SETTINGS = settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
FMT = "%Y-%m-%dT%H:%M:%SZ"


def _ts(d: datetime) -> str:
    return d.strftime(FMT)


def _dt(s: str) -> datetime:
    return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)


class World(SimpleNamespace):
    def __repr__(self):                      # Hypothesis prints the arguments of a failing example
        return f"<synthetic {self.sport} store>"


# --- The two stores, built once ---------------------------------------------------------------------------------------
def _nhl_store(path, mp):
    from test_nhl_eval import TEAMS, synthetic_season
    from test_nhl_xg import fake_fetch
    c = db.connect(path / "nhl.sqlite")
    db.migrate(c)
    ingest.ingest_nhl_teams(c, fetch=lambda: TEAMS, retrieved_at="2015-09-01T00:00:00Z")
    rng = random.Random(7)
    for season in range(2016, 2027):
        body = synthetic_season(season, rng, {6: 0.25, 16: -0.2, 8: 0.0})
        ingest.ingest_nhl_season(c, season, fetch=lambda _s, b=body: b, retrieved_at=f"{season}-07-01T00:00:00Z")
    nhl_eval.evaluate_validation(c, decision_path=path / "nhl_decision.json", out_dir=path / "val", log=lambda _m: None)
    mp.setattr(nhl_eval, "DECISION_PATH", path / "nhl_decision.json")
    nhl_shots.import_pending(c, None, pause=0, fetch=fake_fetch(c), log=lambda *_: None)
    return c


def _nba_store(path, mp):
    from test_backtest import synthetic_rows
    from test_live import event, ingest_events
    c = db.connect(path / "nba.sqlite")
    db.migrate(c)
    ingest_events(c, [event(r.game.game_id, r.game.tipoff_at, r.game.home_team_id.split(":")[1], r.game.away_team_id.split(":")[1],
                            "STATUS_FINAL", r.home_score, r.away_score, r.game.season) for r in synthetic_rows(per_season=40)],
                  "2026-09-30T00:00:00Z", month="202601")
    val = backtest.evaluate(c, "validation", out_dir=path / "val", log=lambda _: None)
    (path / "decision.json").write_text(json.dumps(backtest.decision_from_validation_report(val, "synthetic")))
    mp.setattr(backtest, "FROZEN_DECISION_PATH", path / "decision.json")
    db.claim_store(c, "nba")
    return c


@pytest.fixture(scope="module")
def worlds(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(adaptive, "MIN_TRAIN", 30)
        out = {}
        for sport, build in (("nhl", _nhl_store), ("nba", _nba_store)):
            path = tmp_path_factory.mktemp(sport)
            mp.setenv("NBA_DATA_DIR", str(path))
            conn = build(path, mp)
            eng = live.engine(conn)
            rows = adaptive.available_rows(conn, "2100-01-01T00:00:00Z")
            known = {r.game.game_id: r for r in rows}
            mems = {m.name: m for m in adaptive.members(conn)[0] if m.kind == "walk"}
            assert {n for s, n in ONLINE | LEARNED if s == sport} == set(mems), "every walk member must be listed"
            built = agreement.models(conn)[0].cfg
            if sport == "nba":
                names = pr.team_names(conn)
                for inner in (pr.adaptive_member(built, names), pr.PlayersInjuriesPoints(built, names)):
                    mems[f"{inner.name}@adaptive"] = adaptive.Member(f"{inner.name}@adaptive", "walk", adaptive.Adaptive(inner),
                                                                     markets_=("winner",), learned=True)
                team_names = {}
            else:
                inner = npr.NhlPlayersInjuries(built, sports.get(sport).rating_key)
                mems[f"{inner.name}@adaptive"] = adaptive.Member(f"{inner.name}@adaptive", "walk", adaptive.Adaptive(inner),
                                                                 markets_=("winner",), learned=True)
                team_names = {r["team_id"]: r["name"] for r in conn.execute("SELECT team_id, name FROM teams")}
            out[sport] = World(sport=sport, conn=conn, eng=eng, rows=rows, mems=mems, walked={}, team_names=team_names,
                                         events=adaptive._events(conn, eng, rows, known, "2100-01-01T00:00:00Z"),
                                         end=max(r.result_available_at for r in rows), season=max(r.game.season for r in rows),
                                         teams=sorted({r.game.home_team_id for r in rows}),
                                         prefix=rows[0].game.game_id.split(":")[0])
        yield out
        for w in out.values():
            w.conn.close()


def _walked(w, name, inputs=None):
    """A copy of the member walked over the store's whole history (every result and box/shot list delivered), with
    `inputs` (player members: the cache as of the moment it is used; default the history's boxes only)."""
    if name not in w.walked:
        m = copy.deepcopy(w.mems[name].obj)
        _attach(m, _player_inputs(w, None, {}))
        backtest.run(m, w.rows, adaptive._events_for(m, w.eng, w.events), deliver_until=w.end)
        _attach(m, None)                                       # cached without its inputs (cheap to copy)
        w.walked[name] = m
    m = copy.deepcopy(w.walked[name])
    _attach(m, inputs if inputs is not None else _player_inputs(w, None, {}))
    return m


# --- Player members' inputs (in memory) ---------------------------------------------------------------------------------
def _attach(m, inputs):
    """What serving does after a thaw when a player member re-reads its cache: the inputs as of now, derived state reset."""
    inner = getattr(m, "member", m)
    if isinstance(getattr(inner, "players", None), pr.LiveState):
        inner.players._inputs, inner.players._state = inputs, None
    elif isinstance(inner, npr.NhlPlayersInjuries):
        inner._inputs = inputs


def _roster(w, team, g=None, altered=False):
    """Team `team`'s players in game `g` (None: a history game): eight NBA players / twelve NHL skaters and a goalie."""
    n = 8 if w.sport == "nba" else 13
    ids = [f"{team.split(':')[1]}p{j}" for j in range(n)]
    if g is not None and g["debut"] and team == g["home"]:
        ids[-1] = f"{team.split(':')[1]}n{g['i']}"                        # a debut replaces the last player
    if altered:
        ids[1], ids[2] = ids[2], f"{team.split(':')[1]}x{g['i']}"          # a different lineup
    return ids


def _player_inputs(w, t, include, altered=(), later=False):
    """The cache with the history's boxes plus the tail games in `include` ({game id: tail game}); boxes of games in
    `altered` and (with `later`) NHL snapshots captured after `t.T` differ."""
    if w.sport == "nba":
        from test_player_ratings import _Inputs
        rows = []
        games = [(r.game, None) for r in w.rows] + [(_row(w, g, False, g["avail"]).game, g) for g in include.values()]
        for game, g in games:
            local = datetime.fromisoformat(f"{pr.et_day(game.tipoff_at)}T19:30:00").replace(tzinfo=pr.av.ET).isoformat()
            for team in (game.home_team_id, game.away_team_id):
                for j, pid in enumerate(_roster(w, team, g, g is not None and g["id"] in altered)):
                    rows.append({"season": game.season, "game_id": game.game_id.split(":", 1)[1], "game_date_time": local,
                                 "athlete_id": pid, "athlete_display_name": f"P {pid}", "team_id": team.split(":")[1],
                                 "minutes": str((36, 34, 32, 30, 28, 26, 24, 30)[j]), "did_not_play": "false"})
        seasons = tuple(sorted({r["season"] for r in rows}))
        inp = _Inputs(pr.load_box(seasons, loader=lambda s: [r for r in rows if r["season"] == s]))
        inp.status = lambda: {"box_digest": inp.box.digest, "box_seasons": list(inp.box.seasons)}   # as AdaptiveInputs
        return inp
    import hashlib
    boxes = {}
    games = [(r.game, r.home_score - r.away_score, None) for r in w.rows] + \
            [(_row(w, g, False, g["avail"]).game, g["hs"] - g["as"], g) for g in include.values()]
    for game, margin, g in games:
        alt = g is not None and g["id"] in altered
        teams = {}
        for team, sign in ((game.home_team_id, 1), (game.away_team_id, -1)):
            teams[team] = [{"id": pid, "name": f"Pat {pid}", "minutes": 60.0 if j == 0 else (14.0 + j % 4) * (0.5 if alt else 1),
                            "plus_minus": 0 if j == 0 else sign * (margin > 0) - sign * (margin < 0), "goalie": j == 0}
                           for j, pid in enumerate(_roster(w, team, g, alt))]
        boxes[game.game_id] = {"game_id": game.game_id, "teams": teams, "home_team_id": game.home_team_id,
                               "away_team_id": game.away_team_id,
                               "names": {tid: w.team_names.get(tid, tid) for tid in teams}}
    snaps = []
    for k, (at, team, j, weight) in enumerate(t.injuries if t is not None else ()):
        if later and at > t.T:
            j, weight = (j + 1) % 12 + 1, 1.0 - weight                    # a later report says something else
        snaps.append({"captured_at": _ts(at), "teams": sorted(set(w.team_names.values())),
                      "rows": [{"team": w.team_names.get(team, team), "player": f"Pat {team.split(':')[1]}p{j}", "weight": weight}]})
    inp = object.__new__(npr.Inputs)
    inp.boxes, inp.snapshots, inp.skipped_live = boxes, sorted(snaps, key=lambda x: x["captured_at"]), 0
    inp.digest = hashlib.sha256(json.dumps(boxes, sort_keys=True).encode()).hexdigest()   # over every box, as Inputs
    return inp


# --- Generated tails --------------------------------------------------------------------------------------------------
@st.composite
def tails(draw, w):
    start = _dt(w.end) + timedelta(days=1)
    season = w.season + draw(st.integers(0, 1))
    n = draw(st.integers(3, 16))
    days = sorted(draw(st.lists(st.integers(0, 24), min_size=n, max_size=n)))
    late_i, corrected_i = draw(st.integers(-n, n - 1)), draw(st.integers(-n, n - 1))   # each in about half the tails
    games = []
    for i, day in enumerate(days):
        home, away = draw(st.permutations(w.teams))[:2]
        if w.sport == "nhl":
            decision = draw(st.sampled_from(("REG", "REG", "REG", "OT", "SO")))
            lo = draw(st.integers(0, 5))
            win = lo + (1 if decision != "REG" else draw(st.integers(1, 4)))
            hs, as_ = (win, lo) if draw(st.booleans()) else (lo, win)
        else:
            decision, hs, as_ = None, draw(st.integers(80, 130)), draw(st.integers(80, 130))
            as_ += as_ == hs
        tip = start + timedelta(days=day, hours=draw(st.integers(17, 26)))
        games.append({
            "i": i, "id": f"{w.prefix}:9{season}{i:04d}", "season": season, "home": home, "away": away, "hs": hs, "as": as_,
            "decision": decision, "tip": tip, "avail": tip + timedelta(minutes=draw(st.integers(150, 1800))),
            "late": i == late_i, "corrected": i == corrected_i,
            "box": draw(st.booleans()), "box_delay": timedelta(minutes=draw(st.integers(0, 2880))),
            "box_late": draw(st.booleans()), "seed": draw(st.integers(0, 9999)), "debut": draw(st.integers(0, 4)) == 0,
            "line": [draw(st.integers(70, 100)), draw(st.integers(10, 30)), draw(st.integers(5, 15)), draw(st.integers(8, 20))]})
    end = max(g["avail"] for g in games)
    cut = start + timedelta(minutes=draw(st.integers(0, int((end - start).total_seconds() // 60))))
    now = cut + timedelta(minutes=draw(st.integers(60, 14400)))
    fix = cut + timedelta(minutes=draw(st.integers(1, int((now - cut).total_seconds() // 60))))   # when corrections land
    home, away = draw(st.permutations(w.teams))[:2]
    nxt = PregameGame(f"{w.prefix}:8{season}0000", season + draw(st.integers(0, 1)), "regular", _ts(now + timedelta(hours=3)), home, away, False)
    near = draw(st.sampled_from(games))["tip"] - timedelta(hours=2) + timedelta(minutes=draw(st.integers(0, 240)))
    T = near if draw(st.booleans()) else start + (end - start) * draw(st.floats(0, 1))   # just after a forecast, or anywhere
    injuries = [(start + (now - start) * draw(st.floats(0, 1)), draw(st.sampled_from(w.teams)), draw(st.integers(1, 12)),
                 draw(st.sampled_from((0.5, 1.0)))) for _ in range(draw(st.integers(0, 3)))] if w.sport == "nhl" else []
    return SimpleNamespace(games=games, cut=cut, now=now, fix=fix, nxt=nxt, T=T, injuries=injuries)


def _row(w, g, swapped, avail):
    hs, as_ = (g["as"], g["hs"]) if swapped else (g["hs"], g["as"])
    cutoff = _ts(g["tip"] - timedelta(hours=2))           # adaptive.forecast_cutoff
    game = PregameGame(g["id"], g["season"], "regular", _ts(g["tip"]), g["home"], g["away"], False, cutoff_at=cutoff)
    if w.sport == "nhl":
        return nhl_eval.HockeyRow(game, cutoff, hs, as_, _ts(avail), g["decision"])
    return backtest.Row(game, cutoff, hs, as_, _ts(avail))


def _event(w, g, row, altered=False):
    at = _ts(g["avail"] + g["box_delay"])
    fetch = 10_000_000 + g["i"]
    if w.sport == "nhl":
        from test_nhl_xg import payload
        h, a = (int(t.split(":")[1]) for t in (g["home"], g["away"]))
        shots = tuple(nhl_pbp.parse(json.loads(payload(9000 + g["i"], h, a, g["seed"])))[1])
        if altered:
            shots = tuple(replace(s, goal=not s.goal) for s in shots)
        return nhl_shots.ShotEvent(row.game, at, at, fetch, shots)
    fga, fta, oreb, tov = g["line"]
    bump = 9 if altered else 0
    return BoxEvent(row.game, at, at, fetch, BoxLine(row.home_score + bump, fga, fta, oreb, tov),
                    BoxLine(row.away_score, fga, fta, oreb + bump, tov))


def _views(w, t):
    """Tail rows and events the store holds at the training cutoff and at serving (filtered as adaptive.available_rows
    and candidates.live._events), the forecast cutoffs of the games in flight at the training cutoff (no result in the
    store yet; one still unfinished `ingest.OBSERVED_WINDOW` after its start is stuck, not in flight), and `changed(at)`:
    whether results / box-shot data dated at or before `at` (the cutoff of the frozen state) changed after the fit, and
    whether a game in flight at `at` has a result at serving (a stuck one)."""
    train, serve = [], []
    for g in t.games:
        on_time = g["avail"] <= t.cut
        if on_time and not g["late"]:
            train.append(_row(w, g, False, g["avail"]))
        if on_time and not g["late"] and g["corrected"]:
            serve.append(_row(w, g, True, t.fix))
        else:
            serve.append(_row(w, g, False, g["avail"]))
    serve = [r for r in serve if r.result_available_at <= _ts(t.now)]
    t_known, s_known = {r.game.game_id: r for r in train}, {r.game.game_id: r for r in serve}
    t_events, s_events, t_boxes, s_boxes = [], [], {}, {}
    for g in t.games:
        if not g["box"]:
            continue
        if g["id"] in t_known and not g["box_late"]:
            e = _event(w, g, t_known[g["id"]])
            if max(e.available_at, t_known[g["id"]].result_available_at) <= _ts(t.cut):
                t_events.append(e)
                t_boxes[g["id"]] = g                       # the player box is published with the team box
        if g["id"] in s_known:
            e = _event(w, g, s_known[g["id"]])
            if max(e.available_at, s_known[g["id"]].result_available_at) <= _ts(t.now):
                s_events.append(e)
                s_boxes[g["id"]] = g

    def changed(at):
        results = any(g["avail"] <= _dt(at) and (g["late"] or g["corrected"]) for g in t.games)
        events = any(g["box"] and g["box_late"] and g["id"] in t_known and g["id"] in s_known
                     and max(_ts(g["avail"] + g["box_delay"]), t_known[g["id"]].result_available_at) <= at
                     and max(_ts(g["avail"] + g["box_delay"]), s_known[g["id"]].result_available_at) <= _ts(t.now)
                     for g in t.games)
        landed = [s_known[g["id"]] for g in t.games if g["id"] not in t_known and g["id"] in s_known
                  and s_known[g["id"]].cutoff_at <= at < s_known[g["id"]].result_available_at]
        season_start = any(r.game.season < x.game.season and x.cutoff_at < r.result_available_at <= at
                           for x in landed for r in w.rows + list(s_known.values()))
        return results, events, (bool(landed), season_start)
    # player members: box rows of a result the fit consumed appeared or changed since (rows of later games may be added)
    changed_boxes = any((gid in t_boxes) != (gid in s_boxes) for gid in t_known)
    in_flight = [r.cutoff_at for r in (_row(w, g, False, g["avail"]) for g in t.games)
                 if r.cutoff_at <= _ts(t.cut) and r.game.game_id not in t_known
                 and _dt(r.game.tipoff_at) >= t.cut - ingest.OBSERVED_WINDOW]
    views = SimpleNamespace(t_inputs=_player_inputs(w, t, t_boxes), s_inputs=_player_inputs(w, t, s_boxes),
                            changed_boxes=changed_boxes, in_flight=in_flight, changed=changed)
    return train, t_events, serve, s_events, views


def _numbers(x):
    """Every number in a forecast (probability, score distribution), in a stable order, for an exact comparison."""
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return [float(x)]
    if isinstance(x, dict):
        return [v for k in sorted(x, key=repr) for v in [*_numbers(k), *_numbers(x[k])]]
    if isinstance(x, (list, tuple)):
        return [v for i in x for v in _numbers(i)]
    if hasattr(x, "__dict__"):
        return _numbers({k: v for k, v in vars(x).items() if not k.startswith("_cache")})
    return []


def _forecast(m, game):
    """What the serving cycle records for one game: the probability, then the score distribution (adaptive._record)."""
    p = m.predict(game)
    inner = getattr(m, "member", m)
    return [p, *(_numbers(m.distribution(game)) if hasattr(inner, "distribution") else [])]


def _train(w, name, rows, events, cut, inputs, in_flight):
    """adaptive.train's walk of one member over the training view, from the walked history, to the cutoff of its frozen
    state (`adaptive.frozen_at`: online members stop before the games in flight at the training cutoff). (member, cutoff)"""
    m = _walked(w, name, inputs)
    at = adaptive.frozen_at(m, w.rows + rows, in_flight, cut)
    adaptive._walk_until(m, rows, adaptive._events_for(m, w.eng, events), {r.game.game_id: r for r in rows}, at)
    return m, at


def _freeze(w, m, nxt, cut, at, rows, events):
    """adaptive.train after the walk: the training-time forecast of the next game (learned members), frozen; the
    artifact with the digests of what the state consumed (results and box/shot data available at its cutoff `at`)."""
    adaptive._training_forecast(m, replace(nxt, cutoff_at=cut))
    status = getattr(getattr(m, "member", m), "input_status", None)
    full = w.rows + rows
    known = {r.game.game_id: r for r in full}
    ev = adaptive._events_for(m, w.eng, w.events + events)
    return {"cutoff": at, "state": adaptive.freeze(m),
            "delivered_digest": w.eng.dataset_version([r for r in full if r.result_available_at <= at]),
            "events_digest": adaptive._events_digest(ev, known, at), "inputs": status() if status else None}


def _serve(w, name, m, art, rows, events, now, nxt, inputs):
    """adaptive._record for one walk member: inputs re-read, guards, advance, season guard, forecast. (reason, forecast)."""
    _attach(m, inputs)
    check = getattr(getattr(m, "member", m), "input_mismatch", None)
    mismatch = check(art.get("inputs") or {}) if check else None
    if mismatch:
        return mismatch, None
    full = w.rows + rows
    known = {r.game.game_id: r for r in full}
    ev = adaptive._events_for(m, w.eng, w.events + events)
    reason = adaptive._frozen_state_unusable(w.eng, m, art, full, ev, known, {})
    if reason:
        return reason, None
    adaptive._advance(m, art, full, ev, known, now)
    season_fit = adaptive._season_fit(m)
    if season_fit is not None and nxt.season > season_fit:
        return "awaiting the scheduled season fit", None
    return None, _forecast(m, replace(nxt, cutoff_at=now))


def _check_serving(w, sport, name, t):
    """Properties 1-2 and the guards for one member and one tail; returns (reason, forecast) of the served member."""
    cut, now = _ts(t.cut), _ts(t.now)
    t_rows, t_events, s_rows, s_events, v = _views(w, t)
    trained, at = _train(w, name, t_rows, t_events, cut, v.t_inputs, v.in_flight)
    assert at == cut or (sport, name) in ONLINE, (name, at)                     # learned members are frozen at the cutoff
    art = _freeze(w, trained, t.nxt, cut, at, t_rows, t_events)
    started = adaptive._season_fit(trained)                                      # learned: by the training-time forecast
    season_late = started is not None and any(r.result_available_at > at and r.game.season < started for r in s_rows)
    served = adaptive.thaw(copy.deepcopy(w.mems[name].obj), art["state"])
    reason, got = _serve(w, name, served, art, s_rows, s_events, now, t.nxt, v.s_inputs)
    gets_events = bool(adaptive._events_for(served, w.eng, [None]))
    players = (sport, name) in PLAYERS
    changed_results, changed_events, (landed, season_start) = v.changed(at)
    if (changed_results or season_late or (gets_events and changed_events) or (players and v.changed_boxes)
            or ((sport, name) in ONLINE and landed and ((sport, name) in FORECAST_STATE or season_start))):
        assert reason is not None and not reason.startswith("awaiting"), (reason, changed_results, season_late)  # a cause: out
    else:                                                                        # no outage without a cause; only a learned
        assert reason is None or (reason.startswith("awaiting") and (sport, name) not in ONLINE), reason  # member awaits a fit
    if got is None:
        return reason, got
    _, kept = _serve(w, name, trained, art, s_rows, s_events, now, t.nxt, v.s_inputs)   # 2. the artifact loses nothing
    assert got == pytest.approx(kept, abs=1e-12)
    if (sport, name) in ONLINE:                                                  # 1. serving equals a fresh walk
        fresh = _walked(w, name, v.s_inputs)
        backtest.run(fresh, s_rows, adaptive._events_for(fresh, w.eng, s_events), deliver_until=now)
        assert got == pytest.approx(_forecast(fresh, replace(t.nxt, cutoff_at=now)), abs=1e-12)
    return reason, got


@pytest.mark.parametrize("sport,name", ALL)
@SETTINGS
@given(data=st.data())
def test_a_served_member_forecasts_as_its_frozen_fit_and_online_members_as_a_fresh_walk(worlds, sport, name, data):
    _check_serving(worlds[sport], sport, name, data.draw(tails(worlds[sport])))


def _pinned(w, case):
    """Fixed tails for cases the generator reaches rarely (found by it or by review), relative to the world's history."""
    start = _dt(w.end) + timedelta(days=1)
    a, b, c = w.teams[:3]

    def game(i, home, away, tip, avail, **kw):
        hs, as_ = (3, 1) if w.sport == "nhl" else (104, 97 + i)
        return {"i": i, "id": f"{w.prefix}:9{w.season}{i:04d}", "season": w.season, "home": home, "away": away, "hs": hs,
                "as": as_, "decision": "REG" if w.sport == "nhl" else None, "tip": tip, "avail": avail, "late": False,
                "corrected": False, "box": True, "box_delay": timedelta(0), "box_late": False, "seed": 7 + i, "debut": False,
                "line": [88, 22, 11, 14], **kw}
    tip0 = start + timedelta(hours=21)
    g0 = game(0, a, b, tip0, tip0 + timedelta(minutes=150))
    if case == "late box":            # seed-39 review example: a consumed game's box lands a minute after a later cutoff
        cut = g0["avail"]
        g0["box_delay"] = timedelta(hours=19, minutes=31)
        tip1 = cut + g0["box_delay"] + timedelta(hours=2, minutes=-1)
        games = [g0, game(1, a, c, tip1, tip1 + timedelta(minutes=150))]
    elif case == "in flight":         # a game whose cutoff passed before the fit, its result after; a result in between
        tip1 = start + timedelta(days=1, hours=21)
        cutoff1 = tip1 - timedelta(hours=2)
        g2 = game(2, b, a, cutoff1 - timedelta(hours=1), cutoff1 + timedelta(hours=1))
        cut = cutoff1 + timedelta(hours=2)
        games = [g0, game(1, a, c, tip1, tip1 + timedelta(minutes=150)), g2]
    elif case == "in flight, chained":   # review 2026-10-17: stepping back before the in-flight game (A, cutoff 20:00)
        day = start + timedelta(days=1)    # lands where B (cutoff 17:00, result 21:30) is in flight, after C's result
        gb = game(1, a, c, day + timedelta(hours=19), day + timedelta(hours=21, minutes=30))
        gc = game(2, b, c, day + timedelta(hours=16), day + timedelta(hours=18, minutes=30))
        ga = game(3, b, a, day + timedelta(hours=22), day + timedelta(hours=24, minutes=30))
        cut = day + timedelta(hours=23)
        games = [g0, gc, gb, ga]
    elif case in ("stuck landed", "stuck landed, season start"):   # review 2026-10-17: still scheduled 30 h after its
        new = case.endswith("season start")                          # start at the fit, its result 2 h after the fit
        cut = start + timedelta(days=2, hours=3)
        gs = game(1, a, b, start + timedelta(hours=21), cut + timedelta(hours=2), season=w.season + new)
        games = [gs, game(2, a, c, start + timedelta(hours=26), start + timedelta(hours=28, minutes=30))]
        if new:                       # the new season's first forecast precedes the old season's last result
            games[1].update(tip=start + timedelta(hours=20), avail=start + timedelta(hours=23))
    elif case == "previous season late":   # the fit starts the next season; a result of this one arrives after it
        tip1 = start + timedelta(days=1, hours=21)
        cut = tip1 + timedelta(minutes=30)
        games = [g0, game(1, a, c, tip1, tip1 + timedelta(minutes=150))]
    elif case == "backfill":          # a box dated before the cutoff, imported after the fit
        g0.update(box_delay=timedelta(minutes=10), box_late=True)
        cut = g0["avail"] + timedelta(hours=1)
        games = [g0]
    now = max(g["avail"] for g in games) + timedelta(hours=1)
    nxt = PregameGame(f"{w.prefix}:8{w.season}0000", w.season + 1, "regular", _ts(now + timedelta(hours=3)), a, c, False)
    return SimpleNamespace(games=games, cut=cut, now=now, fix=cut + timedelta(minutes=1), nxt=nxt, T=cut, injuries=[])


@pytest.mark.parametrize("case,sport,name,outage", [
    ("late box", "nba", "efficiency_margin", False), ("late box", "nba", "elo", False),
    ("in flight", "nba", "efficiency_margin", False), ("in flight", "nba", "elo", False),
    ("in flight", "nhl", "hockey_elo", False), ("in flight", "nhl", "goals_poisson", False),
    ("in flight, chained", "nba", "efficiency_margin", False), ("in flight, chained", "nba", "elo", False),
    ("in flight, chained", "nhl", "hockey_elo", False), ("in flight, chained", "nhl", "goals_poisson", False),
    ("stuck landed", "nba", "elo", False), ("stuck landed", "nba", "points_normal", False),
    ("stuck landed", "nba", "efficiency_margin", True), ("stuck landed", "nhl", "hockey_elo", False),
    ("stuck landed", "nhl", "goals_poisson", False),
    ("stuck landed, season start", "nba", "elo", True), ("stuck landed, season start", "nhl", "hockey_elo", True),
    ("stuck landed, season start", "nba", "points_normal", True),   # the worlds' Elo has carry 1.0: points_normal (and the
                                                                    # production-Elo test in test_adaptive) shows the cause
    ("previous season late", "nba", "elo", False), ("previous season late", "nba", "logistic@adaptive", True),
    ("previous season late", "nhl", "hockey_elo", False), ("previous season late", "nhl", "hockey_logistic@adaptive", True),
    ("backfill", "nba", "efficiency_margin", True), ("backfill", "nba", "elo", False),
    ("backfill", "nhl", "hockey_logistic_xg@adaptive", True), ("backfill", "nhl", "goals_poisson", False)])
def test_pinned_serving_cases(worlds, case, sport, name, outage):
    """The serving properties on fixed tails (Hypothesis cannot take explicit examples for an interactive `st.data()` draw):
    online members keep forecasting exactly as a fresh walk; only the members a cause reaches are out, until a retrain."""
    reason, got = _check_serving(worlds[sport], sport, name, _pinned(worlds[sport], case))
    assert (reason is not None) == outage and (got is None) == outage, reason


@pytest.mark.parametrize("sport,name", sorted(ONLINE))
def test_the_online_members_whose_forecast_enters_their_state_are_exactly_the_listed_ones(worlds, sport, name):
    """`adaptive.FORECAST_IN_STATE` (and FORECAST_STATE here) decide whether a stuck game landing after the fit makes an
    online member missing (`adaptive._landed_in_flight`): a forecast within the walked season must leave every other
    member's state unchanged."""
    w = worlds[sport]
    m = _walked(w, name)
    before = adaptive.freeze(m)
    a, b = w.teams[:2]
    m.predict(PregameGame(f"{w.prefix}:7{w.season}0000", w.season, "regular", _ts(_dt(w.end) + timedelta(days=1)), a, b, False,
                          cutoff_at=w.end))
    assert (adaptive.freeze(m) != before) == ((sport, name) in FORECAST_STATE)
    assert isinstance(getattr(m, "member", m), adaptive.FORECAST_IN_STATE) == ((sport, name) in FORECAST_STATE)


@pytest.mark.parametrize("sport,name", ALL)
@SETTINGS
@given(data=st.data())
def test_no_forecast_depends_on_anything_available_after_its_cutoff(worlds, sport, name, data):
    w = worlds[sport]
    t = data.draw(tails(w))
    T = _ts(t.T)

    def walk(alter):
        rows = [_row(w, g, alter and _ts(g["avail"]) > T, g["avail"]) for g in t.games]
        by_id = {r.game.game_id: r for r in rows}
        events = []
        for g in t.games:
            if g["box"]:
                eff = max(_ts(g["avail"] + g["box_delay"]), _ts(g["avail"]))
                events.append(_event(w, g, by_id[g["id"]], altered=alter and eff > T))
        changed = {g["id"] for g in t.games if alter and _ts(g["avail"]) > T}      # player boxes: by result time
        m = _walked(w, name, _player_inputs(w, t, {g["id"]: g for g in t.games}, altered=changed, later=alter))
        return {p["game_id"]: p["f"] for p in backtest.run(m, rows, adaptive._events_for(m, w.eng, events),
                                                           extra=lambda model, g: {"f": _forecast(model, g)})
                if p["cutoff_at"] <= T}
    base, altered = walk(False), walk(True)
    assert base.keys() == altered.keys()
    for gid in base:
        assert altered[gid] == pytest.approx(base[gid], abs=1e-12), gid


# --- The combiner (adaptive.fit_combiner / combine_now) ---------------------------------------------------------------
@st.composite
def histories(draw):
    """Out-of-sample member vectors per game as adaptive.train builds them for fit_combiner, and a fit cutoff."""
    market = draw(st.sampled_from(("winner", "total", "reg3")))
    names = [f"m{i}" for i in range(draw(st.integers(2, 4)))]
    n = draw(st.integers(45, 90))
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        vectors = {}
        for m in names:
            if draw(st.integers(0, 9)) == 0:
                continue                                                    # this member had no forecast for the game
            p, q = draw(st.floats(0.05, 0.95)), (draw(st.floats(0.0, 0.2)) if market == "total" else 0.0)
            if market == "reg3":
                r = draw(st.floats(0.05, 0.4))
                vectors[m] = (p * (1 - r), r, (1 - p) * (1 - r))
            else:
                vectors[m] = (p * (1 - q), q, (1 - p) * (1 - q))
        at = base + timedelta(hours=6 * i)
        rows.append({"game_id": f"g{i:03d}", "season": 2026, "cutoff_at": _ts(at), "result_available_at": _ts(at + timedelta(hours=5)),
                     "realised": draw(st.sampled_from((0, 1, 2) if market != "winner" else (0, 2))), "vectors": vectors})
    cutoff = _ts(base + timedelta(hours=6 * (n - draw(st.integers(0, n // 3))) + 2))      # mostly enough rows to fit
    groups = (tuple(names[:2]),) if len(names) >= 4 and draw(st.booleans()) else ()
    return market, names, rows, cutoff, groups


def _canon(cm):
    def default(o):
        return o.tolist() if hasattr(o, "tolist") else str(o)
    return json.dumps({k: v for k, v in cm.items() if k != "rows_after_cutoff"}, sort_keys=True, default=default)


@settings(max_examples=EXAMPLES * 2, deadline=None)
@given(h=histories(), data=st.data())
def test_a_combiner_uses_only_outcomes_available_at_its_cutoff_whatever_the_row_order(h, data):
    market, names, rows, cutoff, groups = h
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(adaptive, "MIN_TRAIN", 30)
        fit = adaptive.fit_combiner(rows, names, market, cutoff, groups=groups)
        later = [{**r, "realised": (2 - r["realised"]) if r["realised"] != 1 else 0,
                  "vectors": {m: tuple(reversed(v)) for m, v in r["vectors"].items()}}
                 if r["result_available_at"] > cutoff else r for r in rows]                  # every later outcome changed
        assert _canon(adaptive.fit_combiner(later, names, market, cutoff, groups=groups)) == _canon(fit)
        shuffled = data.draw(st.permutations(rows))
        assert _canon(adaptive.fit_combiner(shuffled, names, market, cutoff, groups=groups)) == _canon(fit)
    assert fit["rows_available"] == sum(1 for r in rows if r["result_available_at"] <= cutoff and
                                        r["realised"] in ((0, 1, 2) if market == "reg3" else (0, 2)))


@settings(max_examples=EXAMPLES * 2, deadline=None)
@given(h=histories(), data=st.data())
def test_a_combination_uses_only_available_members_and_abstains_without_a_trained_subset(h, data):
    market, names, rows, cutoff, groups = h
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(adaptive, "MIN_TRAIN", 30)
        cm = adaptive.fit_combiner(rows, names, market, cutoff, groups=groups)
    have = {m: rows[0]["vectors"].get(m) or (0.3, 0.1, 0.6) for m in data.draw(st.sets(st.sampled_from(names)))}
    have = {m: v if market != "winner" else (v[0] / (v[0] + v[2]), 0.0, v[2] / (v[0] + v[2])) for m, v in have.items()}
    p, stack, used, reason = adaptive.combine_now(cm, have)
    if cm.get("stack") is None:
        assert p is None and reason.startswith("no combination trained")
        return
    missing = [m for m in cm["members"] if m not in have]
    trained = (not missing or (len(missing) == 1 and missing[0] in cm["fallbacks"])
               or any(set(g["missing"]) == set(missing) for g in cm["group_fallbacks"]))
    assert (p is not None) == trained, reason
    if p is not None:
        assert set(used) <= set(have) and not set(used) & set(missing)
        assert len(p) == 3 and all(0 <= x <= 1 for x in p) and abs(sum(p) - 1) < 1e-6
        if market != "total":
            assert p[1] == 0 or market == "reg3"                                  # no push mass where none exists
