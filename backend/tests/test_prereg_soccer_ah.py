"""Research-only Asian handicap evaluation (docs/preregistration/soccer-asian-handicap-research-2026-11-18.md): synthetic only."""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import db, prereg_common as pc, prereg_soccer_ah as ps, soccer_markets_live


@pytest.fixture(autouse=True)
def _looks_dir_in_tmp(tmp_path, monkeypatch):
    """No test may write to the real looks folder (~/nba-predictor-research-data/prereg-looks)."""
    monkeypatch.setattr(pc, "LOOKS_DIR", tmp_path / "default-looks")

RAW = "2026-11-01T00:00:00Z"
NOW = datetime(2027, 1, 20, tzinfo=timezone.utc)
# joint[h][a]: P(home win) .45, draw .25, away win .30 (away by 1: .20, away by 2: .10)
JOINT = [[0.10, 0.15, 0.10], [0.20, 0.10, 0.05], [0.15, 0.10, 0.05]]


# ---- document ---------------------------------------------------------------------------------------------------------
def test_constants(tmp_path, monkeypatch):
    ps.check_document()
    assert ps.PREREG_SHA256 == "a3e1fc3b60ffb9316432213fc66567bcc8b269548576ce299e88e06ec031b2b4"
    assert ps.CANDIDATE_SHA256 == soccer_markets_live.CANDIDATES["goals"] == "cfe78bf98a8e48215474de26d020fc5a0fdb45830957b38e24e47db88efbd432"
    assert ps.T0 == "2026-11-18T14:25:37Z" and ps.STOP_N == 800 and ps.STOP_DATE == "2027-05-31"
    assert ps.ADDENDA_SHA256 == {"addendum-2026-11-18-soccer-evaluator-clarifications.md":
                                 "a85ad1313f59a9578e0043484ca54ef0ea82c1cc47c63b44786a777e2ffa8a53"}
    monkeypatch.setattr(ps, "REPO_ROOT", tmp_path)
    with pytest.raises(pc.Refused):
        ps.check_document()


# ---- pricing ------------------------------------------------------------------------------------------------------------
def test_handicap_probabilities_hand_values():
    P = lambda line: ps.handicap_probs(JOINT, line)                                   # noqa: E731
    assert P(-0.5)["win"] == pytest.approx(0.45) and P(-0.5)["loss"] == pytest.approx(0.55)
    assert ps.app_share(P(-0.5)) == pytest.approx(0.45)                              # L = -0.5: P(home win)
    p0 = P(0.0)
    assert (p0["win"], p0["push"], p0["loss"]) == (pytest.approx(0.45), pytest.approx(0.25), pytest.approx(0.30))
    assert ps.app_share(p0) == pytest.approx(0.45 / 0.75)                            # L = 0: win / (win + away win)
    q = P(-0.25)                                                                     # draw loses half
    assert q["half_loss"] == pytest.approx(0.25) and ps.app_share(q) == pytest.approx(0.45 / 0.875)
    r = P(0.75)                                                                      # +0.5 and +1.0: away by 1 loses half, by 2 loses
    assert (r["win"], r["half_loss"], r["loss"]) == (pytest.approx(0.70), pytest.approx(0.20), pytest.approx(0.10))
    assert ps.app_share(r) == pytest.approx(0.70 / 0.90)
    q75 = P(-0.75)                                                                   # -0.5 and -1.0: win by 1 wins half, by 2+ wins
    assert (q75["win"], q75["half_win"], q75["loss"]) == (pytest.approx(0.15), pytest.approx(0.30), pytest.approx(0.55))
    for line in (-1.5, -0.5, 0.0, 0.25, 0.75, 1.5):                                   # home and away shares sum to 1 at paired lines
        assert ps.app_share(P(line)) + ps.app_share(ps.handicap_probs(JOINT, -line, side="away")) == pytest.approx(1.0)


