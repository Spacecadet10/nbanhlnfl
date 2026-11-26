"""The NBA/NHL forward comparison is made against the PUBLISHED forecast (game_forecasts), on candidate inputs frozen at the
published row's information time; displayed pairing counts come from the evaluator's own pairing; every sport gets a forward
status; the prospective start of the repaired comparison is not established (None) and nothing before it ever counts.
Synthetic forward-store rows and a synthetic NHL store; no network."""
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import autoresearch as ar
from app import candidates_view, coverage, main
from app import research_status as rs
from test_live_markets import sealed  # noqa: F401  (fixture)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)
from test_research_live import GID, game, run  # noqa: F401  (fixture)

FMT = "%Y-%m-%dT%H:%M:%SZ"
T0 = datetime(2026, 12, 1, 12, tzinfo=timezone.utc)
START = (T0 + timedelta(minutes=600)).strftime(FMT)           # a game starting 10 h after T0


def ts(minutes, seconds=0):
    return (T0 + timedelta(minutes=minutes, seconds=seconds)).strftime(FMT)


def put(path, model, role, run_at, info, line, pa, *, gid="g1", start=START, market="total", sport="nba", version="v",
        basis="auto", orientation=None, mid=None):
    """One forward row. basis 'auto': a published row / a pool candidate row carrying its information time; None: a row
    recorded before the repair (no basis at all)."""
    if basis == "auto":
        basis = {"kind": "published" if model == ar.PUBLISHED else "pool_candidate", "information_at": info,
                 "orientation": orientation or ar.SIDE_ORIENTATION[market]}
    probs = {"a": pa, "b": 1 - pa} if mid is None else {"a": pa, "mid": mid, "b": 1 - pa - mid}
    r = {"game_id": gid, "season": 2027, "start_at": start, "forecast_cutoff": info or run_at, "forecast_at": run_at,
         "market": market, "line": line, "probs": probs, "outcome": None, "model_version": version}
    if basis is not None:
        r["basis"] = basis
    out = ar.forward_record(path, sport=sport, protocol_id="p", model=model, role=role, model_version=version, data_version="d",
                            forecasted_at=run_at, records=[r], now=run_at)
    assert out["inserted"] == 1, out
    return out


def pair_row(path, gid, *, bar_info=ts(0), cand_info=None, cand_run=None, bar_line=150.5, cand_line=150.5, cand_basis="auto",
             cand_orientation=None, start=START, sport="nba", market="total", cand="cand", bar=ar.PUBLISHED, mid=None):
    cand_info_ = cand_info if cand_info is not None else bar_info
    put(path, bar, "incumbent", bar_info, bar_info, bar_line, 0.5, gid=gid, start=start, sport=sport, market=market, mid=mid)
    put(path, cand, "candidate", cand_run or cand_info_, cand_info_, cand_line, 0.6, gid=gid, start=start, sport=sport,
        market=market, basis=cand_basis, orientation=cand_orientation, mid=mid)


def grade(path, gids, sport="nba", market="total", line=150.5, outcome="a"):
    c = ar.forward_connect(path)
    with c:
        for g in gids:
            c.execute("INSERT INTO rf_grades (sport, game_id, market, line_key, outcome, graded_at) VALUES (?,?,?,?,?,?)",
                      (sport, g, market, ar._lk(line), outcome, "x"))
    c.close()


def rows_of(path, sport="nba"):
    return ar._forward_rows(path, sport)[0]


# --- 1. the published row is read read-only from game_forecasts and recorded with market, side, line and information time --------
def _cycle(conn, at="2026-10-12T21:30:00Z"):
    run(conn, at)
    out = coverage.record(conn, now=at)
    assert out["status"] == "success", out["errors"]
    return at


def test_the_published_reader_returns_the_canonical_row_of_every_market_with_its_line_and_information_time(game):
    at = _cycle(game)
    rows = candidates_view.upcoming_published(game, "2026-10-12T21:40:00Z")
    assert {r["market"] for r in rows} == {"winner", "reg3", "spread", "total", "team_total_home", "team_total_away"}
    g = game.execute("SELECT * FROM games WHERE game_id=?", (GID,)).fetchone()
    for m in {r["market"] for r in rows}:
        canon = coverage.canonical(game, [g], m, "2026-10-12T21:40:00Z")[GID]["record"]
        mine = [r for r in rows if r["market"] == m][-1]                  # the latest recorded run = the canonical selection
        assert mine["forecast_id"] == canon["forecast_id"] and mine["generated_at"] == canon["generated_at"] == at
        assert (mine["p_a"], mine["p_mid"], mine["p_b"]) == tuple(canon["p"]) and mine["line"] == canon["line"]
        assert mine["model_version"] == canon["model_version"] and mine["source"] == canon["source"]
    assert all(r["source"] == "primary_recorded" and r["source_generated_at"] == at for r in rows)


