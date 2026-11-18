"""NCAAB top-4 policy evaluation (docs/preregistration/ncaab-top4-policy-eval-2026-11-18.md): synthetic fixtures only,
expected values computed by hand (or from numpy / the normal CDF independently of the module)."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from app import ncaab_cards, prereg_common as pc, prereg_ncaab_top4 as pn
from tests.ncaab_support import add_game, make_store


@pytest.fixture(autouse=True)
def _looks_dir_in_tmp(tmp_path, monkeypatch):
    """No test may write to the real looks folder (~/nba-predictor-research-data/prereg-looks)."""
    monkeypatch.setattr(pc, "LOOKS_DIR", tmp_path / "default-looks")

DAY = "2026-11-18"
TIP = "2026-11-18T23:00:00Z"
SEL_OK = "2026-11-18T16:00:30Z"           # 11:00:30 Eastern (UTC-5 in November)


def ent(market, side, line, app, book, qid, provider="DraftKings", at="2026-11-18T15:30:00Z"):
    return {"market": market, "side": side, "line": line, "app": app, "book": book, "gap": round((app - book) * 100, 1),
            "quote_id": qid, "provider": provider, "quote_retrieved_at": at}


def cand(gid, tip, entries, error=None):
    priced = [e for e in entries if e.get("gap") is not None]
    best = max(priced, key=lambda m: (m["gap"], -ncaab_cards.MARKETS.index(m["market"])), default=None)
    return {"game_id": gid, "tipoff_at": tip, "markets": entries, "gap": best["gap"] if best else None,
            "best_market": best["market"] if best else None}


def row(et_date=DAY, selected_at=SEL_OK, status="ok", rule=ncaab_cards.RULE, candidates=(), selected=None, mv="m1"):
    cands = list(candidates)
    if selected is None:
        ranked = sorted((c for c in cands if c["gap"] is not None and c["gap"] > 0), key=lambda c: (-c["gap"], c["tipoff_at"], c["game_id"]))
        selected = [c["game_id"] for c in ranked[:4]]
    return {"et_date": et_date, "rule": rule, "selected_at": selected_at, "status": status, "error": None,
            "model_version": mv, "selected": selected, "candidates": cands}


def pricedcand(gid, tip, gap, qid=1):
    """One winner entry with the given gap (points): app = book + gap/100."""
    return cand(gid, tip, [ent("winner", "a", None, round(0.5 + gap / 100, 6), 0.5, qid)])


ASOF = datetime(2027, 1, 1, tzinfo=timezone.utc)


# ---- document / constants -------------------------------------------------------------------------------------------
def test_constants_match_the_frozen_document_and_decision_file():
    pc.check_prereg("ncaab-top4-policy-eval-2026-11-18.md", pn.PREREG_SHA256)
    assert pn.PREREG_SHA256 == "55b8734a28eba1f56804810516fbf96ee1f0df6c2b7ce7c4dd1e6baa22bb9f3b"
    assert pn.SIGMA_SPREAD == 11.319247889236534 and pn.SIGMA_TOTAL == 17.762583342156923
    assert pn.T0 == "2026-11-18T14:29:50Z" and pn.RULE == "ncaab-top4-gap-v1"


def test_rule_is_the_literal_not_the_live_constant(monkeypatch):
    import importlib
    monkeypatch.setattr(ncaab_cards, "RULE", "ncaab-top4-gap-v2")      # a changed live rule must not change what is counted
    try:
        reloaded = importlib.reload(pn)
        assert reloaded.RULE == "ncaab-top4-gap-v1"
    finally:
        monkeypatch.undo()
        importlib.reload(pn)


def test_addendum_pin_is_checked(tmp_path, monkeypatch):
    assert pn.ADDENDA_SHA256 == {"addendum-2026-11-18-ncaab-evaluator-clarifications.md":
                                 "da8d4ff0cb1ef96c16b46d7d74b8e7718d18031240a28112155cb6f23ab7fb2d"}
    pn.check_document()
    d = tmp_path / "docs" / "preregistration"
    d.mkdir(parents=True)
    (d / "ncaab-top4-policy-eval-2026-11-18.md").write_bytes((pn.REPO_ROOT / "docs/preregistration/ncaab-top4-policy-eval-2026-11-18.md").read_bytes())
    (d / "addendum-2026-11-18-ncaab-evaluator-clarifications.md").write_bytes(b"changed")
    monkeypatch.setattr(pn, "REPO_ROOT", tmp_path)
    with pytest.raises(pc.Refused, match="addendum"):
        pn.check_document()


def test_wrong_document_bytes_refuse(tmp_path, monkeypatch):
    monkeypatch.setattr(pn, "REPO_ROOT", tmp_path)
    with pytest.raises(pc.Refused):
        pn.check_document()


# ---- qualifying days ------------------------------------------------------------------------------------------------
def test_qualifying_day_rules_and_reasons():
    c = [pricedcand("g1", TIP, 5.0)]
    rows = [
        row("2026-11-17", "2026-11-17T16:00:30Z", candidates=c),                          # before T0
        row("2026-11-18", "2026-11-18T16:15:00Z", candidates=c),                          # 11:15:00 ET inclusive: qualifies
        row("2026-11-19", "2026-11-19T16:15:01Z", candidates=c),                          # 11:15:01: late
        row("2026-11-20", "2026-11-20T15:59:59Z", candidates=c),                          # 10:59:59: early
        row("2026-11-21", "2026-11-21T16:00:00Z", candidates=c),                          # 11:00:00 inclusive: qualifies
        row("2026-11-22", "2026-11-22T23:26:00Z", candidates=c),                          # 18:26 ET: late
        row("2026-11-23", "2026-11-23T16:00:00Z", status="error", candidates=[]),         # selection error
        row("2026-11-24", "2026-11-24T16:00:00Z", candidates=[cand("g9", TIP, [{"market": "winner", "reason": "no moneyline saved"}])]),
    ]
    cl = pn.classify(rows, asof=ASOF)
    assert [d["et_date"] for d in cl.qualifying] == ["2026-11-18", "2026-11-21"]
    assert cl.excluded["before T0"] == 1 and cl.excluded["selection outside 11:00-11:15 ET"] == 3
    assert cl.excluded["selection error"] == 1 and cl.excluded["no priced candidate"] == 1
    # 2026-11-25 .. 2026-12-31 have no row at all (the days up to ASOF-1)
    assert cl.excluded["no selection row"] > 30


def test_summer_time_is_eastern_not_fixed_offset():
    c = [pricedcand("g1", "2027-06-01T23:00:00Z", 5.0)]
    ok = row("2027-06-01", "2027-06-01T15:05:00Z", candidates=c)       # 11:05 EDT (UTC-4)
    bad = row("2027-06-02", "2027-06-02T16:05:00Z", candidates=c)      # 12:05 EDT
    cl = pn.classify([ok, bad], asof=datetime(2027, 7, 1, tzinfo=timezone.utc), t0="2026-11-18T14:29:50Z", stop_date="2027-12-31")
    assert [d["et_date"] for d in cl.qualifying] == ["2027-06-01"]


def test_t0_filter_compares_selected_at_not_the_date():
    c = [pricedcand("g1", TIP, 5.0)]
    just_before = row(DAY, "2026-11-18T14:29:49Z", candidates=c)       # 09:29:49 ET: before T0 (and early)
    cl = pn.classify([just_before], asof=ASOF)
    assert cl.qualifying == [] and cl.excluded["before T0"] == 1


def test_rule_change_ends_counting_and_stop_rules():
    c = [pricedcand("g1", TIP, 5.0)]
    rows = [row("2026-11-18", candidates=c), row("2026-11-19", "2026-11-19T16:00:00Z", rule="ncaab-top4-gap-v2", candidates=c),
            row("2026-11-20", "2026-11-20T16:00:00Z", candidates=c)]
    cl = pn.classify(rows, asof=ASOF)
    assert [d["et_date"] for d in cl.qualifying] == ["2026-11-18"]
    assert cl.excluded["rule changed (counting ended)"] == 2 and cl.stop_reached and cl.stop_reason == "rule changed"
    # 60th qualifying day stops counting; 2027-03-15 date stops counting
    many = [row(f"2027-01-{d:02d}", f"2027-01-{d:02d}T16:00:00Z", candidates=c) for d in range(1, 29)] + \
           [row(f"2027-02-{d:02d}", f"2027-02-{d:02d}T16:00:00Z", candidates=c) for d in range(1, 29)] + \
           [row(f"2027-03-{d:02d}", f"2027-03-{d:02d}T16:00:00Z", candidates=c) for d in range(1, 11)]
    assert len(many) == 66
    cl = pn.classify(many, asof=ASOF)
    assert len(cl.counted) == 60 and cl.stop_reached and cl.stop_reason == "60 qualifying days"
    assert cl.counted[-1]["et_date"] == "2027-03-04"
    short = pn.classify(many[:5], asof=datetime(2027, 3, 15, 20, tzinfo=timezone.utc))
    assert not short.stop_reached                                       # 2027-03-15 itself is still a counted day
    short = pn.classify(many[:5], asof=datetime(2027, 3, 16, 12, tzinfo=timezone.utc))
    assert short.stop_reached and short.stop_reason == "2027-03-15" and len(short.counted) == 5
    late = pn.classify([row("2027-03-16", "2027-03-16T16:00:00Z", candidates=c)], asof=datetime(2027, 4, 1, tzinfo=timezone.utc))
    assert late.counted == [] and late.excluded["after 2027-03-15"] == 1


# ---- replay and policies --------------------------------------------------------------------------------------------
def test_replay_and_mismatch_excludes_the_day():
    cs = [pricedcand("g1", "2026-11-18T22:00:00Z", 9.0), pricedcand("g2", "2026-11-18T21:00:00Z", 12.0),
          pricedcand("g3", "2026-11-18T20:00:00Z", -1.0), pricedcand("g4", "2026-11-18T20:30:00Z", 12.0),
          pricedcand("g5", "2026-11-18T19:00:00Z", 3.0), pricedcand("g6", "2026-11-18T19:30:00Z", 4.0),
          pricedcand("g7", "2026-11-18T19:45:00Z", 1.0)]
    assert pn.replay_selected(cs) == ["g4", "g2", "g1", "g6"]            # 12.0 tie: earlier tip-off first
    good = row(candidates=cs)
    assert good["selected"] == ["g4", "g2", "g1", "g6"]
    bad = row(candidates=cs, selected=["g2", "g4", "g1", "g6"])
    cl = pn.classify([bad], asof=ASOF)
    assert cl.qualifying == [] and cl.excluded["P0 replay mismatch"] == 1


def day_for_policies():
    e = lambda m, s, ln, app, book, q: ent(m, s, ln, app, book, q)      # noqa: E731
    cs = [
        cand("a", "2026-11-18T18:00:00Z", [e("winner", "a", None, .70, .60, 1), e("spread", "b", 3.5, .62, .55, 2), e("total", "a", 140.5, .55, .52, 3)]),  # best spread 7.0
        cand("b", "2026-11-18T19:00:00Z", [e("winner", "a", None, .80, .60, 4), e("spread", "a", -2.5, .51, .50, 5)]),                                 # best winner 20.0
        cand("c", "2026-11-18T20:00:00Z", [e("winner", "b", None, .55, .60, 6)]),                                                                       # -5.0
        cand("d", "2026-11-18T21:00:00Z", [e("total", "b", 150.5, .90, .50, 7), e("winner", "a", None, .66, .62, 8)]),                                 # best total 40.0
        cand("e", "2026-11-18T22:00:00Z", [e("winner", "a", None, .53, .50, 9)]),                                                                       # 3.0
        cand("f", "2026-11-18T22:30:00Z", [e("winner", "a", None, .56, .50, 10), e("spread", "a", -1.5, .75, .70, 11)]),                               # best spread? winner 6.0 vs spread 5.0 -> winner
        cand("u", "2026-11-18T23:00:00Z", [{"market": "winner", "reason": "no moneyline saved"}]),                                                      # unpriced
    ]
    return pn.prepare_day(row(candidates=cs))


def test_policy_choices():
    day = day_for_policies()
    ids = lambda units: [(u["game_id"], u["market"]) for u in units]      # noqa: E731
    assert [c["game_id"] for c in day["priced"]] == ["a", "b", "c", "d", "e", "f"]
    assert ids(pn.choose("P0", day)) == [("d", "total"), ("b", "winner"), ("a", "winner"), ("f", "winner")]
    assert ids(pn.choose("P3", day)) == [("a", "winner"), ("b", "winner"), ("c", "winner"), ("d", "total"), ("e", "winner"), ("f", "winner")]
    assert ids(pn.choose("P4", day)) == [("c", "winner"), ("e", "winner"), ("f", "winner"), ("a", "winner")]   # gaps -5, 3, 6, 10
    assert ids(pn.choose("P7", day)) == []


def test_policy_p2_p6_details():
    day = day_for_policies()
    p2 = pn.choose("P2", day)                      # largest app over priced entries: d total .90, b winner .80, a winner .70, d.. a/f
    assert [(u["game_id"], u["market"], u["app"]) for u in p2] == [("d", "total", .90), ("b", "winner", .80), ("f", "spread", .75), ("a", "winner", .70)]
    p6 = pn.choose("P6", day)                      # 0 < gap < 20: b is 20.0 (excluded), d is 40.0 (excluded)
    assert [u["game_id"] for u in p6] == ["a", "f", "e"]


def test_p5_winner_entries_sorted_by_winner_gap():
    day = day_for_policies()
    got = [(u["game_id"], u["gap"]) for u in pn.choose("P5", day)]
    # winner gaps: a 10.0, b 20.0, c -5.0, d 4.0, e 3.0, f 6.0 -> top 4 with gap > 0
    assert got == [("b", 20.0), ("a", 10.0), ("f", 6.0), ("d", 4.0)]


def test_random4_draw_order_and_seed():
    d6 = pn.prepare_day(row(candidates=[pricedcand(g, f"2026-11-18T{18 + i}:00:00Z", 1.0 + i) for i, g in enumerate("abcdef")]))
    d2 = pn.prepare_day(row("2026-11-19", "2026-11-19T16:00:00Z", candidates=[pricedcand("x", "2026-11-19T20:00:00Z", 2.0),
                                                                          pricedcand("y", "2026-11-19T21:00:00Z", 3.0)]))
    draws = pn.random4_draws([d6, d2], reps=2)
    # numpy default_rng(20261118): choice(6,4), choice(2,2), choice(6,4), choice(2,2) in this order (replicate-major)
    assert [[list(map(int, x)) for x in rep] for rep in draws] == [[[0, 5, 1, 3], [0, 1]], [[1, 5, 3, 0], [0, 1]]]


def test_random4_candidates_sorted_by_tipoff_then_id():
    # input order differs from (tipoff, game_id) order: the draw indexes the sorted list
    d = pn.prepare_day(row(candidates=[pricedcand("z", "2026-11-18T18:00:00Z", 1.0), pricedcand("a", "2026-11-18T19:00:00Z", 2.0),
                                       pricedcand("m", "2026-11-18T18:00:00Z", 3.0)]))
    assert [c["game_id"] for c in d["priced"]] == ["m", "z", "a"]
    assert [c["game_id"] for c in pn.chosen_random(d, [2, 0])] == ["a", "m"]


# ---- close quotes, conversion, CLV ----------------------------------------------------------------------------------
def test_convert_line_hand_values():
    # spread, close line -4 p .5 -> mu = 4; at selection line -3: Phi(1/sigma) = 0.5351988 (side a), 0.4648012 (side b)
    assert pn.convert("spread", 0.5, -4.0, -3.0, "a") == pytest.approx(0.5351987997334281, abs=1e-9)
    assert pn.convert("spread", 0.5, -4.0, -3.0, "b") == pytest.approx(0.4648012002665719, abs=1e-9)
    # total, close 140 p .5; selection 141 over -> Phi(-1/sigma_total)
    assert pn.convert("total", 0.5, 140.0, 141.0, "a") == pytest.approx(0.47755215932976297, abs=1e-9)
    assert pn.convert("total", 0.5, 140.0, 141.0, "b") == pytest.approx(0.522447840670237, abs=1e-9)
    # same line: unchanged
    assert pn.convert("spread", 0.55, -3.0, -3.0, "a") == pytest.approx(0.55, abs=1e-12)
    # p_c = .55, close -4, selection -3: Phi(Phi^-1(.55) + 1/sigma) = 0.5847289821
    assert pn.convert("spread", 0.55, -4.0, -3.0, "a") == pytest.approx(0.5847289821220172, abs=1e-9)


def test_points_clv_signs():
    assert pn.points_clv("spread", "a", -3.0, -4.0) == 1.0 and pn.points_clv("spread", "b", -3.0, -4.0) == -1.0
    assert pn.points_clv("total", "a", 141.0, 140.0) == -1.0 and pn.points_clv("total", "b", 141.0, 140.0) == 1.0
    assert pn.points_clv("winner", "a", None, None) is None


def quote(conn, game, market, line, p_a, at, provider="DraftKings", label="current"):
    fetch = conn.execute("INSERT INTO raw_fetches (raw_id, retrieved_at) VALUES (1, ?)", (at,)).lastrowid
    conn.execute("INSERT INTO market_quotes (sport, game_id, provider, market, price_label, line, price_a, price_b, p_a_devig, p_b_devig, "
                 "quote_time, retrieved_at, fetch_id, raw_id) VALUES ('ncaab', ?, ?, ?, ?, ?, -110, -110, ?, ?, NULL, ?, ?, 1)",
                 (game, provider, market, label, line, p_a, 1 - p_a, at, fetch))
    conn.commit()
    return conn.execute("SELECT MAX(quote_id) FROM market_quotes").fetchone()[0]


@pytest.fixture
def store(tmp_path, monkeypatch):
    conn = make_store(tmp_path, monkeypatch)
    yield conn
    conn.close()


def unit(game="ncaab:501", market="winner", side="a", line=None, q_sel=0.60, qid=1, provider="DraftKings", tip=TIP):
    return {"et_date": DAY, "game_id": game, "tipoff_at": tip, "market": market, "side": side, "line": line, "q_sel": q_sel,
            "quote_id": qid, "provider": provider, "quote_retrieved_at": "2026-11-18T15:30:00Z", "gap": 5.0, "game_gap": 5.0,
            "app": 0.65, "model_version": "m1"}


def test_close_winner_same_provider_spelling_and_staleness(store):
    add_game(store, "501", TIP, "1", "2")
    sel = quote(store, "ncaab:501", "winner", None, 0.60, "2026-11-18T16:00:00Z")
    quote(store, "ncaab:501", "winner", None, 0.90, "2026-11-18T22:55:00Z", provider="ESPN BET")    # another book: ignored
    quote(store, "ncaab:501", "winner", None, 0.63, "2026-11-18T22:40:00Z", provider="Draft Kings")   # same book, other spelling
    quote(store, "ncaab:501", "winner", None, 0.20, "2026-11-18T23:00:00Z")                           # at tip-off: not before it
    c = pn.close_for(store, unit(qid=sel))
    assert c["status"] == "ok" and c["q_close"] == pytest.approx(0.63) and not c["converted"]
    assert pn.close_for(store, unit(qid=sel, side="b"))["q_close"] == pytest.approx(0.37)


def test_close_stale_boundary_and_none_and_same_quote(store):
    add_game(store, "502", TIP, "1", "2")
    add_game(store, "503", TIP, "1", "2")
    add_game(store, "504", TIP, "3", "4")
    s1 = quote(store, "ncaab:502", "winner", None, 0.60, "2026-11-18T15:00:00Z")
    quote(store, "ncaab:502", "winner", None, 0.61, "2026-11-18T21:59:59Z")        # 60 min + 1 s before tip-off: stale
    assert pn.close_for(store, unit("ncaab:502", qid=s1))["status"] == "stale"
    s2 = quote(store, "ncaab:503", "winner", None, 0.60, "2026-11-18T15:00:00Z")
    quote(store, "ncaab:503", "winner", None, 0.62, "2026-11-18T22:00:00Z")        # exactly 60 min: not stale
    assert pn.close_for(store, unit("ncaab:503", qid=s2))["status"] == "ok"
    s3 = quote(store, "ncaab:504", "winner", None, 0.60, "2026-11-18T22:45:00Z", provider="ESPN BET")
    assert pn.close_for(store, unit("ncaab:504", qid=s3, provider="DraftKings"))["status"] == "no_close"   # no cross-book fallback
    # the selection quote itself is the latest close: CLV 0
    s4 = quote(store, "ncaab:504", "winner", None, 0.60, "2026-11-18T22:30:00Z")
    c = pn.close_for(store, unit("ncaab:504", qid=s4))
    assert c["status"] == "ok" and c["quote_id"] == s4
    assert pn.values(unit("ncaab:504", qid=s4), c)["clv"] == 0.0


def test_close_lined_uses_current_label_and_converts(store):
    add_game(store, "505", TIP, "1", "2")
    sel = quote(store, "ncaab:505", "spread", -3.0, 0.52, "2026-11-18T16:00:00Z")
    quote(store, "ncaab:505", "spread", -9.0, 0.10, "2026-11-18T22:50:00Z", label="open")       # open rows never close a lined market
    quote(store, "ncaab:505", "spread", -4.0, 0.50, "2026-11-18T22:40:00Z")
    u = unit("ncaab:505", "spread", "a", -3.0, 0.52, sel)
    c = pn.close_for(store, u)
    assert c["status"] == "ok" and c["converted"] and c["line"] == -4.0
    v = pn.values(u, c)
    assert v["clv"] == pytest.approx(0.5351987997334281 - 0.52, abs=1e-9) and v["points_clv"] == 1.0
    ub = unit("ncaab:505", "spread", "b", -3.0, 0.48, sel)
    assert pn.values(ub, pn.close_for(store, ub))["clv"] == pytest.approx(0.4648012002665719 - 0.48, abs=1e-9)
    same = quote(store, "ncaab:505", "spread", -3.0, 0.58, "2026-11-18T22:50:00Z")             # same line again: no conversion
    c2 = pn.close_for(store, u)
    assert c2["quote_id"] == same and not c2["converted"] and pn.values(u, c2)["clv"] == pytest.approx(0.58 - 0.52)
    assert pn.values(u, c2)["points_clv"] == 0.0


def test_close_total_conversion(store):
    add_game(store, "506", TIP, "1", "2")
    sel = quote(store, "ncaab:506", "total", 141.0, 0.50, "2026-11-18T16:00:00Z")
    quote(store, "ncaab:506", "total", 140.0, 0.50, "2026-11-18T22:30:00Z")
    u = unit("ncaab:506", "total", "a", 141.0, 0.50, sel)
    assert pn.values(u, pn.close_for(store, u))["clv"] == pytest.approx(0.47755215932976297 - 0.50, abs=1e-9)


# ---- settlement and excess ------------------------------------------------------------------------------------------
def test_settle_units(store):
    add_game(store, "601", TIP, "1", "2", "final", 80, 78)
    add_game(store, "602", TIP, "1", "2", "final", 80, 78)
    add_game(store, "603", TIP, "1", "2", "postponed")
    add_game(store, "604", "2026-11-18T23:30:00Z", "1", "2", "final", 80, 78)      # start moved after the selection
    add_game(store, "605", TIP, "1", "2", "scheduled")
    add_game(store, "606", "2026-11-01T23:00:00Z", "1", "2", "in_progress")        # > 7 days old, never final
    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    assert pn.settle_unit(store, unit("ncaab:601", "spread", "a", -1.5), now) == {"state": "decided", "won": True}
    assert pn.settle_unit(store, unit("ncaab:601", "spread", "b", -1.5), now) == {"state": "decided", "won": False}
    assert pn.settle_unit(store, unit("ncaab:602", "spread", "a", -2.0), now)["state"] == "push"
    assert pn.settle_unit(store, unit("ncaab:602", "total", "a", 157.5), now) == {"state": "decided", "won": True}   # 158 > 157.5
    assert pn.settle_unit(store, unit("ncaab:603"), now)["state"] == "void"
    assert pn.settle_unit(store, unit("ncaab:604"), now)["state"] == "void"
    assert pn.settle_unit(store, unit("ncaab:605"), now)["state"] == "pending"
    assert pn.settle_unit(store, unit("ncaab:606", tip="2026-11-01T23:00:00Z"), now)["state"] == "past7"
    assert pn.settle_unit(store, unit("ncaab:999"), now)["state"] == "unscorable"
    final_nulls = pn.settle_unit(store, unit("ncaab:601", "winner", "a"), now)
    assert final_nulls == {"state": "decided", "won": True}


def test_excess_is_outcome_minus_q_sel():
    v = pn.values(unit(q_sel=0.60), {"status": "no_close"}, {"state": "decided", "won": True})
    assert v["excess"] == pytest.approx(0.40) and v["clv"] is None
    assert pn.values(unit(q_sel=0.60), {"status": "no_close"}, {"state": "decided", "won": False})["excess"] == pytest.approx(-0.60)
    assert pn.values(unit(q_sel=0.60), {"status": "no_close"}, {"state": "push"})["excess"] is None
    ok = {"status": "ok", "q_close": 0.63, "converted": False, "line": None, "quote_id": 2}
    assert pn.values(unit(q_sel=0.60), ok, {"state": "void"})["clv"] is None        # excluded from CLV when not decided


# ---- aggregation ----------------------------------------------------------------------------------------------------
def test_summary_hand_values():
    vs = [{"clv": 0.02, "excess": 0.4, "points_clv": 1.0, "won": True, "q_sel": 0.6, "et_date": "d1", "state": "decided", "close": "ok"},
          {"clv": -0.01, "excess": -0.5, "points_clv": None, "won": False, "q_sel": 0.5, "et_date": "d1", "state": "decided", "close": "ok"},
          {"clv": None, "excess": 0.3, "points_clv": None, "won": True, "q_sel": 0.7, "et_date": "d2", "state": "decided", "close": "stale"},
          {"clv": None, "excess": None, "points_clv": None, "won": None, "q_sel": 0.5, "et_date": "d2", "state": "push", "close": "no_close"}]
    s = pn.summarize(vs)
    assert s["n_picks"] == 4 and s["n_days"] == 2 and s["n_decided"] == 3 and s["push"] == 1
    assert s["mean_clv_pp"] == pytest.approx(0.5) and s["n_clv"] == 2 and s["stale_close"] == 1 and s["no_close"] == 1
    assert s["mean_excess_pp"] == pytest.approx((0.4 - 0.5 + 0.3) / 3 * 100) and s["hit_rate"] == pytest.approx(2 / 3)
    assert s["mean_q_sel"] == pytest.approx(0.6)
    assert s["day_sums"] == {"d1": {"clv": pytest.approx(0.01), "excess": pytest.approx(-0.1)}, "d2": {"clv": 0.0, "excess": pytest.approx(0.3)}}
    assert pn.summarize([])["n_picks"] == 0


def test_bins_edges():
    assert [pn.bin_of(g) for g in (-3, 0, 0.1, 4.9, 5, 9.9, 10, 19.9, 20, 31)] == ["<=0", "<=0", "0-5", "0-5", "5-10", "5-10", "10-20", "10-20", ">=20", ">=20"]


def test_hypothesis_p_values_all_positive_and_all_zero():
    days = {"d1": [(0.01, 0.3, 2.0), (0.02, 0.2, 3.0)], "d2": [(0.03, 0.1, 4.0)], "d3": [(0.04, 0.5, 6.0), (0.01, 0.1, 1.0)]}
    nums = pn.day_arrays([[v[0] for v in d] for d in days.values()])
    h = pn.test_mean(*nums)
    assert h["theta"] == pytest.approx((0.01 + 0.02 + 0.03 + 0.04 + 0.01) / 5)
    assert h["p"] == pytest.approx(1 / 10001)
    z = pn.test_mean(*pn.day_arrays([[0.0, 0.0], [0.0]]))
    assert z["p"] == 1.0 and z["theta"] == 0.0


def test_h3_randomisation_p():
    assert pn.h3_p(0.5, [0.1] * 1000) == pytest.approx(1 / 1001)
    assert pn.h3_p(0.5, [0.5] * 1000) == 1.0                 # ties count as >=
    assert pn.h3_p(0.5, [0.9] * 3 + [0.1] * 997) == pytest.approx(4 / 1001)


# ---- analysis, interim-look lock, audit -----------------------------------------------------------------------------
def make_world(tmp_path, monkeypatch, nday=2):
    """A synthetic copy folder: ncaab store with two games per day, quotes and results, and a ledger with the selections."""
    conn = make_store(tmp_path, monkeypatch)
    ledger = sqlite3.connect(tmp_path / "ledger.sqlite")
    ledger.row_factory = sqlite3.Row
    ledger.executescript(ncaab_cards.SCHEMA)
    for i in range(nday):
        d = f"2026-11-{18 + i}"
        tip = f"{d}T23:00:00Z"
        ev1, ev2 = str(700 + 2 * i), str(701 + 2 * i)
        add_game(conn, ev1, tip, "1", "2", "final", 80, 70)
        add_game(conn, ev2, tip, "3", "4", "final", 60, 70)
        s1 = quote(conn, f"ncaab:{ev1}", "winner", None, 0.60, f"{d}T16:00:00Z")
        s2 = quote(conn, f"ncaab:{ev2}", "winner", None, 0.50, f"{d}T16:00:00Z")
        quote(conn, f"ncaab:{ev1}", "winner", None, 0.62, f"{d}T22:30:00Z")
        quote(conn, f"ncaab:{ev2}", "winner", None, 0.48, f"{d}T22:30:00Z")
        cs = [cand(f"ncaab:{ev1}", tip, [ent("winner", "a", None, 0.66, 0.60, s1)]), cand(f"ncaab:{ev2}", tip, [ent("winner", "a", None, 0.55, 0.50, s2)])]
        r = row(d, f"{d}T16:00:30Z", candidates=cs)
        ledger.execute("INSERT INTO ncaab_card_selections (et_date, rule, selected_at, status, error, model_version, selected, candidates) "
                       "VALUES (?, ?, ?, 'ok', NULL, ?, ?, ?)", (d, r["rule"], r["selected_at"], "m1", json.dumps(r["selected"]), json.dumps(r["candidates"])))
    ledger.commit()
    conn.close()
    return tmp_path / "ncaab.sqlite", tmp_path / "ledger.sqlite"


def test_final_analysis_refused_before_the_stop_and_status_has_counts_only(tmp_path, monkeypatch):
    store_path, ledger_path = make_world(tmp_path, monkeypatch)
    out = tmp_path.parent / (tmp_path.name + "-report")
    now = datetime(2026, 11, 25, tzinfo=timezone.utc)
    with pytest.raises(pc.Refused, match="stop"):
        pn.run(ledger_path, tmp_path, out, now=now, mode="final")
    st = pn.run(ledger_path, tmp_path, out, now=now, mode="status")
    assert st["qualifying_days"] == 2 and st["p0_units"] == 4 and st["priced_candidates"] == 4
    assert st["stop_reached"] is False
    assert not [k for k in st if "close" in k]                           # no close is read before the stop (N3)
    blob = json.dumps(st)
    for banned in ("clv", "excess", "hit_rate", "slope", "mean_", "p_value", "wins"):
        assert banned not in blob.lower().replace("close_", "").replace("p0_close", "")
    assert (out / "ncaab-top4-status.json").is_file()


def test_status_never_reads_closes(tmp_path, monkeypatch):
    store_path, ledger_path = make_world(tmp_path, monkeypatch)
    monkeypatch.setattr(pn, "close_for", lambda *a, **k: pytest.fail("status mode must not read a close"))
    pn.run(ledger_path, tmp_path, tmp_path.parent / (tmp_path.name + "-r3"), now=datetime(2026, 11, 25, tzinfo=timezone.utc), mode="status")


def test_status_never_reads_scores(tmp_path, monkeypatch):
    store_path, ledger_path = make_world(tmp_path, monkeypatch)
    c = sqlite3.connect(store_path)
    c.execute("CREATE TRIGGER no_peek BEFORE UPDATE ON games BEGIN SELECT 1; END")     # harmless marker
    c.close()
    seen = []
    orig = pn.settle_unit
    monkeypatch.setattr(pn, "settle_unit", lambda *a, **k: seen.append(1) or orig(*a, **k))
    pn.run(ledger_path, tmp_path, tmp_path.parent / (tmp_path.name + "-r2"), now=datetime(2026, 11, 25, tzinfo=timezone.utc), mode="status")
    assert seen == []


def test_run_refuses_live_paths(tmp_path):
    with pytest.raises(pc.Refused):
        pn.run(pc.LIVE_LEDGER, tmp_path, tmp_path.parent / "x", now=ASOF, mode="status")
    with pytest.raises(pc.Refused):
        pn.run(tmp_path / "l.sqlite", pc.LIVE_DATA, tmp_path.parent / "x", now=ASOF, mode="status")


def test_analyze_full_pipeline_hand_values(tmp_path, monkeypatch):
    store_path, ledger_path = make_world(tmp_path, monkeypatch, nday=2)
    ledger = sqlite3.connect(ledger_path)
    ledger.row_factory = sqlite3.Row
    store = sqlite3.connect(store_path)
    store.row_factory = sqlite3.Row
    cl = pn.classify(pn.load_rows(ledger), asof=datetime(2026, 11, 25, tzinfo=timezone.utc))
    rep = pn.analyze(cl.counted, store, ledger, now=datetime(2026, 11, 25, tzinfo=timezone.utc))
    p0 = rep["policies"]["P0"]
    # per day: game 1 (q .60, close .62, home wins) CLV +.02 excess +.40; game 2 (q .50, close .48, home loses) CLV -.02 excess -.50
    assert p0["n_picks"] == 4 and p0["n_decided"] == 4 and p0["n_days"] == 2
    assert p0["mean_clv_pp"] == pytest.approx(0.0, abs=1e-9) and p0["mean_excess_pp"] == pytest.approx(-5.0)
    assert p0["hit_rate"] == 0.5 and rep["policies"]["P7"]["n_picks"] == 0
    assert rep["policies"]["P3"]["n_picks"] == 4 and rep["policies"]["P4"]["n_picks"] == 4
    assert rep["policies"]["P1"]["replicates"] == 1000 and rep["close_coverage_p0"] == 1.0 and rep["limitations"] == []
    hs = rep["hypotheses"]
    assert hs["H1"]["theta"] == pytest.approx(0.0, abs=1e-12) and hs["H2"]["theta"] == pytest.approx(-0.05)
    assert set(rep["holm_primary"]) == {"H1", "H2"} and set(rep["holm_secondary"]) == {"H3", "H4", "H5"}
    assert rep["integrity"]["q_sel_mismatch"] == 0
    # P1 with 2 priced candidates per day draws both every time: it equals P0 exactly
    assert rep["policies"]["P1"]["mean_clv_pp_of_replicates"] == pytest.approx(0.0, abs=1e-9)


def _world_rows(tmp_path, monkeypatch, nday=1):
    store_path, ledger_path = make_world(tmp_path, monkeypatch, nday=nday)
    ledger = sqlite3.connect(ledger_path)
    ledger.row_factory = sqlite3.Row
    store = sqlite3.connect(store_path)
    store.row_factory = sqlite3.Row
    return pn.load_rows(ledger), store, ledger


def test_q_sel_mismatch_excludes_the_whole_day_at_classification(tmp_path, monkeypatch):
    rows, store, _ = _world_rows(tmp_path, monkeypatch, nday=2)
    rows[0]["candidates"][0]["markets"][0]["book"] = 0.61               # stored q_sel no longer equals the quote's 0.60
    monkeypatch.setattr(pn, "settle_unit", lambda *a, **k: pytest.fail("no result may be read while classifying"))
    monkeypatch.setattr(pn, "close_for", lambda *a, **k: pytest.fail("no close may be read while classifying"))
    cl = pn.classify(rows, asof=datetime(2026, 11, 25, tzinfo=timezone.utc), store=store)
    assert [d["et_date"] for d in cl.qualifying] == ["2026-11-19"] and cl.excluded["q_sel integrity failure"] == 1


def test_missing_quote_is_an_integrity_failure_and_side_b_is_complemented(tmp_path, monkeypatch):
    rows, store, _ = _world_rows(tmp_path, monkeypatch, nday=2)
    rows[0]["candidates"][0]["markets"][0]["quote_id"] = 99999
    e = rows[1]["candidates"][0]["markets"][0]                          # stored q_sel of a side b entry = 1 - p_a_devig
    e["side"], e["book"] = "b", 0.40
    cl = pn.classify(rows, asof=datetime(2026, 11, 25, tzinfo=timezone.utc), store=store)
    assert cl.qualifying and [d["et_date"] for d in cl.qualifying] == ["2026-11-19"] and cl.excluded["q_sel integrity failure"] == 1


def test_only_entries_evaluated_by_a_policy_are_checked(tmp_path, monkeypatch):
    rows, store, _ = _world_rows(tmp_path, monkeypatch, nday=1)
    # a lower, never evaluated entry (not the best, not the largest app, not a winner) with a wrong q_sel
    rows[0]["candidates"][0]["markets"].append(ent("total", "a", 140.5, 0.30, 0.20, 1))
    cl = pn.classify(rows, asof=datetime(2026, 11, 25, tzinfo=timezone.utc), store=store)
    assert len(cl.qualifying) == 1 and "q_sel integrity failure" not in cl.excluded


def test_analysis_still_counts_every_mismatch(tmp_path, monkeypatch):
    rows, store, ledger = _world_rows(tmp_path, monkeypatch, nday=1)
    rows[0]["candidates"][0]["markets"][0]["book"] = 0.61
    cl = pn.classify(rows, asof=datetime(2026, 11, 25, tzinfo=timezone.utc))                # without a store: no day exclusion
    rep = pn.analyze(cl.counted, store, ledger, now=datetime(2026, 11, 25, tzinfo=timezone.utc))
    assert rep["integrity"]["q_sel_mismatch"] >= 1


def test_h3_and_h1_not_testable_without_any_p0_clv(tmp_path, monkeypatch):
    rows, store, ledger = _world_rows(tmp_path, monkeypatch, nday=2)
    for r in rows:                                    # another sportsbook: there is no same-book close, hence no CLV
        for c in r["candidates"]:
            for e in c["markets"]:
                e["provider"] = "ESPN BET"
    cl = pn.classify(rows, asof=datetime(2026, 11, 25, tzinfo=timezone.utc))
    rep = pn.analyze(cl.counted, store, ledger, now=datetime(2026, 11, 25, tzinfo=timezone.utc))
    assert rep["hypotheses"]["H1"]["theta"] is None and rep["hypotheses"]["H1"]["p"] == 1.0
    assert rep["hypotheses"]["H3"]["p"] == 1.0 and rep["hypotheses"]["H3"]["testable"] is False


def test_pending_game_on_any_counted_day_blocks_the_look(tmp_path, monkeypatch):
    store_path, ledger_path = make_world(tmp_path, monkeypatch, nday=2)
    c = sqlite3.connect(store_path)
    c.execute("UPDATE games SET status = 'scheduled', home_score = NULL, away_score = NULL WHERE game_id = 'ncaab:700'")   # first day, not the last
    c.commit()
    c.close()
    monkeypatch.setattr(pn, "STOP_DAYS", 2)
    with pytest.raises(pc.Refused, match="neither final nor past 7 days"):
        pn.run(ledger_path, tmp_path, tmp_path.parent / (tmp_path.name + "-r4"), now=datetime(2026, 11, 21, tzinfo=timezone.utc), mode="final",
               looks_dir=tmp_path.parent / (tmp_path.name + "-looks"))


def test_final_look_is_stored_once_and_returned_verbatim(tmp_path, monkeypatch):
    store_path, ledger_path = make_world(tmp_path, monkeypatch, nday=2)
    monkeypatch.setattr(pn, "STOP_DAYS", 2)
    kw = dict(mode="final", looks_dir=tmp_path.parent / (tmp_path.name + "-looks2"))
    first = pn.run(ledger_path, tmp_path, tmp_path.parent / (tmp_path.name + "-r5"), now=datetime(2026, 11, 21, tzinfo=timezone.utc), **kw)
    c = sqlite3.connect(store_path)
    c.execute("UPDATE games SET home_score = 0 WHERE game_id = 'ncaab:700'")        # a changed result must not change the stored look
    c.commit()
    c.close()
    again = pn.run(ledger_path, tmp_path, tmp_path.parent / (tmp_path.name + "-r6"), now=datetime(2026, 11, 22, tzinfo=timezone.utc), **kw)
    assert json.dumps(again, sort_keys=True, default=str) == json.dumps(first, sort_keys=True, default=str)


def test_no_selection_row_days_after_the_last_counted_day_are_not_counted():
    c = [pricedcand("g1", TIP, 5.0)]
    rows = [row("2026-11-18", candidates=c), row("2026-11-20", "2026-11-20T16:00:00Z", candidates=c),
            row("2026-11-21", "2026-11-21T16:00:00Z", candidates=c)]
    cl = pn.classify(rows, asof=ASOF, stop_days=2)
    assert cl.excluded["no selection row"] == 1                         # 2026-11-19 only; nothing after the stop day
    assert cl.excluded["after 60th qualifying day"] == 1


def test_audit_2b_structured_records_only(tmp_path, monkeypatch):
    from app import published_picks
    store_path, ledger_path = make_world(tmp_path, monkeypatch, nday=1)
    ledger = sqlite3.connect(ledger_path)
    ledger.row_factory = sqlite3.Row
    ledger.executescript("CREATE TABLE deliveries (delivery_key TEXT PRIMARY KEY, chat_id TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL, "
                         "attempted_at TEXT NOT NULL, next_attempt_at TEXT, message_id INTEGER, error TEXT);" + published_picks.SCHEMA)
    for k, st in (("k1", "sent"), ("k2", "failed")):
        ledger.execute("INSERT INTO deliveries VALUES (?, 'c', 'a card text that is never parsed', ?, '2026-11-18T20:00:00Z', NULL, 1, NULL)", (k, st))
    ins = "INSERT INTO published_picks (delivery_key, sport, game_id, market, forecast_id, side, line, probability, sent_at, basis) VALUES (?, 'ncaab', ?, ?, '1', ?, ?, .6, '2026-11-18T20:00:00Z', ?)"
    ledger.execute(ins, ("k1", "ncaab:700", "winner", "a", None, "live"))         # matches the selection entry
    ledger.execute(ins, ("k2", "ncaab:701", "winner", "a", None, "live"))         # delivery failed: not a delivered card
    ledger.commit()
    cl = pn.classify(pn.load_rows(ledger), asof=datetime(2026, 11, 25, tzinfo=timezone.utc))
    a = pn.audit(ledger, cl.counted)
    assert a == {"p0_games": 2, "delivered_matches_selection": 1, "delivered_differs_from_selection": 0,
                 "no_structured_delivery_record": 1, "delivered_pick_not_recorded": 0}
    ledger.execute(ins, ("k1", "ncaab:701", "spread", "b", -3.5, "live"))         # a sent card whose pick differs from the entry
    ledger.commit()
    a = pn.audit(ledger, cl.counted)
    assert a["delivered_differs_from_selection"] == 1 and a["no_structured_delivery_record"] == 0


def test_audit_2b_best_picks_and_non_live_rows_are_delivered_but_not_recorded(tmp_path, monkeypatch):
    from app import best_pick_record, published_picks
    store_path, ledger_path = make_world(tmp_path, monkeypatch, nday=1)
    ledger = sqlite3.connect(ledger_path)
    ledger.row_factory = sqlite3.Row
    ledger.executescript("CREATE TABLE deliveries (delivery_key TEXT PRIMARY KEY, chat_id TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL, "
                         "attempted_at TEXT NOT NULL, next_attempt_at TEXT, message_id INTEGER, error TEXT);" + published_picks.SCHEMA + best_pick_record.SCHEMA)
    for k in ("k1", "k2"):
        ledger.execute("INSERT INTO deliveries VALUES (?, 'c', 'x', 'sent', '2026-11-18T20:00:00Z', NULL, 1, NULL)", (k,))
    ledger.execute("INSERT INTO best_picks (delivery_key, recorded_at, written_at, sport, game_id, status, recorder_version, rule_applies, picture) "
                   "VALUES ('k1', 't', 't', 'ncaab', 'ncaab:700', 'ok', 'v', 1, '{}')")
    ledger.execute("INSERT INTO published_picks (delivery_key, sport, game_id, market, forecast_id, side, line, probability, sent_at, basis) "
                   "VALUES ('k2', 'ncaab', 'ncaab:701', 'winner', '1', 'a', NULL, .6, 't', 'backfill')")
    ledger.commit()
    cl = pn.classify(pn.load_rows(ledger), asof=datetime(2026, 11, 25, tzinfo=timezone.utc))
    a = pn.audit(ledger, cl.counted)
    assert a == {"p0_games": 2, "delivered_matches_selection": 0, "delivered_differs_from_selection": 0,
                 "no_structured_delivery_record": 0, "delivered_pick_not_recorded": 2}


# ---- H4 / H5 slopes and bootstrap mechanics -------------------------------------------------------------------------------
def test_slope_test_hand_values_positive_flat_and_negative():
    import numpy as np
    days = [[(1.0, 0.01), (2.0, 0.02)], [(3.0, 0.03), (4.0, 0.05)], [(5.0, 0.05), (6.0, 0.07)]]
    xs = [x for d in days for x, _ in d]
    ys = [y for d in days for _, y in d]
    pos = pn._slope_test(days)
    assert pos["theta"] == pytest.approx(np.polyfit(xs, ys, 1)[0]) and pos["n"] == 6
    assert pos["p"] == pytest.approx(1 / 10001) and pos["ci95"][0] > 0          # every replicate has x variance and a positive slope
    neg = pn._slope_test([[(x, -y) for x, y in d] for d in days])
    assert neg["theta"] < 0 and neg["p"] == 1.0 and neg["ci95"][1] < 0
    flat = pn._slope_test([[(x, 0.5) for x, _ in d] for d in days])
    assert flat["theta"] == pytest.approx(0.0, abs=1e-12) and flat["p"] > 0.9    # a zero slope is "<= 0" in every replicate
    assert pn._slope_test([[(1.0, 0.1)], [(1.0, 0.2)]])["theta"] is None       # no x variance: undefined, p = 1
    assert pn._slope_test([[(1.0, 0.1)], [(1.0, 0.2)]])["p"] == 1.0


def test_mixed_sign_test_mean_p_matches_an_independent_loop():
    import numpy as np
    sums, cnts = [0.03, -0.01, 0.02, -0.04], [2, 1, 2, 1]
    num, den = np.array(sums), np.array(cnts, float)
    rng = np.random.default_rng(20261118)
    idx = rng.integers(0, 4, size=(20, 4))
    expect = 0
    for rep in idx:
        n = sum(sums[i] for i in rep)
        d = sum(cnts[i] for i in rep)
        expect += (n / d) <= 0
    p_loop = (1 + expect) / 21
    from app import prereg_common as pcm
    th = pcm.boot_ratio(num, den, B=20)
    assert pcm.p_le_zero(th) == pytest.approx(p_loop) and 1 / 21 < p_loop < 1.0     # a genuinely mixed outcome, not degenerate
