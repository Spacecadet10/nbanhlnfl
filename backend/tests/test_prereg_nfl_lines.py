"""NFL line-market evaluation (docs/preregistration/nfl-line-eval-2026-11-18.md): synthetic stores and units only."""
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import db, prereg_common as pc, prereg_nfl_lines as pl

RAW = "2026-11-01T00:00:00Z"
NOW = datetime(2026, 12, 10, tzinfo=timezone.utc)


# ---- fixtures -------------------------------------------------------------------------------------------------------------
def make_store(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    c = db.connect(data_dir / "nfl.sqlite", sport="nfl")
    db.migrate(c)
    db.claim_store(c, "nfl")
    for t in "12":
        c.execute("INSERT INTO teams (team_id, abbr, name, first_seen_at) VALUES (?,?,?,?)", (f"nfl:{t}", f"T{t}", f"Team {t}", RAW))
    c.execute("INSERT INTO raw_responses (raw_id, source, endpoint, request_key, content_hash, byte_size, path, importer_version, "
              "first_retrieved_at) VALUES (1,'espn_nfl','sb','k','h',1,'x','v',?)", (RAW,))
    c.execute("INSERT INTO raw_fetches (fetch_id, raw_id, retrieved_at) VALUES (1,1,?)", (RAW,))
    c.commit()
    return c


def add_game(c, gid, tip, final=None, status="scheduled", season_type="regular", forecast=None, quote=None, available=None):
    """forecast = (market, line, line_source, p_a, generated_at[, quote_id]); the game is scheduled while the row is written."""
    c.execute("INSERT INTO games (game_id, season, season_type, tipoff_at, home_team_id, away_team_id, neutral_site, status, first_seen_at, "
              "last_raw_id, updated_at) VALUES (?,2026,?,?,'nfl:1','nfl:2',0,'scheduled',?,1,?)", (gid, season_type, tip, RAW, RAW))
    qid = None
    if quote:
        market, line, p, at, label, provider = quote
        c.execute("INSERT INTO market_quotes (sport, game_id, provider, market, price_label, line, price_a, price_b, p_a_devig, p_b_devig, "
                  "retrieved_at, fetch_id, raw_id) VALUES ('nfl',?,?,?,?,?,-110,-110,?,?,?,1,1)", (gid, provider, market, label, line, p, 1 - p, at))
        qid = c.execute("SELECT MAX(quote_id) FROM market_quotes").fetchone()[0]
    for f in ([forecast] if forecast and not isinstance(forecast, list) else (forecast or [])):
        market, line, src, pa, gen = f
        c.execute("INSERT INTO game_forecasts (game_id,sport,season_type,market,order_version,status,source,fallback_rank,model_version,line,"
                  "line_source,market_quote_id,p_a,p_mid,p_b,confidence,generated_at,tipoff_at,idempotency_key) VALUES (?,'nfl',?,?,"
                  "'nfl-live-v1','forecast','primary_direct',3,'m',?,?,?,?,0.0,?,?,?,?,?)",
                  (gid, season_type, market, line, src, qid if src == "bookmaker" else None, pa, 1 - pa, max(pa, 1 - pa), gen, tip, f"{gid}-{market}"))
    if final:
        c.execute("UPDATE games SET status='final', home_score=?, away_score=?, result_available_at=?, result_availability_basis="
                  "'observed_retrieval', result_first_seen_at=? WHERE game_id=?", (final[0], final[1], available or tip, available or tip, gid))
    elif status != "scheduled":
        c.execute("UPDATE games SET status=? WHERE game_id=?", (status, gid))
    c.commit()
    return qid


def unit(market="spread", p=0.6, y=1, q=0.5, week="w1", state="decided", line_source="bookmaker", gid="g", tip="2026-11-22T18:00:00Z",
         label="current", provider="DraftKings", line=-3.5, qid=1):
    return {"game_id": gid, "market": market, "week": week, "tipoff_at": tip, "cutoff_at": "2026-11-22T16:00:00Z",
            "generated_at": "2026-11-22T16:00:00Z", "p": p, "line": line, "line_source": line_source, "quote_id": qid, "q_book": q,
            "quote_label": label, "quote_provider": provider, "state": state, "y": y if state == "decided" else None}


# ---- document, constants, weeks ---------------------------------------------------------------------------------------
def test_constants_and_document_checks(tmp_path, monkeypatch):
    pl.check_document()
    assert pl.PREREG_SHA256 == "5ea6fef126ca9d4b8555f689d255a590f08261aac758a0e11101294d419e6e6c"
    assert pl.POLICY_SHA256 == "9503bca10a1120b3fd687f62105fd84467ea7ebf33269c79f3ebf5e15841cede"
    assert pl.T0 == "2026-11-18T14:25:37Z" and pl.N_MIN == 600 and pl.DEADLINE == "2029-02-28"
    assert pl.ADDENDA_SHA256 == {"addendum-2026-11-18-nfl-evaluator-clarifications.md":
                                 "5961a03c7e0bb69bb51cccabb8e3c6e946c01da2c0a762f3e963902b4ccfcec2"}
    bad = tmp_path / "p.json"
    bad.write_bytes(b"{}")
    monkeypatch.setattr(pl, "POLICY_PATH", bad)
    with pytest.raises(pc.Refused, match="challenger"):
        pl.check_document()
    monkeypatch.undo()
    monkeypatch.setattr(pl, "REPO_ROOT", tmp_path)
    with pytest.raises(pc.Refused):
        pl.check_document()


def test_week_key_is_tuesday_anchored_eastern():
    thu = pl.week_key(2026, "2026-11-20T01:15:00Z")      # Thursday 2026-11-19 20:15 ET
    sun = pl.week_key(2026, "2026-11-22T18:00:00Z")
    mon = pl.week_key(2026, "2026-11-24T01:15:00Z")      # Monday night 2026-11-23 20:15 ET
    assert thu == sun == mon == "2026|2026-11-17"
    assert pl.week_key(2026, "2026-11-27T01:15:00Z") == "2026|2026-11-24"       # next Thursday night


# ---- collect: counted games, states, counts ---------------------------------------------------------------------------
def build_world(tmp_path):
    c = make_store(tmp_path)
    cut = lambda tip: tip                                               # noqa: E731  (forecasts at exactly the cutoff are built below)
    add_game(c, "g1", "2026-11-22T18:00:00Z", (24, 20), forecast=[("spread", -3.5, "bookmaker", 0.60, "2026-11-22T16:30:00Z")],
             quote=("spread", -3.5, 0.55, "2026-11-22T15:00:00Z", "current", "DraftKings"))
    add_game(c, "g2", "2026-11-29T18:00:00Z", (20, 24), forecast=[("spread", -2.5, "model", 0.70, "2026-11-29T16:00:00Z")])
    add_game(c, "g3", "2026-12-06T18:00:00Z", (24, 20), forecast=[("spread", -4.0, "bookmaker", 0.50, "2026-12-06T16:00:00Z")],
             quote=("spread", -4.0, 0.52, "2026-12-06T15:00:00Z", "open", "ESPN"))
    add_game(c, "g4", "2026-12-03T18:00:00Z", status="postponed", forecast=[("spread", -1.5, "model", 0.55, "2026-12-03T16:00:00Z")])
    add_game(c, "g5", "2026-12-09T18:00:00Z", forecast=[("spread", -1.5, "model", 0.55, "2026-12-09T16:00:00Z")])
    add_game(c, "g6", "2026-11-18T15:00:00Z", (24, 20), forecast=[("spread", -3.5, "model", 0.60, "2026-11-18T13:00:00Z")])      # cutoff before T0
    add_game(c, "g7", "2026-11-25T18:00:00Z", (24, 20))                                                                        # no forecast row
    add_game(c, "g8", "2026-11-26T18:00:00Z", (24, 20), season_type="preseason", forecast=[("spread", -3.5, "model", 0.60, "2026-11-26T16:00:00Z")])
    add_game(c, "g9", "2026-11-18T16:25:37Z", (24, 20), forecast=[("spread", -3.5, "model", 0.60, "2026-11-18T14:25:37Z")])      # cutoff == T0: not after it
    add_game(c, "g10", "2026-11-18T16:25:38Z", (24, 20), forecast=[("spread", -3.5, "model", 0.60, "2026-11-18T14:25:39Z")])     # cutoff 1 s after T0, generated 1 s late
    return c


def test_collect_counts_hand_values(tmp_path):
    c = build_world(tmp_path)
    col = pl.collect(c, now=NOW)
    n = col.counts["spread"]
    assert col.games_counted == 7                       # g1 g2 g3 g4 g5 g7 g10 (g6, g9 not after T0; g8 preseason)
    assert n["forecasts"] == 6 and n["no_canonical_forecast"] == 1
    assert n["decided"] == 3 and n["push"] == 1 and n["void"] == 1 and n["pending"] == 1 and n["past7"] == 0
    assert n["bookmaker_line"] == 2 and n["model_line"] == 4 and n["decided_bookmaker_line"] == 1
    assert n["open_label_t120"] == 1 and n["open_label_providers"] == {"ESPN": 1}
    assert n["generated_after_cutoff"] == 2             # g1 (30 min late) and g10 (1 s late)
    u = {x["game_id"]: x for x in col.units if x["market"] == "spread"}
    assert u["g1"]["p"] == pytest.approx(0.60) and u["g1"]["y"] == 1 and u["g1"]["q_book"] == pytest.approx(0.55) and u["g1"]["state"] == "decided"
    assert u["g2"]["y"] == 0 and u["g2"]["line_source"] == "model" and u["g2"]["q_book"] is None
    assert u["g3"]["state"] == "push" and u["g3"]["quote_label"] == "open" and u["g3"]["quote_provider"] == "ESPN"
    assert u["g4"]["state"] == "void" and u["g5"]["state"] == "pending"
    assert u["g1"]["week"] == "2026|2026-11-17"


def test_results_not_yet_available_are_not_final(tmp_path):
    c = make_store(tmp_path)
    add_game(c, "g1", "2026-11-22T18:00:00Z", (24, 20), forecast=[("spread", -3.5, "model", 0.60, "2026-11-22T16:00:00Z")], available="2026-11-23T00:00:00Z")
    early = pl.collect(c, now=datetime(2026, 11, 22, 23, 0, tzinfo=timezone.utc))
    assert early.counts["spread"]["pending"] == 1 and early.counts["spread"]["decided"] == 0
    late = pl.collect(c, now=datetime(2026, 11, 23, 1, 0, tzinfo=timezone.utc))
    assert late.counts["spread"]["decided"] == 1


def test_past_seven_days_is_excluded_and_counted(tmp_path):
    c = make_store(tmp_path)
    add_game(c, "g1", "2026-11-22T18:00:00Z", forecast=[("spread", -3.5, "model", 0.60, "2026-11-22T16:00:00Z")])
    col = pl.collect(c, now=datetime(2026, 11, 29, 18, 0, 1, tzinfo=timezone.utc))
    assert col.counts["spread"]["past7"] == 1 and col.counts["spread"]["pending"] == 0


def test_winner_book_comes_from_the_t120_moneyline(tmp_path):
    c = make_store(tmp_path)
    add_game(c, "g1", "2026-11-22T18:00:00Z", (24, 20), forecast=[("winner", None, "none", 0.62, "2026-11-22T16:00:00Z")],
             quote=("winner", None, 0.58, "2026-11-22T15:00:00Z", "current", "DraftKings"))
    add_game(c, "g2", "2026-11-29T18:00:00Z", (24, 20), forecast=[("winner", None, "none", 0.62, "2026-11-29T16:00:00Z")],
             quote=("winner", None, 0.58, "2026-11-29T13:59:59Z", "current", "DraftKings"))       # older than cutoff - 2 h
    col = pl.collect(c, now=NOW)
    w = {x["game_id"]: x for x in col.units if x["market"] == "winner"}
    assert w["g1"]["q_book"] == pytest.approx(0.58) and w["g1"]["line_source"] == "bookmaker"
    assert w["g2"]["q_book"] is None and w["g2"]["line_source"] == "model"
    assert col.counts["winner"]["decided_bookmaker_line"] == 1


def test_team_totals_have_no_book(tmp_path):
    c = make_store(tmp_path)
    add_game(c, "g1", "2026-11-22T18:00:00Z", (24, 20), forecast=[("team_total_home", 23.5, "model", 0.55, "2026-11-22T16:00:00Z")])
    col = pl.collect(c, now=NOW)
    u = col.units[0]
    assert u["market"] == "team_total_home" and u["q_book"] is None and u["y"] == 1 and col.counts["team_total_home"]["decided_bookmaker_line"] == 0


# ---- metrics ----------------------------------------------------------------------------------------------------------
def test_metrics_hand_values():
    us = [unit(p=0.8, y=1, q=0.5), unit(p=0.6, y=0, q=0.5)]
    m = pl.metrics(us)
    assert m["log_loss_app"] == pytest.approx((-math.log(0.8) - math.log(0.4)) / 2) and m["brier_app"] == pytest.approx(0.2)
    assert m["log_loss_book"] == pytest.approx(math.log(2)) and m["brier_book"] == pytest.approx(0.25)
    assert m["delta_ll"] == pytest.approx((-math.log(0.8) - math.log(0.4)) / 2 - math.log(2))
    assert m["hit_rate"] == 0.5 and m["mean_q_side"] == 0.5 and m["excess"] == pytest.approx(0.0)
    lo, hi = m["hit_rate_wilson95"]
    assert lo < 0.5 < hi
    assert [b["n"] for b in m["reliability"]] == [0, 0, 0, 1, 1]       # p in [0.6,0.8) and [0.8,1.0]


def test_side_b_when_p_below_half():
    m = pl.metrics([unit(p=0.3, y=0, q=0.4)])           # app side b: won; book's side-b probability 0.6
    assert m["hit_rate"] == 1.0 and m["mean_q_side"] == pytest.approx(0.6) and m["excess"] == pytest.approx(0.4)


def test_calibration_is_exact_for_calibrated_groups():
    us = [unit(p=0.2, y=1 if i < 2 else 0, q=0.5) for i in range(10)] + [unit(p=0.8, y=1 if i < 8 else 0, q=0.5) for i in range(10)]
    cal = pl.metrics(us)["calibration"]
    assert cal["intercept"] == pytest.approx(0.0, abs=1e-6) and cal["slope"] == pytest.approx(1.0, abs=1e-6)
    assert pl.metrics([unit(p=0.6, y=1), unit(p=0.6, y=0)])["calibration"] == {"intercept": None, "slope": None}


def test_outcome_only_metrics_for_team_totals():
    m = pl.metrics([unit("team_total_home", p=0.5, y=1, q=None), unit("team_total_home", p=0.5, y=0, q=None)], book=False)
    assert m["log_loss_app"] == pytest.approx(math.log(2)) and "delta_ll" not in m and m["hit_rate"] == 0.5


# ---- gate and look ------------------------------------------------------------------------------------------------------
def calibrated_units(n=600, weeks=30, p=0.6, q=0.5, start=0, market="spread"):
    per = n // weeks
    out = []
    for w in range(weeks):
        for i in range(per):
            out.append(unit(market, p=p, y=1 if i < per * 6 // 10 else 0, q=q, week=f"w{w:02d}", gid=f"g{start + w * per + i:04d}",
                            tip=f"2027-01-{1 + (w % 28):02d}T18:00:00Z"))
    return out


def test_below_600_only_counts_and_no_metric_values():
    us = calibrated_units(570, weeks=30)
    r = pl.look_market(us, "spread", NOW)
    assert r["status"] == "insufficient evidence (n of 600)" and r["n_decided_bookmaker_line"] == 570
    assert set(r) <= {"status", "n_decided_bookmaker_line", "n_required", "market", "coverage"}


def test_look_at_exactly_600_hand_values():
    r = pl.look_market(calibrated_units(), "spread", NOW)
    assert r["status"] == "look" and r["n"] == 600
    dll = (0.6 * -math.log(0.6) + 0.4 * -math.log(0.4)) - math.log(2)
    assert r["metrics"]["delta_ll"] == pytest.approx(dll) and r["metrics"]["hit_rate"] == pytest.approx(0.6)
    assert r["test"]["theta"] == pytest.approx(dll) and r["test"]["p"] == pytest.approx(1 / 10001)
    assert r["test"]["reject"] is True and r["test"]["threshold"] == 0.025 and r["verdict"] == "app beats the T-120 book on spread"


def test_look_uses_first_600_and_waits_for_earlier_pending_games():
    us = calibrated_units(630, weeks=30, start=0)
    first = pl.look_market(us, "spread", NOW)
    assert first["n"] == 600
    waiting = calibrated_units(600, weeks=30)
    waiting[5] = unit(state="pending", gid=waiting[5]["game_id"], week=waiting[5]["week"], tip=waiting[5]["tipoff_at"])
    waiting.append(unit(gid="gzzzz", week="w99", tip="2027-02-01T18:00:00Z"))
    r = pl.look_market(waiting, "spread", NOW)
    assert r["status"].startswith("waiting")


def test_book_better_and_not_shown():
    worse = [unit(p=0.6, y=1 if (i // 30) % 5 < 2 else 0, q=0.4, week=f"w{i % 30:02d}", gid=f"g{i:04d}") for i in range(600)]
    r = pl.look_market(worse, "spread", NOW)
    assert r["test"]["theta"] > 0 and r["test"]["reject"] is False and r["verdict"] == "book beats app on spread"
    flat = [unit(p=0.5, y=1 if i % 2 else 0, q=0.5, week=f"w{i % 30:02d}", gid=f"g{i:04d}") for i in range(600)]
    assert pl.look_market(flat, "spread", NOW)["verdict"] == "not shown"


def test_deadline_closes_a_market_below_600():
    us = calibrated_units(300, weeks=30)
    r = pl.look_market(us, "spread", datetime(2029, 3, 1, tzinfo=timezone.utc))
    assert r["status"] == "insufficient evidence (closed at deadline 2029-02-28)" and "metrics" not in r


def test_winner_has_no_hypothesis_and_team_totals_gate_on_decided_forecasts():
    us = calibrated_units(600, market="winner", weeks=30)
    r = pl.look_market(us, "winner", NOW)
    assert r["status"] == "look" and "test" not in r and r["metrics"]["delta_ll"] < 0
    tt = [unit("team_total_home", p=0.5, y=i % 2, q=None, line_source="model", week=f"w{i % 30:02d}", gid=f"t{i:04d}") for i in range(599)]
    assert pl.look_market(tt, "team_total_home", NOW)["status"] == "insufficient evidence (n of 600)"
    tt.append(unit("team_total_home", p=0.5, y=1, q=None, line_source="model", week="w00", gid="t9999"))
    r = pl.look_market(tt, "team_total_home", NOW)
    assert r["status"] == "look" and "delta_ll" not in r["metrics"] and "test" not in r


def test_open_label_sensitivity_drops_open_quotes():
    us = calibrated_units(600, weeks=30)
    for u in us[:60]:
        u["quote_label"] = "open"
    r = pl.look_market(us, "spread", NOW)
    assert r["sensitivity_without_open_label"]["n"] == 540


# ---- CLV (secondary) ------------------------------------------------------------------------------------------------------
def test_close_same_line_moved_line_stale_and_none(tmp_path):
    c = make_store(tmp_path)
    tip = "2026-11-22T18:00:00Z"
    for g in ("g1", "g2", "g3", "g4"):
        add_game(c, g, tip if g == "g1" else tip.replace("22", "2" + str("1234".index(g[1]) + 3)))
    def q(g, line, p, at, label="current", provider="DraftKings"):
        c.execute("INSERT INTO market_quotes (sport, game_id, provider, market, price_label, line, price_a, price_b, p_a_devig, p_b_devig, retrieved_at, "
                  "fetch_id, raw_id) VALUES ('nfl',?,?,'spread',?,?,-110,-110,?,?,?,1,1)", (g, provider, label, line, p, 1 - p, at))
    q("g1", -3.5, 0.60, "2026-11-22T17:30:00Z"); q("g1", -3.5, 0.50, "2026-11-22T17:59:00Z", provider="Other"); q("g1", -9.0, 0.5, "2026-11-22T17:59:30Z", label="open")
    c.commit()
    u = unit(gid="g1", p=0.7, q=0.55, tip=tip, line=-3.5)
    r = pl.close_for(c, u)
    assert r["status"] == "ok" and r["clv"] == pytest.approx(0.60 - 0.55) and r["points_clv"] is None
    u["p"] = 0.3                                           # the app's side is b: q_close(b) - q(b) = 0.40 - 0.45
    assert pl.close_for(c, u)["clv"] == pytest.approx((1 - 0.60) - (1 - 0.55))
    u2 = unit(gid="g1", p=0.7, q=0.55, tip=tip, line=-2.5)    # line moved to -3.5 vs forecast -2.5: side a, points CLV = L_f - L_c = 1.0
    r2 = pl.close_for(c, u2)
    assert r2["clv"] is None and r2["points_clv"] == pytest.approx(1.0) and r2["status"] == "ok"
    u3 = unit(gid="g1", p=0.3, q=0.55, tip=tip, line=-2.5)
    assert pl.close_for(c, u3)["points_clv"] == pytest.approx(-1.0)
    u4 = unit("total", gid="g1", p=0.7, q=0.5, tip=tip, line=44.5)
    assert pl.close_for(c, u4)["status"] == "no_close"
    assert pl.points_clv("total", "a", 45.5, 44.5) == -1.0 and pl.points_clv("total", "b", 45.5, 44.5) == 1.0
    assert pl.points_clv("spread", "b", -3.5, -2.5) == 1.0


def test_close_stale_when_older_than_an_hour(tmp_path):
    c = make_store(tmp_path)
    tip = "2026-11-22T18:00:00Z"
    add_game(c, "g1", tip)
    c.execute("INSERT INTO market_quotes (sport, game_id, provider, market, price_label, line, price_a, price_b, p_a_devig, p_b_devig, retrieved_at, fetch_id, raw_id) "
              "VALUES ('nfl','g1','DraftKings','spread','current',-3.5,-110,-110,0.6,0.4,'2026-11-22T16:59:59Z',1,1)")
    c.commit()
    assert pl.close_for(c, unit(gid="g1", tip=tip))["status"] == "stale"


# ---- challengers --------------------------------------------------------------------------------------------------------
def test_policy_hypotheses_are_the_eight_registered():
    pol = pl.load_policy()
    assert pol["sha256"] == pl.POLICY_SHA256
    assert sorted(pol["hypotheses"]) == sorted([("nfl_elo_k10", "winner"), ("nfl_elo_k30", "winner"), ("nfl_elo_ha25", "winner"),
                                                 ("nfl_elo_ha75", "winner"), ("nfl_points_eta02", "spread"), ("nfl_points_eta02", "total"),
                                                 ("nfl_points_eta10", "spread"), ("nfl_points_eta10", "total")])
    assert len(pol["hypotheses"]) == 8 and pl.CHALLENGER_ALPHA == pytest.approx(0.05 / 8)


def challenger_db(path, rows):
    from app import learning_challengers as lc
    conn = lc.open_store(path)
    for r in rows:
        conn.execute("INSERT INTO challenger_forecasts (policy_id, policy_sha, spec_name, spec_json, spec_hash, definition, game_id, market, line, p_a, p_mid, "
                     "p_b, cutoff_at, tipoff_at, generated_at, dataset_version, model_version) VALUES ('nfl-challengers-v1',?,?,'{}','h','d',?,?,?,?,0.0,?,?,?,?,'dv','mv')",
                     (r["sha"], r["spec"], r["game"], r["market"], r["line"], r["p"], 1 - r["p"], r["cutoff"], r["tip"], r["gen"]))
    conn.commit()
    return conn


def test_challenger_pairing_start_and_counts(tmp_path):
    inc = [unit("winner", p=0.6, y=1, q=0.5, gid="a1", tip="2026-11-22T18:00:00Z", line=None),
           unit("winner", p=0.6, y=0, q=0.5, gid="a2", tip="2026-11-29T18:00:00Z", line=None),
           unit("winner", p=0.6, y=1, q=0.5, gid="a3", tip="2026-12-06T18:00:00Z", line=None),
           unit("winner", p=0.6, state="push", gid="a4", tip="2026-12-13T18:00:00Z", line=None)]
    sha = pl.POLICY_SHA256
    row = lambda g, tip, gen, p: dict(sha=sha, spec="nfl_elo_k10", game=g, market="winner", line=None, p=p, cutoff=tip, tip=tip, gen=gen)   # noqa: E731
    rows = [row("a2", "2026-11-29T18:00:00Z", "2026-11-29T16:00:00Z", 0.7), row("a3", "2026-12-06T18:00:00Z", "2026-12-06T16:00:00Z", 0.4),
            row("a4", "2026-12-13T18:00:00Z", "2026-12-13T16:00:00Z", 0.5)]
    rows[0]["cutoff"], rows[1]["cutoff"], rows[2]["cutoff"] = "2026-11-29T16:00:00Z", "2026-12-06T16:00:00Z", "2026-12-13T16:00:00Z"
    rows.append(dict(row("a1", "2026-11-22T18:00:00Z", "2026-11-22T16:00:00Z", 0.9), sha="f" * 64))      # another policy file: ignored
    conn = challenger_db(tmp_path / "challengers.sqlite", rows)
    pol = pl.load_policy()
    cu = pl.challenger_units(conn, inc, pol, "nfl_elo_k10", "winner")
    # start = max(T0, first generated_at under this policy) = 2026-11-29T16:00:00Z; a1's cutoff precedes it: not counted
    assert cu["start"] == "2026-11-29T16:00:00Z"
    assert [x["game_id"] for x in cu["pairs"]] == ["a2", "a3"]          # a4 is a push: counted, not paired
    assert cu["counts"]["incumbent_decided_after_start"] == 2 and cu["counts"]["missing_rows"] == 0 and cu["counts"]["rows"] == 3
    assert cu["pairs"][0]["p_ch"] == pytest.approx(0.7) and cu["pairs"][0]["p"] == pytest.approx(0.6)


def test_challenger_missing_row_and_late_row_are_counted_not_imputed(tmp_path):
    inc = [unit("winner", p=0.6, y=1, q=0.5, gid="a1", tip="2026-11-29T18:00:00Z", line=None),
           unit("winner", p=0.6, y=1, q=0.5, gid="a2", tip="2026-12-06T18:00:00Z", line=None)]
    r = dict(sha=pl.POLICY_SHA256, spec="nfl_elo_k10", game="a1", market="winner", line=None, p=0.7, cutoff="2026-11-29T16:00:00Z",
             tip="2026-11-29T18:00:00Z", gen="2026-11-29T16:00:00Z")
    conn = challenger_db(tmp_path / "c.sqlite", [r])
    cu = pl.challenger_units(conn, inc, pl.load_policy(), "nfl_elo_k10", "winner")
    assert [x["game_id"] for x in cu["pairs"]] == ["a1"] and cu["counts"]["missing_rows"] == 1


def test_challenger_look_below_gate_counts_only_and_at_600_bonferroni():
    pairs = [{"game_id": f"g{i}", "week": f"w{i % 30:02d}", "p": 0.7, "p_ch": 0.6, "y": 1 if (i // 30) % 10 < 6 else 0, "q_book": None} for i in range(599)]
    r = pl.look_challenger(pairs, "nfl_elo_k10", "winner", NOW)
    assert r["status"] == "insufficient evidence (n of 600)" and "metrics" not in r
    pairs.append({"game_id": "gx", "week": "w00", "p": 0.7, "p_ch": 0.6, "y": 1, "q_book": None})
    r = pl.look_challenger(pairs, "nfl_elo_k10", "winner", NOW)
    assert r["status"] == "look" and r["test"]["threshold"] == pytest.approx(0.00625)
    assert r["test"]["theta"] < 0 and r["test"]["reject"] is True                 # the challenger's p (0.6) is calibrated to the 60% base rate


# ---- run -------------------------------------------------------------------------------------------------------------------
def test_run_refuses_live_folder_and_writes_outside(tmp_path):
    with pytest.raises(pc.Refused):
        pl.run(pc.LIVE_DATA, tmp_path.parent / "o", now=NOW)
    data = tmp_path / "copy"
    c = build_world(data)
    c.close()
    out = tmp_path / "report"
    rep = pl.run(data, out, now=NOW)
    assert (out / "nfl-line-eval-report.json").is_file() and rep["markets"]["spread"]["status"] == "insufficient evidence (n of 600)"
    blob = json.dumps(rep["markets"]["spread"])
    assert "log_loss" not in blob and "hit_rate" not in blob and "delta_ll" not in blob
    assert rep["counts"]["spread"]["forecasts"] == 6
    with pytest.raises(pc.Refused):
        pl.run(data, data / "inside", now=NOW)


def test_single_look_is_keyed_to_document_and_market_not_to_the_report_folder(tmp_path, monkeypatch):
    data = tmp_path / "copy"
    make_store(data).close()
    looks = tmp_path / "looks"
    monkeypatch.setattr(pl, "look_market", lambda units, market, now, store=None: {"market": market, "status": "look", "n": 600, "stamp": str(now)})
    first = pl.run(data, tmp_path / "out1", now=NOW, looks_dir=looks)
    monkeypatch.setattr(pl, "look_market", lambda *a, **k: pytest.fail("a second look must not be computed"))
    again = pl.run(data, tmp_path / "out2", now=datetime(2026, 12, 11, tzinfo=timezone.utc), looks_dir=looks)     # another --out folder
    assert again["markets"] == first["markets"] and first["markets"]["spread"]["status"] == "look"


def test_games_after_the_deadline_never_count_toward_600():
    us = calibrated_units(600, weeks=30)
    for u in us[-5:]:
        u["tipoff_at"] = "2029-03-01T18:00:00Z"
    r = pl.look_market(us, "spread", NOW)
    assert r["status"] == "insufficient evidence (n of 600)" and r["n_decided_bookmaker_line"] == 595
    edge = calibrated_units(600, weeks=30)
    edge[-1]["tipoff_at"] = "2029-03-01T03:00:00Z"            # 22:00 Eastern on 2029-02-28: still on the deadline day
    assert pl.look_market(edge, "spread", NOW)["status"] == "look"


def test_challenger_pairs_after_the_deadline_do_not_count(tmp_path):
    inc = [unit("winner", p=0.6, y=1, q=0.5, gid="a1", tip="2029-03-02T18:00:00Z", line=None)]
    r = dict(sha=pl.POLICY_SHA256, spec="nfl_elo_k10", game="a1", market="winner", line=None, p=0.7, cutoff="2029-03-02T16:00:00Z",
             tip="2029-03-02T18:00:00Z", gen="2029-03-02T16:00:00Z")
    cu = pl.challenger_units(challenger_db(tmp_path / "c.sqlite", [r]), inc, pl.load_policy(), "nfl_elo_k10", "winner")
    assert cu["pairs"] == []


def test_canonical_is_read_from_the_activation_time(tmp_path):
    c = make_store(tmp_path)
    add_game(c, "gx", "2026-12-20T18:00:00Z")                                                       # scheduled, no forecast row, in the future
    add_game(c, "gy", "2026-12-28T18:00:00Z", forecast=[("spread", -3.5, "model", 0.6, "2026-12-28T16:00:00Z")])      # first row ever
    assert pl.collect(c, now=NOW).counts["spread"]["upcoming"] == 1                                  # only gy: recording_began falls back to MIN(generated_at) = 2026-12-28T16:00
    col = pl.collect(c, now=NOW, began="2026-11-20T00:00:00Z")
    assert col.counts["spread"]["upcoming"] == 2                                                      # gx now counts as upcoming, not "before recording began"


def test_activation_time_is_read_from_the_copy(tmp_path):
    assert pl.activated_at(tmp_path) is None
    f = tmp_path / "research" / "nfl_live"
    f.mkdir(parents=True)
    (f / "activation.json").write_text(json.dumps({"activated_at": "2026-11-20T00:00:00Z"}))
    assert pl.activated_at(tmp_path) == "2026-11-20T00:00:00Z"


def test_below_600_reports_allowed_coverage_counts_only(tmp_path):
    c = build_world(tmp_path)
    add_game(c, "gs", "2026-12-08T18:00:00Z", (24, 20), forecast=[("spread", -3.5, "bookmaker", 0.60, "2026-12-08T16:00:00Z")],
             quote=("spread", -3.5, 0.55, "2026-12-08T15:00:00Z", "current", "DraftKings"))
    c.execute("UPDATE games SET tipoff_at = '2026-12-08T19:00:00Z' WHERE game_id = 'gs'")             # kick-off moved after the forecast
    c.commit()
    col = pl.collect(c, now=NOW)
    assert col.counts["spread"]["start_changed"] == 1
    r = pl.look_market(col.units, "spread", NOW, c)
    cov = r["coverage"]
    assert cov["forecasts"] == 7 and cov["bookmaker_line"] == 3 and cov["model_line"] == 4
    assert cov["t120_coverage"] == pytest.approx(3 / 7) and cov["generated_after_cutoff"] == 2 and cov["start_changed"] == 1
    assert set(cov["close"]) == {"ok", "stale", "no_close", "of"} and cov["close"]["of"] == 3
    blob = json.dumps(r)
    for banned in ("log_loss", "hit_rate", "delta_ll", "clv", "excess", "calibration"):
        assert banned not in blob


def test_team_total_challenger_delta_is_descriptive_at_600_paired():
    pairs = [{"game_id": f"t{i}", "week": f"w{i % 30:02d}", "p": 0.7, "p_ch": 0.6, "y": 1 if (i // 30) % 10 < 6 else 0, "q_book": None} for i in range(599)]
    r = pl.look_team_total_challenger(pairs, "nfl_points_eta02", "team_total_home")
    assert r["status"] == "insufficient evidence (n of 600)" and "delta_c" not in r and r["n_paired_decided"] == 599
    pairs.append({"game_id": "t599", "week": "w00", "p": 0.7, "p_ch": 0.6, "y": 1, "q_book": None})
    r = pl.look_team_total_challenger(pairs, "nfl_points_eta02", "team_total_home")
    ll = lambda p, y: -(y * math.log(p) + (1 - y) * math.log(1 - p))      # noqa: E731
    want = sum(ll(0.6, x["y"]) - ll(0.7, x["y"]) for x in pairs) / 600
    assert r["status"] == "descriptive" and r["delta_c"] == pytest.approx(want) and "test" not in r and "p" not in r


def test_run_reports_team_total_challengers_only_for_points_challengers(tmp_path):
    from app import learning_challengers as lc
    data = tmp_path / "copy"
    make_store(data).close()
    (data / "research").mkdir()
    lc.open_store(data / "research" / "challengers.sqlite").close()
    rep = pl.run(data, tmp_path / "out", now=NOW, looks_dir=tmp_path / "looks")
    assert set(rep["challenger_team_totals"]) == {f"{n}:{m}" for n in ("nfl_points_eta02", "nfl_points_eta10") for m in ("team_total_home", "team_total_away")}
    assert all(v["status"] == "insufficient evidence (n of 600)" for v in rep["challenger_team_totals"].values())