def test_the_published_reader_ignores_missing_rows_preseason_and_a_moved_start(game):
    _cycle(game)
    assert candidates_view.upcoming_published(game, "2026-10-12T23:00:00Z") == []      # the game has started: nothing ahead
    game.execute("UPDATE games SET season_type='preseason' WHERE game_id=?", (GID,))
    assert candidates_view.upcoming_published(game, "2026-10-12T21:40:00Z") == []      # preseason is never canonical
    game.execute("UPDATE games SET season_type='regular', tipoff_at='2026-10-12T23:30:00Z' WHERE game_id=?", (GID,))
    assert candidates_view.upcoming_published(game, "2026-10-12T21:40:00Z") == []      # a row for a superseded start is not carried


def test_the_recorder_records_the_published_forecast_as_the_incumbent_with_side_line_and_information_time(game, tmp_path):
    _cycle(game)
    fn = ar._study_upcoming("nhl", {"winner": [], "lines": []})
    recs = fn(game, "2026-10-12T21:40:00Z", ar.PUBLISHED)
    by = {r["market"]: r for r in recs}
    assert set(by) == {"winner", "reg3", "spread", "total", "team_total_home", "team_total_away"}
    pred = game.execute("SELECT home_win_prob FROM predictions WHERE game_id=? AND model_version NOT LIKE 'research-%'", (GID,)).fetchone()
    assert by["winner"]["probs"]["a"] == pytest.approx(pred[0]) and by["winner"]["probs"]["b"] == pytest.approx(1 - pred[0])   # a = home
    mf = game.execute("SELECT line, line_source FROM market_forecasts WHERE game_id=? AND market='total' AND model_version "
                      "NOT LIKE 'research-%'", (GID,)).fetchone()
    assert by["total"]["line"] == mf["line"] and by["total"]["line_source"] == "bookmaker" and mf["line_source"] == "market"
    for m, r in by.items():
        assert r["forecast_cutoff"] == r["forecast_at"] == "2026-10-12T21:30:00Z"
        assert r["basis"]["kind"] == "published" and r["basis"]["information_at"] == r["forecast_cutoff"]
        assert tuple(r["basis"]["orientation"]) == ar.SIDE_ORIENTATION[m] and r["basis"]["source"] == "primary_recorded"
    p = tmp_path / "research" / ar.FORWARD_FILE
    out = ar.forward_record(p, sport="nhl", protocol_id="p", model=ar.PUBLISHED, role="incumbent", model_version="x",
                            data_version="d", forecasted_at="2026-10-12T21:40:00Z", records=recs, now="2026-10-12T21:40:00Z")
    assert out["inserted"] == 6 and not out["rejected"]
    stored = ar.forward_connect(p).execute("SELECT basis, model_version FROM rf_forecasts WHERE market='total'").fetchone()
    assert json.loads(stored["basis"])["information_at"] == "2026-10-12T21:30:00Z" and stored["model_version"] == by["total"]["model_version"]


def test_the_scheduled_recorder_stores_the_published_rows_as_baseline_rows_never_as_candidate_rows(game, tmp_path):
    _cycle(game)
    class Open:                                       # the recorder closes the store it opened: keep the fixture's open
        def __getattr__(self, name):
            return getattr(game, name)

        def close(self):
            pass
    adapter = ar.nba_nhl_adapter("nhl") | {"connect": Open}
    data = tmp_path / "data"
    data.mkdir()
    res = ar.record(data, "2026-10-12T21:40:00Z", "nhl", adapters={"nhl": adapter})
    assert res["candidate_rows"] == 0 and res["rejected"] == 0 and ar.PUBLISHED not in res["candidates"], res
    c = ar.forward_connect(data / "research" / ar.FORWARD_FILE)
    roles = {(r["model"], r["role"]) for r in c.execute("SELECT model, role FROM rf_forecasts")}
    assert (ar.PUBLISHED, "incumbent") in roles and all(role == "incumbent" for _, role in roles)
    assert c.execute("SELECT COUNT(*) FROM rf_forecasts WHERE model=?", (ar.PUBLISHED,)).fetchone()[0] == 6
    again = ar.record(data, "2026-10-12T21:40:00Z", "nhl", adapters={"nhl": adapter})
    assert again["inserted"] == 0 and again["already_recorded"] == res["inserted"]            # a retry changes nothing