def test_quarter_line_split_results():
    from app import soccer_markets as sm
    assert [sm.grade_handicap(h, a, -0.75, "home") for h, a in ((2, 0), (1, 0), (1, 1))] == ["win", "half_win", "loss"]
    assert [sm.grade_handicap(h, a, -0.25, "home") for h, a in ((1, 0), (1, 1), (0, 1))] == ["win", "half_loss", "loss"]
    assert [sm.grade_handicap(h, a, 0.0, "home") for h, a in ((1, 0), (1, 1), (0, 1))] == ["win", "push", "loss"]


def test_scoring_weights():
    assert [ps.score("win"), ps.score("half_win"), ps.score("push"), ps.score("half_loss"), ps.score("loss")] == \
        [(1.0, 1), (0.5, 1), (0.0, None), (0.5, 0), (1.0, 0)]


def test_line_type_and_bins():
    assert [ps.line_type(x) for x in (-0.5, 1.5, 0.0, -1.0, 0.25, -0.75, 2.25)] == ["half", "half", "whole", "whole", "quarter", "quarter", "quarter"]
    assert [ps.line_bin(x) for x in (-0.5, 0.5, 1.0, 1.25, 1.5, 2.0, 2.5, -3.5, 0.0, 0.25)] == ["0.5", "0.5", "0.5", "0.5", "1.5", "1.5", ">=2.5", ">=2.5", "0.5", "0.5"]


def test_power_devig():
    assert ps.power_devig(-110, -110) == (pytest.approx(0.5), pytest.approx(0.5))
    a, b = ps.power_devig(-200, 150)
    assert a + b == pytest.approx(1.0, abs=1e-9) and a > 0.625                          # favourite gets more than the proportional 0.625


# ---- metrics ------------------------------------------------------------------------------------------------------------
def u(p=0.6, q=0.5, w=1.0, y=1, comp="eng.1", day="2027-01-01", line=-0.5, mid="m"):
    return {"match_id": mid, "competition": comp, "cluster": f"{comp}|{day}", "kickoff_at": f"{day}T15:00:00Z", "p_home": p, "q_home": q,
            "w": w, "y": y, "line_home": line, "line_type": ps.line_type(line), "state": "decided", "close_status": "no_close", "clv": None}


def test_weighted_metrics_hand_values():
    m = ps.metrics([u(p=0.8, q=0.5, w=1.0, y=1), u(p=0.6, q=0.5, w=0.5, y=0)])
    assert m["log_loss_app"] == pytest.approx((-math.log(0.8) + 0.5 * -math.log(0.4)) / 1.5)
    assert m["log_loss_book"] == pytest.approx(math.log(2)) and m["delta_ll"] == pytest.approx(m["log_loss_app"] - math.log(2))
    assert m["brier_app"] == pytest.approx((0.04 + 0.5 * 0.36) / 1.5) and m["brier_book"] == pytest.approx(0.25)
    assert m["weight"] == 1.5 and m["n"] == 2