def test_the_published_incumbent_is_a_forward_model_of_nba_and_nhl_never_a_walked_member_and_the_named_bar():
    for sport in ("nba", "nhl"):
        a = ar.nba_nhl_adapter(sport)
        assert a["forward_models"][ar.PUBLISHED]["role"] == "incumbent" and a["published_bar"] is True
        assert ar.PUBLISHED not in a["models"]                      # never walked historically: it exists only as a stored forecast
        assert set(ar.FORWARD_PAIRING["bar"][sport].values()) == {ar.PUBLISHED}
        assert set(ar.FORWARD_PAIRING["bar"][sport]) == set(ar.NBA_NHL_MARKETS[sport])


def test_side_orientation_matches_the_apps_own_side_labels():
    from app import markets
    for m, (a, mid, b) in ar.SIDE_ORIENTATION.items():
        la, _, lb = markets.side_labels(m, 1.5, "HOME", "AWAY")
        if m in ("winner", "reg3", "spread"):
            assert (a, b) == ("home", "away") and la.startswith("HOME") and lb.startswith("AWAY")
        else:                                           # total and the team totals: a = over, b = under
            assert (a, b) == ("over", "under") and "over" in la.lower() and "under" in lb.lower()


def test_a_forward_store_without_basis_gains_it_and_keeps_its_rows(tmp_path):
    p = tmp_path / "old.sqlite"
    old = sqlite3.connect(p)
    old.executescript(ar.FORWARD_SCHEMA)
    old.execute("INSERT INTO rf_forecasts (sport, protocol_id, model, role, model_version, data_version, game_id, market, line_key, "
                "start_at, forecast_cutoff, forecasted_at, p_a, p_b, recorded_at) VALUES ('nba','p','m','candidate','v','d','g',"
                "'winner','','2026-11-23T00:00:00Z','2026-11-22T00:00:00Z','2026-11-22T00:00:00Z',0.6,0.4,'x')")
    old.commit()
    old.close()
    c = ar.forward_connect(p)
    row = c.execute("SELECT * FROM rf_forecasts").fetchone()
    assert row["basis"] is None and row["p_a"] == 0.6 and c.execute("SELECT COUNT(*) FROM rf_forecasts").fetchone()[0] == 1


# --- 2. the pairing: candidate never later than the published bar, at most 10 min before, same line, same side ---------------------
def test_a_candidate_one_second_later_than_the_published_row_is_unpaired_and_ten_minutes_before_is_paired(tmp_path):
    p = tmp_path / "t.sqlite"
    pair_row(p, "same")
    pair_row(p, "later", cand_info=ts(0, 1))                    # 1 s after the bar's information time
    pair_row(p, "ten", cand_info=ts(-10))                       # exactly 10 min before: allowed
    pair_row(p, "ten1", cand_info=ts(-10, -1))                  # 10 min 1 s before: too early
    pr = ar.forward_pairs(rows_of(p), "cand", ar.PUBLISHED, "total", require_information_time=True)
    assert sorted(pr["unpaired"]) == ["later", "ten1"] and pr["paired_games"] == 2
    assert sorted(pr["unpaired_timing"]) == ["later", "ten1"] and pr["unpaired_line"] == [] and pr["missing"] == []
    assert pr["candidate_later_pairs"] == 0 and ar.FORWARD_PAIRING["max_candidate_later_minutes"] == 0   # the rule is unchanged


def test_a_different_line_or_side_orientation_is_unpaired_and_classified(tmp_path):
    p = tmp_path / "t.sqlite"
    pair_row(p, "ok")
    pair_row(p, "line", cand_line=151.5)
    pair_row(p, "side", cand_orientation=("under", "push", "over"))        # the candidate's a/b are the opposite way round
    pr = ar.forward_pairs(rows_of(p), "cand", ar.PUBLISHED, "total", require_information_time=True)
    assert pr["paired_games"] == 1 and pr["unpaired_line"] == ["line"] and pr["unpaired_orientation"] == ["side"]
    assert sorted(pr["unpaired"]) == ["line", "side"] and pr["unpaired_timing"] == []


def test_a_candidate_without_an_information_time_never_pairs_and_is_labelled_not_missing(tmp_path):
    p = tmp_path / "t.sqlite"
    pair_row(p, "good")
    pair_row(p, "old", cand_basis=None)                         # recorded before the repair: no basis, no information time
    pair_row(p, "noinfo", cand_basis={"kind": "pool_candidate", "information_at": None})
    pr = ar.forward_pairs(rows_of(p), "cand", ar.PUBLISHED, "total", require_information_time=True)
    assert pr["paired_games"] == 1 and pr["missing"] == [] and pr["unpaired"] == []
    assert sorted(pr["invalid_information"]) == ["noinfo", "old"]
    assert pr["invalid_reasons"]["old"] == "invalid: no information time (pre-repair)"
    assert pr["invalid_reasons"]["noinfo"] == "invalid: no information time"
    legacy = ar.forward_pairs(rows_of(p), "cand", ar.PUBLISHED, "total")        # the plain pairing (other sports) is unchanged
    assert legacy["paired_games"] == 3 and legacy["invalid_information"] == []


def test_a_stored_cutoff_that_disagrees_with_the_stated_information_time_is_invalid(tmp_path):
    p = tmp_path / "t.sqlite"
    pair_row(p, "g1")
    put(p, "cand", "candidate", ts(0), ts(0), 150.5, 0.6, gid="g2", basis={"kind": "pool_candidate", "information_at": ts(-5),
                                                                          "orientation": ar.SIDE_ORIENTATION["total"]})
    put(p, ar.PUBLISHED, "incumbent", ts(0), ts(0), 150.5, 0.5, gid="g2")
    pr = ar.forward_pairs(rows_of(p), "cand", ar.PUBLISHED, "total", require_information_time=True)
    assert pr["invalid_information"] == ["g2"] and pr["paired_games"] == 1


# --- 3. displayed counts are the evaluator's own ---------------------------------------------------------------------------
def _mixed_store(p):
    pair_row(p, "ok1")
    pair_row(p, "ok2")
    pair_row(p, "timing", cand_info=ts(0, 1))
    pair_row(p, "line", cand_line=151.5)
    pair_row(p, "old", cand_basis=None)
    put(p, ar.PUBLISHED, "incumbent", ts(0), ts(0), 150.5, 0.5, gid="nocand")
    put(p, "cand", "candidate", ts(0), None, 150.5, 0.6, gid="nobar", basis=None)                  # a candidate game with no published row
    grade(p, ["ok1", "timing"])


def test_pair_counts_use_the_pairing_function_and_separate_every_ineligible_category(tmp_path):
    p = tmp_path / "t.sqlite"
    _mixed_store(p)
    c = ar.forward_pair_counts(p, "nba", start=ts(-1000))
    t = c["totals"]["confirmatory"]
    assert (t["valid_pairs"], t["valid_pairs_graded"], t["unpaired_timing"], t["unpaired_line"], t["missing"],
            t["invalid_no_information_time"], t["no_published_bar"]) == (2, 1, 1, 1, 1, 2, 0)
    assert t["unpaired_orientation"] == 0 and t["as_of_late"] == 0
    assert c["totals"]["exploratory"]["valid_pairs"] == 0 and c["totals"]["all"]["valid_pairs"] == 2
    forward = ar._forward_stage(p, {"sport": "nba", "data_version": "d", "markets": {"total": {"kind": "line"}},
                                    "unsupported": {}, "models": {}, "published_bar": True, "periods": {}}, "p")
    fw = forward["markets"]["total"]["comparisons"]["cand"]["vs_incumbent"]["forward"]
    assert fw["paired_games"] == t["valid_pairs"] and fw["unpaired_games"] >= t["unpaired_timing"] + t["unpaired_line"]
    assert fw["invalid_no_information_time_games"] == 1 and fw["missing_games"] == t["missing"]


def test_games_before_the_prospective_start_are_exploratory_in_the_counts(tmp_path):
    p = tmp_path / "t.sqlite"
    pair_row(p, "early", start=ts(300), bar_info=ts(-200))
    pair_row(p, "late1")
    pair_row(p, "late2")
    c = ar.forward_pair_counts(p, "nba", start=ts(400))
    assert c["totals"]["confirmatory"]["valid_pairs"] == 2 and c["totals"]["exploratory"]["valid_pairs"] == 1
    none = ar.forward_pair_counts(p, "nba", start=None)
    assert none["established"] is False and none["totals"]["confirmatory"]["valid_pairs"] == 0
    assert none["totals"]["exploratory"]["valid_pairs"] == 3 and none["totals"]["all"]["valid_pairs"] == 3


def test_the_status_page_counts_come_from_the_evaluators_pairing_not_a_naive_join(tmp_path):
    p = tmp_path / "research" / ar.FORWARD_FILE
    p.parent.mkdir()
    _mixed_store(p)
    c = ar.forward_connect(p)
    with c:
        c.execute("CREATE TABLE IF NOT EXISTS rf_runs (id INTEGER PRIMARY KEY, kind TEXT, sport TEXT, at TEXT, outcome TEXT, detail TEXT)")
    c.close()
    naive = sqlite3.connect(p).execute(
        "SELECT COUNT(DISTINCT c.game_id) FROM rf_forecasts c JOIN rf_forecasts i ON i.sport=c.sport AND i.game_id=c.game_id AND "
        "i.market=c.market AND i.line_key=c.line_key AND i.role='incumbent' JOIN rf_grades g ON g.sport=c.sport AND g.game_id=c.game_id "
        "AND g.market=c.market AND g.line_key=c.line_key WHERE c.role='candidate'").fetchone()[0]
    assert naive == 2                                            # the old join: "ok1" and "timing" (graded, both sides present)
    s = rs.forward_summary(p)["nba"]
    assert s["pairing"]["totals"]["all"]["valid_pairs_graded"] == 1 and s["paired_graded_items"] == 1     # "timing" is not eligible
    assert s["pairing"]["totals"]["all"]["unpaired_timing"] == 1 and s["pairing"]["totals"]["all"]["invalid_no_information_time"] == 2