# ---- store ------------------------------------------------------------------------------------------------------------------
def make_store(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    c = db.connect(data_dir / "soccer.sqlite", sport="soccer")
    db.migrate(c)
    db.claim_store(c, "soccer")
    c.execute("DROP TRIGGER soccer_market_forecasts_generated_now")           # synthetic rows are backdated by design
    for t in "12":
        c.execute("INSERT INTO teams (team_id, abbr, name, first_seen_at) VALUES (?,?,?,?)", (f"soccer:{t}", f"T{t}", f"Team {t}", RAW))
    for comp in ("eng.1", "uefa.champions", "esp.1"):
        c.execute("INSERT OR IGNORE INTO soccer_competitions (competition_id, label) VALUES (?, ?)", (comp, comp))
    c.execute("INSERT INTO raw_responses (raw_id, source, endpoint, request_key, content_hash, byte_size, path, importer_version, first_retrieved_at) "
              "VALUES (1,'espn_soccer','sb','k','h',1,'x','v',?)", (RAW,))
    c.commit()
    return c


def add_match(c, n, kickoff, comp="eng.1", stage="regular-season", score=None, status="scheduled", decided="regulation", forecasts=((1, None),),
              available=None, joint=JOINT):
    """forecasts: (version, candidate sha or None for the pinned one, generated_at optional)."""
    mid = f"soccer:{comp}:{n}"
    c.execute("INSERT INTO soccer_matches (match_id, competition_id, event_id, season, season_type_id, stage, kickoff_at, home_team_id, away_team_id, "
              "status, source_status, first_seen_at, last_raw_id, updated_at) VALUES (?,?,?,2026,1,?,?,'soccer:1','soccer:2','scheduled','STATUS_SCHEDULED',?,1,?)",
              (mid, comp, str(n), stage, kickoff, RAW, RAW))
    day = kickoff[:10]
    for f in forecasts:
        version, sha = f[0], f[1] or ps.CANDIDATE_SHA256
        gen = f[2] if len(f) > 2 else f"{day}T05:00:00Z"
        c.execute("INSERT INTO soccer_market_forecasts (match_id, competition_id, family, version, candidate_sha256, model, idempotency_key, inputs_digest, "
                  "inputs_through, source_retrieved_at, mean_home, mean_away, max_count, joint_json, cutoff_at, generated_at) VALUES "
                  "(?,?,'goals',?,?,'poisson',?,'d',NULL,?,1.5,1.2,5,?,?,?)",
                  (mid, comp, version, sha, f"{mid}-{version}-{sha[:4]}", f"{day}T04:00:00Z", json.dumps(joint), f"{day}T06:00:00Z", gen))
    if score:
        c.execute("UPDATE soccer_matches SET status='final', source_status='STATUS_FULL_TIME', decided_in=?, home_score=?, away_score=?, "
                  "result_available_at=?, result_availability_basis='observed_retrieval', result_first_seen_at=? WHERE match_id=?",
                  (decided, score[0], score[1], available or f"{day}T18:00:00Z", available or f"{day}T18:00:00Z", mid))
    elif status != "scheduled":
        c.execute("UPDATE soccer_matches SET status=? WHERE match_id=?", (status, mid))
    c.commit()
    return mid


def quote(c, mid, side, line, american, at, kind="current", offered=1, provider="DraftKings", dec=None):
    dec = dec or (None if american is None else 1 + (american / 100 if american > 0 else 100 / -american))
    c.execute("INSERT INTO soccer_odds_quotes (match_id, provider, market, side, price_kind, offered, line, american, decimal_odds, retrieved_at, raw_id, "
              "parser_version) VALUES (?,?,'handicap',?,?,?,?,?,?,?,1,'p')", (mid, provider, side, kind, offered, line,
              american if offered else None, dec if offered else None, at))
    c.commit()


def pair(c, mid, line, ah, aa, at, **kw):
    quote(c, mid, "home", line, ah, at, **kw)
    quote(c, mid, "away", -line, aa, at, **kw)


def test_pairing_rules(tmp_path):
    c = make_store(tmp_path)
    K = "2026-11-21T15:00:00Z"
    ok = add_match(c, 1, K)
    pair(c, ok, -0.5, -120, 100, "2026-11-20T10:00:00Z")
    pair(c, ok, -0.5, -150, 130, "2026-11-21T09:00:00Z")                   # after t_d (05:00): ignored
    r = ps.pair_at(c, ok, "2026-11-21T05:00:00Z", K)
    assert r["status"] == "paired" and r["line_home"] == -0.5 and (r["american_home"], r["american_away"]) == (-120, 100)
    assert ps.pair_at(c, ok, "2026-11-21T10:00:00Z", K)["american_home"] == -150
    one = add_match(c, 2, K)
    quote(c, one, "home", -0.5, -110, "2026-11-20T10:00:00Z")
    assert ps.pair_at(c, one, "2026-11-21T05:00:00Z", K)["status"] == "unpaired"
    gone = add_match(c, 3, K)
    pair(c, gone, -0.5, -110, -110, "2026-11-20T10:00:00Z")
    quote(c, gone, "away", None, None, "2026-11-20T11:00:00Z", offered=0)    # a withdrawn side
    assert ps.pair_at(c, gone, "2026-11-21T05:00:00Z", K)["status"] == "unpaired"
    skew = add_match(c, 4, K)
    quote(c, skew, "home", -0.5, -110, "2026-11-20T10:00:00Z"); quote(c, skew, "away", 1.0, -110, "2026-11-20T10:00:00Z")
    assert ps.pair_at(c, skew, "2026-11-21T05:00:00Z", K)["status"] == "unpaired"          # +1.0 is not -(-0.5)
    odd = add_match(c, 5, K)
    pair(c, odd, -0.3, -110, -110, "2026-11-20T10:00:00Z")
    assert ps.pair_at(c, odd, "2026-11-21T05:00:00Z", K)["status"] == "unpaired"           # not a multiple of 0.25
    none = add_match(c, 6, K)
    assert ps.pair_at(c, none, "2026-11-21T05:00:00Z", K)["status"] == "no_quote"
    spell = add_match(c, 7, K)
    pair(c, spell, -1.5, -110, -110, "2026-11-20T10:00:00Z", provider="Draft Kings")
    assert ps.pair_at(c, spell, "2026-11-21T05:00:00Z", K)["status"] == "paired"           # every spelling one book
    other = add_match(c, 8, K)
    pair(c, other, -1.5, -110, -110, "2026-11-20T10:00:00Z", provider="ESPN BET")
    assert ps.pair_at(c, other, "2026-11-21T05:00:00Z", K)["status"] == "no_quote"
    opn = add_match(c, 9, K)
    pair(c, opn, -1.5, -110, -110, "2026-11-20T10:00:00Z", kind="open")
    assert ps.pair_at(c, opn, "2026-11-21T05:00:00Z", K)["status"] == "no_quote"           # price_kind 'current' only


def build_world(tmp_path):
    c = make_store(tmp_path)
    K = lambda d: f"2026-11-{d}T15:00:00Z"                                                 # noqa: E731
    def priced(n, line, ah, aa, **kw):
        mid = add_match(c, n, **kw)
        pair(c, mid, line, ah, aa, f"{kw['kickoff'][:10]}T01:00:00Z")
        return mid
    priced(1, -0.5, -120, 100, kickoff=K(21), score=(2, 0), forecasts=((1, None), (2, None)))              # decided, home covers
    priced(2, -0.5, -110, -110, kickoff="2026-11-18T20:00:00Z", score=(1, 0), forecasts=((1, None, "2026-11-18T05:00:00Z"),))   # forecast before T0
    priced(3, -0.5, -110, -110, kickoff="2026-11-22T20:00:00Z", comp="uefa.champions", stage="round-of-16", score=(1, 0))      # knockout
    priced(4, -0.5, -110, -110, kickoff=K(23), score=(1, 0), forecasts=((1, "a" * 64),))                      # not the pinned candidate
    priced(5, -0.5, -110, -110, kickoff=K(24), score=(1, 1), decided="extra_time")                           # unknown ending
    priced(6, -0.5, -110, -110, kickoff=K(25), status="postponed")                                          # void
    priced(7, -0.5, -110, -110, kickoff="2027-01-19T15:00:00Z")                                             # not final, within 7 days
    add_match(c, 8, K(26), score=(1, 0))                                                                    # no quote
    priced(9, -0.25, -110, -110, kickoff=K(27), score=(1, 1))                                               # quarter, draw: half loss
    priced(10, 0.0, -110, -110, kickoff=K(28), score=(0, 0))                                                # whole, draw: push
    priced(11, -0.5, -110, -110, kickoff="2027-01-05T15:00:00Z")                                            # never final, past 7 days
    priced(12, -1.5, -110, -110, kickoff=K(29), score=(1, 0), comp="esp.1")                                 # decided, away covers
    return c


def test_collect_counts_hand_values(tmp_path):
    c = build_world(tmp_path)
    col = ps.collect(c, now=NOW)
    n = col.counts
    assert n["forecasts"] == 9 and n["not_pinned_candidate"] == 1 and n["knockout_excluded"] == 1 and n["forecast_before_t0"] == 1
    assert n["unknown_ending"] == 1 and n["void"] == 1 and n["pending"] == 1 and n["past7"] == 1 and n["no_quote"] == 1
    assert n["paired"] == {"half": 6, "whole": 1, "quarter": 1}
    assert n["push"] == 1 and n["decided_paired"] == 3 and n["unpaired"] == 0
    byid = {x["match_id"]: x for x in col.units if x["state"] == "decided"}
    m1 = byid["soccer:eng.1:1"]                                                                # highest version (2) is the forecast
    assert m1["p_home"] == pytest.approx(0.45) and m1["w"] == 1.0 and m1["y"] == 1 and m1["q_home"] == pytest.approx((120 / 220) / (120 / 220 + 0.5))
    m9 = byid["soccer:eng.1:9"]
    assert m9["w"] == 0.5 and m9["y"] == 0 and m9["line_type"] == "quarter" and m9["p_home"] == pytest.approx(0.45 / 0.875)
    m12 = byid["soccer:esp.1:12"]
    assert m12["y"] == 0 and m12["p_home"] == pytest.approx(ps.app_share(ps.handicap_probs(JOINT, -1.5)))
    assert m12["cluster"] == "esp.1|2026-11-29"


def test_forecast_before_t0_never_counts(tmp_path):
    c = make_store(tmp_path)
    mid = add_match(c, 1, "2026-11-18T20:00:00Z", score=(1, 0), forecasts=((1, None, "2026-11-18T05:00:00Z"),))
    pair(c, mid, -0.5, -110, -110, "2026-11-18T01:00:00Z")
    col = ps.collect(c, now=NOW)
    assert col.units == [] and col.counts["forecast_before_t0"] == 1 and col.counts["forecasts"] == 0


def test_close_and_clv_same_line_only(tmp_path):
    c = make_store(tmp_path)
    mid = add_match(c, 1, "2026-11-21T15:00:00Z", score=(2, 0))
    pair(c, mid, -0.5, -110, -110, "2026-11-21T01:00:00Z")
    pair(c, mid, -0.5, -130, 110, "2026-11-21T14:00:00Z")
    mid2 = add_match(c, 2, "2026-11-22T15:00:00Z", score=(2, 0))
    pair(c, mid2, -0.5, -110, -110, "2026-11-22T01:00:00Z")
    pair(c, mid2, -1.0, -110, -110, "2026-11-22T14:00:00Z")
    col = ps.collect(c, now=NOW)
    u = {x["match_id"]: x for x in col.units}
    q_td, q_cl = 0.5, (130 / 230) / (130 / 230 + 100 / 210)
    assert u[mid]["close_status"] == "ok" and u[mid]["q_close_home"] == pytest.approx(q_cl)
    assert u[mid2]["close_status"] == "line_moved" and col.counts["close_line_moved"] == 1
    # the app (p .45) is below the book (.5): it favours the away side, CLV = (1 - q_close) - (1 - q_td)
    assert u[mid]["clv"] == pytest.approx((1 - q_cl) - (1 - q_td)) and u[mid2]["clv"] is None


# ---- look --------------------------------------------------------------------------------------------------------------------
def balanced(n=800, p=0.6, q=0.5, clusters=40, line=-0.5):
    out = []
    for i in range(n):
        out.append(u(p=p, q=q, y=1 if (i // clusters) % 10 < 6 else 0, comp="eng.1", day=f"2027-{1 + (i % clusters) // 28:02d}-{1 + (i % clusters) % 28:02d}", line=line, mid=f"m{i:04d}"))
    return out


def counts_stub(**kw):
    return {"paired": {"half": 0, "whole": 0, "quarter": 0}, **kw}


def test_below_800_before_deadline_counts_only():
    r = ps.look(balanced(799), NOW, B=200)
    assert r["status"] == "no look yet (n of 800)" and r["n_decided_paired"] == 799 and set(r) == {"status", "n_decided_paired", "n_required"}


def test_look_at_800_hand_values_and_verdict():
    r = ps.look(balanced(800), NOW, B=500)
    dll = (0.6 * -math.log(0.6) + 0.4 * -math.log(0.4)) - math.log(2)
    assert r["status"] == "look" and r["n"] == 800 and r["verdict"] == "app better than DraftKings"
    assert r["primary"]["delta"] == pytest.approx(dll) and r["primary"]["ci95"][1] < 0


def test_verdict_labels_and_deadline():
    worse = [dict(x, p_home=0.4, q_home=0.6, y=1) for x in balanced(800)]
    r = ps.look(worse, NOW, B=300)
    assert r["verdict"] == "DraftKings better than the app" and r["primary"]["ci95"][0] > 0
    flat = [dict(x, p_home=0.5, q_home=0.5) for x in balanced(800)]
    assert ps.look(flat, NOW, B=300)["verdict"] == "not shown"
    late = datetime(2027, 6, 2, tzinfo=timezone.utc)
    r = ps.look(balanced(400), late, B=200)
    assert r["status"] == "look" and r["short_of_planned_n"] == "n 400 of 800 (deadline 2027-05-31)" and r["verdict"] in ("app better than DraftKings", "not shown", "DraftKings better than the app")
    r = ps.look(balanced(250), late, B=200)
    assert r["status"] == "look" and r["verdict"] is None and r["label"] == "underpowered (n of 800)"


def test_look_waits_for_earlier_pending_and_takes_first_800():
    us = balanced(810)
    first = ps.look(us, NOW, B=100)
    assert first["n"] == 800
    pend = balanced(800) + [dict(balanced(1)[0], mid="zz", state="decided")]
    early = dict(pend[3], state="pending", w=None, y=None)
    pend[3] = early
    assert ps.look(pend, NOW, B=100)["status"].startswith("waiting")


def test_secondary_family_and_by_line_type_gate():
    r = ps.look(balanced(800), NOW, B=300)
    assert set(r["holm_secondary"]) == {"S1", "S2"}
    assert r["by_line_type"]["half"]["n"] == 800 and r["by_line_type"]["whole"]["status"] == "counts only (n of 100)"
    mixed = balanced(700) + [dict(x, line_home=0.0, line_type="whole", mid=f"w{i}") for i, x in enumerate(balanced(100, p=0.6))]
    r = ps.look(mixed, NOW, B=100)
    assert r["by_line_type"]["whole"]["n"] == 100 and r["by_line_type"]["half"]["n"] == 700


def test_clv_secondary_p_and_flat_stake():
    us = balanced(800)
    for x in us:
        x["clv"], x["close_status"] = 0.02, "ok"
    r = ps.look(us, NOW, B=300)
    assert r["secondary"]["S1"]["theta"] == pytest.approx(0.02) and r["secondary"]["S1"]["p"] == pytest.approx(1 / 301)
    assert r["holm_secondary"]["S1"]["reject"] is True



def test_flat_stake_descriptive():
    r = ps.flat_stake([dict(u(p=0.6, q=0.5), result_home="win", result_away="loss", decimal_home=1.9, decimal_away=1.9),
                       dict(u(p=0.4, q=0.5), result_home="loss", result_away="half_win", decimal_home=1.9, decimal_away=2.0)])
    # match 1: app >= book -> home, win at 1.9 returns 1.9; match 2: app < book -> away, half win at 2.0 returns (2+1)/2 = 1.5
    assert r["n"] == 2 and r["mean_return"] == pytest.approx((1.9 + 1.5) / 2)


def test_run_refuses_live_and_stores_look(tmp_path):
    with pytest.raises(pc.Refused):
        ps.run(pc.LIVE_DATA, tmp_path.parent / "o", now=NOW)
    data = tmp_path / "copy"
    c = build_world(data)
    c.close()
    out = tmp_path / "report"
    rep = ps.run(data, out, now=NOW, looks_dir=tmp_path / "looks")
    assert rep["look"]["status"] == "no look yet (n of 800)" and (out / "soccer-ah-report.json").is_file()
    blob = json.dumps(rep["look"])
    assert "log_loss" not in blob and "delta" not in blob and "ci95" not in blob
    assert rep["counts"]["forecasts"] == 9
    with pytest.raises(pc.Refused):
        ps.run(data, data / "inside", now=NOW)


def test_calibration_slope_test_on_calibrated_groups():
    us = []
    for i in range(20):
        p, hit = (0.2, i % 5 == 0) if i < 10 else (0.8, i % 5 != 0)
        us.append(dict(u(p=p, q=0.5, y=1 if hit else 0, day=f"2027-01-{1 + i % 10:02d}", mid=f"c{i}"), state="decided"))
    r = ps.slope_test(us, B=60)
    assert r["slope"] is not None and r["p"] > 0.5


# ---- review fixes (addendum 2026-11-18 soccer) ---------------------------------------------------------------------------
def test_before_the_look_only_forecasts_pairs_and_the_stop_count_are_reported(tmp_path):
    data = tmp_path / "copy"
    build_world(data).close()
    rep = ps.run(data, tmp_path / "report", now=NOW, looks_dir=tmp_path / "looks")
    assert rep["look"]["status"] == "no look yet (n of 800)"
    banned = {"push", "void", "unknown_ending", "pending", "past7", "close_line_moved", "close_no_pair"}
    assert not banned & set(rep["counts"])
    assert rep["counts"]["forecasts"] == 9 and rep["counts"]["paired"] == {"half": 6, "whole": 1, "quarter": 1}
    assert rep["counts"]["decided_paired"] == 3 and rep["counts"]["no_quote"] == 1
    assert set(rep["counts"]) == set(ps.PRE_LOOK_COUNTS) == {"forecasts", "no_goals_forecast", "not_pinned_candidate", "knockout_excluded", "not_regular_season",
                                                             "forecast_before_t0", "after_stop_date", "no_quote", "unpaired", "paired", "decided_paired"}
    assert rep["look"] == {"status": "no look yet (n of 800)", "n_decided_paired": 3, "n_required": 800}


def test_matches_without_any_goals_forecast_are_counted(tmp_path):
    c = make_store(tmp_path)
    add_match(c, 1, "2026-11-21T15:00:00Z", forecasts=())
    add_match(c, 2, "2026-11-22T15:00:00Z", forecasts=((1, "a" * 64),))
    n = ps.collect(c, now=NOW).counts
    assert n["no_goals_forecast"] == 1 and n["not_pinned_candidate"] == 1


def test_line_bins_include_whole_and_quarter_types_only_once_they_reach_100():
    def mixed(n_whole):
        return balanced(800 - n_whole) + [dict(x, line_home=-2.0, line_type="whole", mid=f"w{i}") for i, x in enumerate(balanced(n_whole))]
    r = ps.look(mixed(100), NOW, B=60)
    assert r["by_line_bin"]["1.5"]["n"] == 100 and r["by_line_bin"]["0.5"]["n"] == 700        # |line| 2.0 sits in the 1.5 bin
    r = ps.look(mixed(99), NOW, B=60)
    assert r["by_line_bin"]["1.5"]["n"] == 0 and r["by_line_bin"]["0.5"]["n"] == 701
    assert r["by_line_type"]["whole"] == {"status": "counts only (n of 100)", "n": 99}


def test_clv_set_is_the_look_sets_same_line_decided_half_matches():
    us = balanced(800)
    for x in us[:100]:
        x["clv"], x["close_status"] = 0.02, "ok"
    for x in us[100:150]:
        x["close_status"] = "line_moved"                      # close pair at another line: no CLV
    pend = dict(us[0], mid="zz-pending", state="pending", w=None, y=None, clv=0.5, close_status="ok", kickoff_at="2027-02-27T15:00:00Z")
    r = ps.look(us + [pend], NOW, B=60)
    assert r["status"] == "look" and r["secondary"]["S1"]["n"] == 100 and r["secondary"]["S1"]["theta"] == pytest.approx(0.02)


def test_flat_stake_push_returns_the_stake_and_is_counted():
    r = ps.flat_stake([dict(u(p=0.6, q=0.5, line=0.0), result_home="push", result_away="push", decimal_home=1.9, decimal_away=1.9),
                       dict(u(p=0.6, q=0.5), result_home="loss", result_away="win", decimal_home=1.9, decimal_away=1.9)])
    assert r["n"] == 2 and r["mean_return"] == pytest.approx(0.5)


def test_single_look_is_keyed_to_the_document_not_to_the_report_folder(tmp_path, monkeypatch):
    data = tmp_path / "copy"
    make_store(data).close()
    looks = tmp_path / "looks"
    monkeypatch.setattr(ps, "STOP_N", 0)                  # the stop counts as reachable, so the (stubbed) look is computed
    monkeypatch.setattr(ps, "look", lambda units, now, B=0: {"status": "look", "n": 800})
    first = ps.run(data, tmp_path / "out1", now=NOW, looks_dir=looks)
    monkeypatch.setattr(ps, "look", lambda *a, **k: pytest.fail("a second look must not be computed"))
    again = ps.run(data, tmp_path / "out2", now=NOW, looks_dir=looks)
    assert again["look"]["status"] == first["look"]["status"] == "look"


def test_stop_date_is_the_competition_local_kickoff_date(tmp_path):
    c = make_store(tmp_path)
    add_match(c, 1, "2027-05-31T22:00:00Z")          # 23:00 BST on 2027-05-31: counts
    add_match(c, 2, "2027-05-31T23:30:00Z")          # 00:30 BST on 2027-06-01: after the stop date
    n = ps.collect(c, now=NOW).counts
    assert n["after_stop_date"] == 1 and n["forecasts"] == 1


def test_deadline_passes_on_the_eastern_date():
    us = balanced(400)
    assert ps.look(us, datetime(2027, 6, 1, 3, 30, tzinfo=timezone.utc))["status"].startswith("no look yet")        # 23:30 Eastern on 05-31
    assert ps.look(us, datetime(2027, 6, 1, 4, 30, tzinfo=timezone.utc), B=50)["status"] == "look"                  # 00:30 Eastern on 06-01


def test_pre_look_mode_reads_results_only_for_the_count_and_no_close(tmp_path, monkeypatch):
    data = tmp_path / "copy"
    build_world(data).close()
    seen = []
    orig = ps.pair_at
    monkeypatch.setattr(ps, "pair_at", lambda conn, mid, at, kickoff, *a, **k: seen.append((at, kickoff)) or orig(conn, mid, at, kickoff, *a, **k))
    rep = ps.run(data, tmp_path / "report", now=NOW, looks_dir=tmp_path / "looks")
    assert rep["look"]["status"].startswith("no look yet") and rep["counts"]["forecasts"] == 9
    assert seen and all(at < kickoff and at[:10] == kickoff[:10] or at < kickoff for at, kickoff in seen)
    assert all(at != ps._ts(ps._parse(k) - ps.timedelta(seconds=1)) for at, k in seen)      # never the closing query
    assert rep["counts"]["decided_paired"] == 3