def test_the_status_reader_is_read_only_and_never_adds_a_column(tmp_path):
    p = tmp_path / "research" / "forward-candidates.sqlite"
    p.parent.mkdir()
    old = sqlite3.connect(p)
    old.executescript(ar.FORWARD_SCHEMA)
    old.execute("ALTER TABLE rf_forecasts ADD COLUMN line_source TEXT")           # an older store: no basis column
    old.execute("INSERT INTO rf_forecasts (sport, protocol_id, model, role, model_version, data_version, game_id, market, line_key, "
                "start_at, forecast_cutoff, forecasted_at, p_a, p_b, recorded_at) VALUES ('nba','p','cand','candidate','v','d','g',"
                "'winner','','2026-11-23T00:00:00Z','2026-11-22T00:00:00Z','2026-11-22T00:00:00Z',0.6,0.4,'x')")
    old.commit()
    old.close()
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    s = rs.forward_summary(p)["nba"]
    assert s["pairing"]["totals"]["all"]["invalid_no_information_time"] == 1 and s["pairing"]["totals"]["all"]["valid_pairs"] == 0
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before


# --- 4. the prospective start of the repaired comparison -------------------------------------------------------------------------
def test_the_prospective_start_is_not_established_in_this_commit_and_protocol_ids_are_unchanged():
    assert ar.FORWARD_PAIRING["published_bar_from"] is None
    assert "published_bar_from" not in json.dumps(ar.PROTOCOL_V3) and "published" not in json.dumps(ar.PROTOCOL_V3)
    a = {"sport": "nba", "data_version": "dv1", "periods": {"search": [2020], "validation": [2021], "holdout": [2022]},
         "markets": {"winner": {"kind": "binary", "settlement": "x"}}}
    assert ar.v3_protocol_id(a) == "sports-research-v3-439e9c320b93"       # captured on the base commit 3774a73
    assert hashlib.sha256(json.dumps(ar.PROTOCOL_V3, sort_keys=True, default=str).encode()).hexdigest()[:16] == "feaa833a93f939d8"


def _published_adapter(**kw):
    return {"sport": "nba", "data_version": "d", "markets": {"winner": {"kind": "binary", "settlement": "x"}},
            "unsupported": {}, "periods": {"search": [2027], "validation": [2027], "holdout": []},
            "models": {"cand": {"role": "candidate", "description": "c", "version": "v"}}, "published_bar": True, "connect": lambda: None} | kw


def _season(p, n=30, first_start=300, member=False, publish=True, step=60, graded=True):
    """n winner games, published bar + candidate (information time = the bar's), graded; games start step minutes apart."""
    gids = []
    for i in range(n):
        g, start, at = f"w{i}", ts(first_start + step * i + 700), ts(step * i)
        gids.append(g)
        if publish:
            put(p, ar.PUBLISHED, "incumbent", at, at, None, 0.5 + (i % 5) / 20, gid=g, start=start, market="winner")
        put(p, "cand", "candidate", at, at, None, 0.55 + (i % 5) / 20, gid=g, start=start, market="winner")
        if member:
            put(p, "elo", "incumbent", at, at, None, 0.5, gid=g, start=start, market="winner", basis=None)
    if graded:
        grade(p, gids, market="winner", line=None, outcome="a")
    return gids


def test_while_the_start_is_not_established_nothing_is_confirmatory(tmp_path):
    p = tmp_path / "t.sqlite"
    _season(p)
    res = ar.forward_evaluate(p, _published_adapter(), "p")
    assert res["status"] == "prospective_start_not_established" and res["prospective"] == {"start": None, "established": False}
    assert res["recommendation"]["candidates"] == {} and "not established" in res["recommendation"]["reason"]
    ex = res["exploratory"]["evaluation"]["markets"]["winner"]["comparisons"]["cand"]["vs_incumbent"]["forward"]
    assert ex["paired_games"] == 30                                    # visible, as exploratory only
    assert ar.forward_connect(p).execute("SELECT COUNT(*) FROM rf_looks").fetchone()[0] == 0     # no look is logged


def test_nothing_that_started_before_the_start_ever_counts(tmp_path, monkeypatch):
    p = tmp_path / "t.sqlite"
    _season(p, n=30, step=60)                                         # starts every hour from T0+1000 min
    cut = ts(1000 + 60 * 10)                                          # the 11th game onward starts at/after the cut
    monkeypatch.setitem(ar.FORWARD_PAIRING, "published_bar_from", cut)
    res = ar.forward_evaluate(p, _published_adapter(), "p", record_look=False)
    assert res["status"] == "evaluated" and res["prospective"] == {"start": cut, "established": True}
    fw = res["evaluation"]["markets"]["winner"]["comparisons"]["cand"]["vs_incumbent"]["forward"]
    ex = res["exploratory"]["evaluation"]["markets"]["winner"]["comparisons"]["cand"]["vs_incumbent"]["forward"]
    assert fw["paired_games"] == 20 and ex["paired_games"] == 10
    assert res["recommendation"]["candidates"]["cand"]["markets"]["winner"]["criteria"]["min_settled_forward"]["n"] == 20
    c = ar.forward_pair_counts(p, "nba")                              # the counts read the same constant
    assert c["totals"]["confirmatory"]["valid_pairs"] == 20 and c["totals"]["exploratory"]["valid_pairs"] == 10
    assert all(r["start_at"] >= cut for recs in ar.forward_records(p, "nba", since=cut)[0].values() for r in recs)


def test_a_game_starting_exactly_at_the_start_counts_and_one_second_earlier_does_not(tmp_path, monkeypatch):
    p = tmp_path / "t.sqlite"
    cut = ts(2000)
    monkeypatch.setitem(ar.FORWARD_PAIRING, "published_bar_from", cut)
    for g, start in (("on", cut), ("before", ts(2000, -1))):
        put(p, ar.PUBLISHED, "incumbent", ts(0), ts(0), None, 0.5, gid=g, start=start, market="winner")
        put(p, "cand", "candidate", ts(0), ts(0), None, 0.6, gid=g, start=start, market="winner")
    c = ar.forward_pair_counts(p, "nba")
    assert c["totals"]["confirmatory"]["valid_pairs"] == 1 and c["totals"]["exploratory"]["valid_pairs"] == 1


# --- 5. the published forecast is THE bar; research members are context only -----------------------------------------------------
def test_the_published_forecast_is_the_bar_and_research_members_are_labelled_context(tmp_path, monkeypatch):
    p = tmp_path / "t.sqlite"
    monkeypatch.setitem(ar.FORWARD_PAIRING, "published_bar_from", ts(-100000))
    _season(p, member=True)
    adapter = _published_adapter(models={"cand": {"role": "candidate", "description": "c", "version": "v"},
                                         "elo": {"role": "incumbent", "description": "e", "version": "v"}})
    res = ar.forward_evaluate(p, adapter, "p", record_look=False)
    e = res["evaluation"]["markets"]["winner"]
    assert e["incumbent"] == ar.PUBLISHED
    fw = e["comparisons"]["cand"]["vs_incumbent"]["forward"]
    assert fw["bar"] == ar.PUBLISHED and fw["bar_fallback"] is False and fw["paired_games"] == 30
    ctx = e["comparisons"]["cand"]["context_incumbents"]
    assert set(ctx) == {"elo"} and "context" in ctx["elo"]["label"] and "not the published" in ctx["elo"]["label"]
    assert ctx["elo"]["paired_games"] == 30


def test_a_research_member_never_stands_in_for_a_published_bar_that_has_no_graded_rows(tmp_path, monkeypatch):
    p = tmp_path / "t.sqlite"
    monkeypatch.setitem(ar.FORWARD_PAIRING, "published_bar_from", ts(-100000))
    _season(p, member=True, publish=False)                           # members and candidate recorded, nothing published
    adapter = _published_adapter(models={"cand": {"role": "candidate", "description": "c", "version": "v"},
                                         "elo": {"role": "incumbent", "description": "e", "version": "v"}})
    res = ar.forward_evaluate(p, adapter, "p", record_look=False)
    fw = res["evaluation"]["markets"]["winner"]["comparisons"]["cand"]["vs_incumbent"]["forward"]
    assert fw["bar_fallback"] is True and fw["decision"] == "reject" and "published" in fw["reason"]
    assert fw["paired_games"] == 0 and fw["forecast"] == 0
    m = res["recommendation"]["candidates"]["cand"]["markets"]["winner"]
    assert m["status"] == "insufficient_evidence" and m["criteria"]["min_settled_forward"]["n"] == 0


def test_the_full_coverage_criterion_is_reported_unchanged(tmp_path, monkeypatch):
    p = tmp_path / "t.sqlite"
    monkeypatch.setitem(ar.FORWARD_PAIRING, "published_bar_from", ts(-100000))
    _season(p)
    m = ar.forward_evaluate(p, _published_adapter(), "p", record_look=False)["recommendation"]["candidates"]["cand"]["markets"]["winner"]
    assert m["criteria"]["full_coverage"] == {"status": "passed", "coverage": 1.0}
    src = open(ar.__file__).read()
    assert 'fw["coverage"] == 1.0' in src                              # the promotion rule is untouched


# --- 6. per-sport forward status ----------------------------------------------------------------------------------------------------
NOW = ts(30)


def _runs(p, rows):
    c = ar.forward_connect(p)
    with c:
        c.execute("CREATE TABLE IF NOT EXISTS rf_runs (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, sport TEXT NOT NULL, at TEXT NOT NULL,"
                  " outcome TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')")
        for kind, sport, outcome, detail in rows:
            c.execute("INSERT INTO rf_runs (kind, sport, at, outcome, detail) VALUES (?,?,?,?,?)", (kind, sport, NOW, outcome, detail))
    c.close()


def _ok_runs(sports, status="candidates_recording"):
    return [(k, s, "evaluated" if k == "record" else "graded", f"forward_status={status}" if k == "record" else "graded=1")
            for s in sports for k in ("record", "grade")]


def status_of(p, now=NOW):
    from app import forward_status
    return forward_status.sport_statuses(rs.forward_summary(p), now=now)


def test_every_sport_is_failed_when_the_forward_store_is_missing_or_unreadable(tmp_path):
    from app import forward_status
    st = forward_status.sport_statuses(None, now=NOW)
    assert set(st) == {"nba", "nhl", "ncaab", "nfl", "soccer"} and {v["status"] for v in st.values()} == {"failed"}
    assert all("missing" in v["reason"] for v in st.values())
    bad = tmp_path / "research" / "forward-candidates.sqlite"
    bad.parent.mkdir()
    bad.write_bytes(b"this is not a database" * 100)
    st = status_of(bad)
    assert {v["status"] for v in st.values()} == {"failed"} and all("unreadable" in v["reason"] for v in st.values())


def test_no_candidate_for_sports_with_only_a_baseline_and_the_baselines_are_labelled(tmp_path):
    p = tmp_path / "t.sqlite"
    for sport, model, market in (("ncaab", "ncaab-incumbent", "winner"), ("nfl", "nfl-incumbent", "winner"), ("soccer", "elo_sealed", "result_1x2")):
        put(p, model, "incumbent", ts(0), ts(0), None, 0.5, gid=f"{sport}1", sport=sport, market=market, basis=None) if market == "winner" else \
            ar.forward_record(p, sport=sport, protocol_id="p", model=model, role="incumbent", model_version="v", data_version="d",
                              forecasted_at=ts(0), now=ts(0), records=[{"game_id": "s1", "season": 1, "start_at": START, "forecast_cutoff": ts(0),
                                                                        "market": market, "line": None, "probs": {"a": .4, "mid": .3, "b": .3}, "outcome": None}])
    _runs(p, _ok_runs(["ncaab", "nfl", "soccer"], "no_candidate_passed"))
    st = status_of(p)
    for s in ("ncaab", "nfl", "soccer"):
        assert st[s]["status"] == "no candidate", st[s]
    assert st["ncaab"]["baseline"]["published"] is False and "re-implementation" in st["ncaab"]["baseline"]["label"]
    assert "recorder" in st["ncaab"]["baseline"]["label"]
    assert st["nfl"]["baseline"]["published"] is False and "recomputed" in st["nfl"]["baseline"]["label"]
    assert st["soccer"]["baseline"]["published"] == "1X2 only" and "stored published" in st["soccer"]["baseline"]["label"]
    assert "no 1X2 candidate" in st["soccer"]["candidate"]["label"]
    assert st["nba"]["baseline"]["published"] is True and st["nhl"]["baseline"]["published"] is True


def test_rows_recorded_before_the_repair_are_an_invalid_comparison_not_a_waiting_one(tmp_path):
    p = tmp_path / "t.sqlite"
    for i in range(3):
        put(p, "elo", "incumbent", ts(0), ts(0), None, 0.5, gid=f"g{i}", market="winner", basis=None)
        put(p, "cand", "candidate", ts(0), ts(0), None, 0.6, gid=f"g{i}", market="winner", basis=None)
    _runs(p, _ok_runs(["nba"]))
    s = status_of(p)["nba"]
    assert s["status"] == "invalid comparison" and "no information time" in s["reason"]
    assert s["pairing"]["totals"]["all"]["invalid_no_information_time"] == 3 and s["pairing"]["totals"]["all"]["valid_pairs"] == 0


def test_valid_pairs_before_the_start_is_established_are_waiting(tmp_path):
    p = tmp_path / "t.sqlite"
    _season(p)
    _runs(p, _ok_runs(["nba"]))
    s = status_of(p)["nba"]
    assert s["status"] == "waiting" and "not established" in s["reason"] and s["pairing"]["totals"]["exploratory"]["valid_pairs"] == 30


def test_valid_pairs_after_the_start_are_recording_valid_evidence_and_earlier_ones_alone_are_only_waiting(tmp_path, monkeypatch):
    p = tmp_path / "t.sqlite"
    _season(p, n=10)
    _runs(p, _ok_runs(["nba"]))
    monkeypatch.setitem(ar.FORWARD_PAIRING, "published_bar_from", ts(1000))
    s = status_of(p)["nba"]
    assert s["status"] == "recording valid evidence" and s["pairing"]["totals"]["confirmatory"]["valid_pairs"] == 10
    monkeypatch.setitem(ar.FORWARD_PAIRING, "published_bar_from", ts(100000))        # a start after every stored game
    s = status_of(p)["nba"]
    assert s["status"] == "waiting" and "before" in s["reason"]


def test_candidates_with_rows_but_no_eligible_pair_are_an_invalid_comparison(tmp_path, monkeypatch):
    p = tmp_path / "t.sqlite"
    for i in range(4):                                                  # the candidate is always 1 s later than the published row
        pair_row(p, f"g{i}", cand_info=ts(0, 1), market="winner", bar_line=None, cand_line=None)
    _runs(p, _ok_runs(["nba"]))
    monkeypatch.setitem(ar.FORWARD_PAIRING, "published_bar_from", ts(-100000))
    s = status_of(p)["nba"]
    assert s["status"] == "invalid comparison" and "timing" in s["reason"]


def test_a_reimplemented_bar_is_never_valid_evidence_even_with_candidate_rows(tmp_path, monkeypatch):
    p = tmp_path / "t.sqlite"
    for i in range(3):
        put(p, "ncaab-incumbent", "incumbent", ts(0), ts(0), None, 0.5, gid=f"g{i}", sport="ncaab", market="winner", basis=None)
        put(p, "ncaab-cand", "candidate", ts(0), ts(0), None, 0.6, gid=f"g{i}", sport="ncaab", market="winner", basis=None)
    _runs(p, _ok_runs(["ncaab"]))
    monkeypatch.setitem(ar.FORWARD_PAIRING, "published_bar_from", ts(-100000))
    s = status_of(p)["ncaab"]
    assert s["status"] == "invalid comparison" and "re-implementation" in s["reason"]


def test_a_failed_record_or_grade_job_or_a_stale_recorder_is_failed(tmp_path):
    p = tmp_path / "t.sqlite"
    _season(p, n=3)
    _runs(p, [("record", "nba", "failed", "forward_status=failed boom"), ("grade", "nba", "graded", "graded=1"),
              ("record", "nhl", "evaluated", "forward_status=candidates_recording"), ("grade", "nhl", "failed", "graded=0 nope")])
    st = status_of(p)
    assert st["nba"]["status"] == "failed" and "record" in st["nba"]["reason"] and "boom" in st["nba"]["reason"]
    assert st["nhl"]["status"] == "failed" and "grade" in st["nhl"]["reason"]
    stale = status_of(p, now=ts(30 + 60 * 5))                           # the last run was five hours ago
    assert stale["nhl"]["status"] == "failed" or "stale" in stale["nhl"]["reason"]
    assert "stale" in stale["ncaab"]["reason"] or stale["ncaab"]["status"] == "failed"


# --- 7. the API ------------------------------------------------------------------------------------------------------------------------
def test_the_api_shows_the_counts_and_a_status_with_its_reason_for_all_five_sports(tmp_path, monkeypatch):
    data = tmp_path / "data"
    p = data / "research" / ar.FORWARD_FILE
    p.parent.mkdir(parents=True)
    _season(p, n=5)
    _runs(p, _ok_runs(["nba", "nhl"]))
    exp = tmp_path / "exp"
    (exp / "v3" / "pid" / "nba").mkdir(parents=True)
    (exp / "v3" / "pid" / "nba" / "state.json").write_text(json.dumps({"sport": "nba", "protocol_id": "pid", "at": ts(0)}))
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    monkeypatch.setenv("NBA_AUTORESEARCH_DIR", str(exp))
    monkeypatch.setenv("NBA_API_MODE", "real")
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    v3 = TestClient(main.app).get("/api/research/autoresearch").json()["v3"]
    fs = v3["forward_status"]
    assert set(fs) == {"nba", "nhl", "ncaab", "nfl", "soccer"}
    assert fs["nba"]["status"] == "waiting" and fs["nba"]["reason"] and fs["ncaab"]["status"] in ("no candidate", "failed")
    assert v3["forward"]["nba"]["pairing"]["totals"]["exploratory"]["valid_pairs"] == 5
    assert v3["forward"]["nba"]["paired_graded_items"] == 5
