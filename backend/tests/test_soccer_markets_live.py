"""Live goal/corner forecasts, their reads and settlement (app/soccer_markets_live.py) and their ticket path.

SYNTHETIC history in the real ESPN format (test_soccer_markets_eval.make_event; the template is a real 2026-11-08 event),
imported through the real ingestion. The sealed candidate is a test seal pinned only inside these tests (the real one is
pinned after the reserved test). Times are the real clock: the ledger refuses backdated rows, so the forecast matches are
placed tomorrow (UTC)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import backtest, db, soccer, soccer_live, soccer_markets as sm, soccer_markets_eval as ev, soccer_markets_live as sml, tickets
from test_soccer_markets_eval import RULES, STRENGTH, make_event, payload, synthetic_league

NOW = datetime.now(timezone.utc)
TODAY, TOMORROW, YESTERDAY = NOW.date(), NOW.date() + timedelta(days=1), NOW.date() - timedelta(days=1)
STAMP = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
SEASON = TODAY.year if NOW.month >= 7 else TODAY.year - 1
STYPE = 5000 + SEASON
IDS = list(STRENGTH)


def day_payload(day, events):
    return lambda *_: payload("eng.1", events)


def ingest_day(c, day, events, at=STAMP):
    soccer.ingest_day(c, "eng.1", day.strftime("%Y%m%d"), fetch=day_payload(day, events), retrieved_at=at)


def final(eid, day, h, a, hs, as_, hc=5, ac=4):
    return make_event(eid, f"{day.isoformat()}T15:00Z", SEASON, STYPE, f"{SEASON}-league", h, a, hs, as_, hc, ac)


def upcoming(eid, day, h, a, hhmm="15:00"):
    return make_event(eid, f"{day.isoformat()}T{hhmm}Z", SEASON, STYPE, f"{SEASON}-league", h, a, status="STATUS_SCHEDULED")


def make_seal():
    return backtest.seal({"family": "goals", "model": "dixon_coles", "config": {"half_life_days": 365, "tau": 3, "D": 0.1},
                          "extra": {"rho": -0.05}, "frequency": {}, "protocol_sha256": ev.protocol_sha256(),
                          "code": ev.code_digest()})


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOCCER_MARKETS_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(ev, "P", {**ev.P, "amendments": [RULES]})
    monkeypatch.setattr(soccer_live, "SEASON", SEASON)
    monkeypatch.setattr(soccer_live, "SEASON_FETCH_FROM", YESTERDAY.isoformat())
    monkeypatch.setattr(soccer_live, "LAUNCH", ("eng.1",))
    monkeypatch.setattr(sml, "INPUTS", ("eng.1",))
    monkeypatch.setattr(sml, "MIN_HISTORY_LEAGUE", 30)
    seals = {}
    for family in ("goals", "corners"):
        s = make_seal() if family == "goals" else backtest.seal({**{k: v for k, v in make_seal().items() if k != "sha256"},
                                                                 "family": "corners", "model": "negative_binomial",
                                                                 "extra": {"k": 12.0}})
        seals[family] = s
        (tmp_path / f"soccer_markets_sealed_{family}.json").write_text(json.dumps(s))
        (tmp_path / f"soccer_markets_reading_{family}.json").write_text(json.dumps(
            {"candidate_sha256": s["sha256"], "verdict": "serve", "served_competitions": ["eng.1"],
             "served_markets": [sml.PROTOCOL_NAME[m] for m in sml.FAMILY_MARKETS[family]], "R4_disclosed_lines": []}))
    monkeypatch.setattr(sml, "HERE", tmp_path)
    monkeypatch.setattr(sml, "CANDIDATES", {f: s["sha256"] for f, s in seals.items()})
    c = db.connect(sport="soccer")
    db.migrate(c)
    db.claim_store(c, "soccer")
    synthetic_league(c, seasons=range(SEASON - 2, SEASON))
    ingest_day(c, YESTERDAY, [final(90001, YESTERDAY, IDS[0], IDS[1], 2, 1, 7, 3), final(90002, YESTERDAY, IDS[2], IDS[3], 0, 0)])
    ingest_day(c, TODAY, [])
    ingest_day(c, TOMORROW, [upcoming(90003, TOMORROW, IDS[0], IDS[2]), upcoming(90004, TOMORROW, IDS[4], IDS[5], "19:30")])
    yield c
    c.close()


def forecasts(c, family=None):
    q = "SELECT * FROM soccer_market_forecasts" + (" WHERE family = ?" if family else "") + " ORDER BY forecast_id"
    return c.execute(q, (family,) if family else ()).fetchall()


def test_complete_inputs_record_append_only_forecasts_once(live):
    rep = sml.run(live, day=TOMORROW.isoformat())
    assert rep["status"] == "ok", rep
    rows = forecasts(live)
    assert {(r["match_id"], r["family"]) for r in rows} == {(f"soccer:eng.1:{e}", f) for e in (90003, 90004)
                                                            for f in ("goals", "corners")}
    for r in rows:
        grid = json.loads(r["joint_json"])
        assert len(grid) == ev.CAP[r["family"]] + 1 and abs(sum(map(sum, grid)) - 1) < 1e-9
        assert r["generated_at"] <= r["cutoff_at"] == f"{TOMORROW.isoformat()}T06:00:00Z"
    assert sml.run(live, day=TOMORROW.isoformat())["status"] == "ok" and len(forecasts(live)) == 4    # idempotent
    with pytest.raises(Exception, match="append-only"):
        live.execute("UPDATE soccer_market_forecasts SET mean_home = 1")


def test_the_live_forecast_is_the_evaluated_forecast(live):
    sml.run(live, day=TOMORROW.isoformat())
    data = ev.collect(live, "goals", SEASON)
    targets = sml.targets_on(live, ("eng.1",), TOMORROW.isoformat())
    expected = {r["mid"]: r for r in ev.forecast_targets(make_seal(), data, targets)}
    for r in forecasts(live, "goals"):
        e = expected[r["match_id"]]
        assert (r["mean_home"], r["mean_away"]) == (e["mh"], e["ma"])
        assert json.loads(r["joint_json"]) == [[float(x) for x in row] for row in e["joint"]]


def test_corners_wait_for_every_current_count_and_goals_are_unaffected(live):
    ingest_day(live, YESTERDAY, [final(90001, YESTERDAY, IDS[0], IDS[1], 2, 1, 7, 3), final(90002, YESTERDAY, IDS[2], IDS[3], 0, 0),
                                 final(90005, YESTERDAY, IDS[6], IDS[7], 1, 1, None, None)])
    rep = sml.run(live, day=TOMORROW.isoformat())
    assert rep["families"]["goals"]["status"] == "ok"
    corners = rep["families"]["corners"]
    assert corners["status"] == "refused" and "no corner count" in " ".join(corners["competitions"]["eng.1"]["problems"])
    assert {r["family"] for r in forecasts(live)} == {"goals"}
    chk = sml.latest_checks(live)
    assert chk["corners"]["eng.1"]["ok"] is False and chk["goals"]["eng.1"]["ok"] is True


def test_stale_inputs_refuse_and_record_nothing(live, monkeypatch):
    monkeypatch.setattr(soccer_live, "MAX_AGE", timedelta(seconds=-1))
    rep = sml.run(live, day=TOMORROW.isoformat())
    assert rep["status"] == "refused" and not forecasts(live)
    assert all("older than 26 h" in " ".join(v["competitions"]["eng.1"]["problems"]) for v in rep["families"].values())


def test_an_unpinned_or_unread_family_is_not_served(live, monkeypatch):
    monkeypatch.setattr(sml, "CANDIDATES", {})
    assert sml.state("goals")["served"] is False and "No tested model" in sml.state("goals")["reason"]
    assert sml.run(live, day=TOMORROW.isoformat())["families"]["goals"]["status"] == "not_served"
    assert not forecasts(live)
    monkeypatch.undo()


def test_a_failed_reading_serves_nothing(live, tmp_path, monkeypatch):
    s = json.loads((tmp_path / "soccer_markets_sealed_goals.json").read_text())
    (tmp_path / "soccer_markets_reading_goals.json").write_text(json.dumps({"candidate_sha256": s["sha256"],
                                                                             "verdict": "do_not_serve"}))
    st = sml.state("goals")
    assert not st["served"] and "did not support" in st["reason"]


def test_no_forecast_after_the_cutoff(live):
    assert sml.run(live, day=YESTERDAY.isoformat())["status"] == "refused"


def test_selections_separate_bookmaker_offers_from_model_lines(live):
    sml.run(live, day=TOMORROW.isoformat())
    mid = "soccer:eng.1:90003"
    raw = live.execute("SELECT raw_id FROM raw_responses ORDER BY raw_id DESC LIMIT 1").fetchone()[0]
    for side, american in (("over", 120), ("under", -150)):
        live.execute("INSERT INTO soccer_odds_quotes (match_id, provider, market, side, price_kind, offered, line, american, "
                     "decimal_odds, retrieved_at, raw_id, parser_version) VALUES (?, 'DraftKings', 'total_goals', ?, 'current', "
                     "1, 2.5, ?, ?, ?, ?, 'test')", (mid, side, american, 1 + (american / 100 if american > 0 else 100 / -american),
                                                     STAMP, raw))
    m = next(x for x in sml.match_markets(live) if x["match_id"] == mid)
    goals = m["families"]["goals"]["selections"]
    book = [s for s in goals if s["source"] == "bookmaker"]
    assert {(s["side"], s["line"], s["american"]) for s in book} == {("over", 2.5, 120), ("under", 2.5, -150)}
    assert all(s["note"] == sml.BOOKMAKER_LINE and s["market_probability"] is not None for s in book)
    over = next(s for s in book if s["side"] == "over")
    assert over["expected_return"] == pytest.approx(over["probabilities"]["win"] * 2.2)
    corners = m["families"]["corners"]["selections"]
    assert corners and all(s["source"] == "model" and s["note"] == sml.MODEL_LINE for s in corners)
    assert {s["market"] for s in corners} == set(sml.FAMILY_MARKETS["corners"])
    fair = [s for s in goals if s["market"] == "goals_total" and s["source"] == "model"]
    assert len(fair) == 2 and fair[0]["line"] == fair[1]["line"] and (fair[0]["line"] * 4) % 1 == 0
    assert m["derived"] is None        # no 1X2 forecast stored for it: nothing is derived (test_soccer_live covers DC/DNB)
    rk = sml.ranking([m])
    assert rk == sorted(rk, key=lambda s: (-s["win_share"], s["kickoff_at"], s["match_id"], s["market"], s["side"]))


def test_settlement_follows_the_stored_forecast_and_the_counts(live):
    sml.run(live, day=TOMORROW.isoformat())
    g = next(r for r in forecasts(live, "goals") if r["match_id"].endswith("90003"))
    c = next(r for r in forecasts(live, "corners") if r["match_id"].endswith("90003"))
    assert sml.grade(live, g["forecast_id"], "goals_total", "over", 2.5, STAMP)["grade"] == "pending"
    with pytest.raises(ValueError):
        sml.grade(live, g["forecast_id"], "corners_total", "over", 9.5, STAMP)      # a goals row never settles corners
    later = (NOW + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    done = [final(90003, TOMORROW, IDS[0], IDS[2], 2, 1, 6, 4)]
    ingest_day(live, TOMORROW, done, at=(datetime.combine(TOMORROW, datetime.min.time(), timezone.utc)
                                         + timedelta(hours=18)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert sml.grade(live, g["forecast_id"], "goals_total", "over", 2.5, later)["grade"] == "win"
    assert sml.grade(live, g["forecast_id"], "goals_total", "over", 3.0, later)["grade"] == "push"
    assert sml.grade(live, g["forecast_id"], "goals_total", "over", 3.25, later)["grade"] == "half_loss"
    assert sml.grade(live, g["forecast_id"], "btts", "yes", None, later)["grade"] == "win"
    assert sml.grade(live, c["forecast_id"], "corners_handicap", "home", -2.0, later)["grade"] == "push"
    assert sml.grade(live, c["forecast_id"], "corners_total", "over", 9.75, later)["grade"] == "half_win"
    hist = sml.history(live, as_of=later)
    assert hist["families"]["goals"]["settled"] == 1 and hist["families"]["goals"]["record"]["goals_total_over@2.5"]["n"] == 1


def test_postponed_matches_are_void(live):
    sml.run(live, day=TOMORROW.isoformat())
    g = next(r for r in forecasts(live, "goals") if r["match_id"].endswith("90004"))
    ev_ = upcoming(90004, TOMORROW, IDS[4], IDS[5], "19:30")
    ev_["competitions"][0]["status"]["type"].update(name="STATUS_POSTPONED", state="post")
    ingest_day(live, TOMORROW, [upcoming(90003, TOMORROW, IDS[0], IDS[2]), ev_])
    assert sml.grade(live, g["forecast_id"], "goals_home", "over", 0.5, STAMP)["grade"] == "void"


def test_tickets_offer_family_selections_with_their_price_source_and_settle_them(live, monkeypatch):
    from app import intl_live
    monkeypatch.setattr(intl_live, "LAUNCH", ())
    soccer_live_ok = soccer_live.run
    monkeypatch.setattr(soccer_live, "clubs", lambda: {"version": "t", "espn": {}})
    sml.run(live, day=TOMORROW.isoformat())
    picks = tickets.soccer_market_picks(live, STAMP)
    fam = [p for p in picks if p["market"] in sm.COUNT_MARKETS and p["game_id"].endswith("90003")]
    assert {p["market"] for p in fam} == set(sml.FAMILY_MARKETS["goals"]) | set(sml.FAMILY_MARKETS["corners"])
    for p in fam:
        assert isinstance(p["forecast_id"], str) and p["forecast_id"].startswith(("goals:", "corners:"))
        assert p["quote_check"]["line_source"] == "model" and p["skill_proven"] is False
        assert p["probability"] == p["outcome_probabilities"]["win"] and p["pick_label"].endswith("(90 min)")
        if p["line"] is not None:
            assert p["line"] % 1 == 0.5 or p["market"] == "corners_handicap"
    kickoff = datetime.fromisoformat(f"{TOMORROW.isoformat()}T15:00:00+00:00")
    p = next(p for p in fam if p["market"] == "goals_total")
    assert tickets.eligible(p, kickoff - timedelta(hours=1))
    assert tickets.grade_leg({**p, "sport": "soccer"}, STAMP)["grade"] == "pending"
    a = tickets.leg_assessment("soccer", p)
    assert a["decimal"] is None and a["skill_proven"] is False
    mixed = [{**p, "sport": "soccer"}, {**fam[1], "sport": "soccer", "game_id": "other"}, {"sport": "nba", "forecast_id": 7}]
    assert tickets.normalized_ticket({"legs": mixed}, "2026-11-09")["ticket_id"].startswith("2026-11-09-")
    text = tickets.ticket_text("2026-11-09", [{"label": "Goals", "ticket_id": "x", "stake_usd": 1, "legs": [
        {**p, "sport": "soccer"}, {**p, "game_id": "g2", "forecast_id": 99, "sport": "nba", "matchup": "A @ B",
                                   "market": "winner", "quote_check": {}}]}])
    assert "draw no bet and whole lines push" in text and "no sportsbook line stored" in text


def test_integer_ticket_signatures_are_unchanged():
    legs = [{"sport": "nba", "forecast_id": 12}, {"sport": "nba", "forecast_id": 3}, {"sport": "nhl", "forecast_id": 40}]
    old = tickets.sha(json.dumps(tuple(sorted((p["sport"], p["forecast_id"]) for p in legs))).encode())
    assert tickets.normalized_ticket({"legs": legs}, "2026-11-09")["signature"] == old


def test_partial_ticket_grade_for_split_stakes():
    assert tickets.ticket_grade([{"grade": "win"}, {"grade": "half_loss"}]) == "partial"
    assert tickets.ticket_grade([{"grade": "half_win"}, {"grade": "push"}]) == "partial"
    assert tickets.ticket_grade([{"grade": "half_win"}, {"grade": "loss"}]) == "loss"
    assert tickets.ticket_grade([{"grade": "win"}, {"grade": "push"}]) == "win"


def _client(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setenv("NBA_API_MODE", "real")
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    return TestClient(main.app)


def test_the_markets_api_serves_stored_selections_and_says_what_is_not_served(live, monkeypatch):
    client = _client(monkeypatch)
    get = lambda path, **params: client.get(path, params=params).json()
    empty = get("/api/soccer/markets", competition="eng.1")
    assert empty["matches"] == [] and empty["families"]["goals"]["served"] is True
    assert "No proven skill" in empty["notes"]["no_proven_skill"] and "corner" in empty["notes"]["corners_no_bookmaker"]
    sml.run(live, day=TOMORROW.isoformat())
    d = get("/api/soccer/markets", competition="eng.1")
    assert {m["match_id"] for m in d["matches"]} == {"soccer:eng.1:90003", "soccer:eng.1:90004"}
    m = d["matches"][0]
    assert set(m["families"]) == {"goals", "corners"} and all(s["source"] == "model" for f in m["families"].values()
                                                              for s in f["selections"])
    assert d["ranking"] and d["ranking"][0]["win_share"] >= d["ranking"][-1]["win_share"]
    monkeypatch.setattr(sml, "CANDIDATES", {})
    off = get("/api/soccer/markets", competition="eng.1")
    assert off["matches"] == [] and off["families"]["corners"]["served"] is False
    assert "No tested model" in off["families"]["corners"]["reason"]
    h = get("/api/soccer/markets/history", competition="eng.1")
    assert h["competition"] == "eng.1" and h["families_state"]["goals"]["served"] is False


def test_the_png_card_and_text_render_soccer_market_legs(live, monkeypatch, tmp_path):
    from PIL import ImageDraw
    from app import telegram_card
    sml.run(live, day=TOMORROW.isoformat())
    picks = {p["market"]: p for p in tickets.soccer_market_picks(live, STAMP) if p["game_id"].endswith("90003")}
    other = {p["market"]: p for p in tickets.soccer_market_picks(live, STAMP) if p["game_id"].endswith("90004")}
    legs = [{**picks["corners_total"], "sport": "soccer", "rationale": "r"}, {**other["btts"], "sport": "soccer", "rationale": "r"}]
    texts, original = [], ImageDraw.ImageDraw.text
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", lambda self, xy, text, *a, **kw: (texts.append(text), original(self, xy, text, *a, **kw))[1])
    ticket = {"label": "Soccer markets", "ticket_id": "2026-11-09-abc", "legs": legs, "assessment": tickets.ticket_assessment(legs)}
    png = telegram_card.render_ticket_card("2026-11-09", [ticket])
    (tmp_path / "card.png").write_bytes(png)
    assert png.startswith(b"\x89PNG")
    assert any("Match corners (90 min)" in t for t in texts) and any("Both teams to score" in t for t in texts)
    assert any(picks["corners_total"]["pick_label"] in t for t in texts)
    text = tickets.ticket_text("2026-11-09", [ticket])
    assert picks["corners_total"]["pick_label"] in text and "Payout unknown" in text


def test_a_both_zero_corner_count_never_settles_and_never_blocks_the_family(live):
    ingest_day(live, YESTERDAY, [final(90001, YESTERDAY, IDS[0], IDS[1], 2, 1, 7, 3), final(90002, YESTERDAY, IDS[2], IDS[3], 0, 0),
                                 final(90006, YESTERDAY, IDS[6], IDS[7], 1, 1, 0, 0)])
    rep = sml.run(live, day=TOMORROW.isoformat())
    assert rep["families"]["corners"]["status"] == "ok"               # delivered (0-0), only not used as an input
    c = next(r for r in forecasts(live, "corners") if r["match_id"].endswith("90003"))
    later = (NOW + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ingest_day(live, TOMORROW, [final(90003, TOMORROW, IDS[0], IDS[2], 2, 1, 0, 0)],
               at=(datetime.combine(TOMORROW, datetime.min.time(), timezone.utc) + timedelta(hours=18)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    g = sml.grade(live, c["forecast_id"], "corners_total", "under", 9.5, later)
    assert g["grade"] == "unscorable" and g["counts"] is None


def test_ticket_market_picks_only_cover_matches_in_the_api_window(live):
    # Review MEDIUM 1 (Nov 9): every snapshot recomputed every stored goal/corner forecast ever (no date window).
    sml.run(live, day=TOMORROW.isoformat())
    now_picks = tickets.soccer_market_picks(live, STAMP)
    assert {p["game_id"] for p in now_picks} >= {"soccer:eng.1:90003", "soccer:eng.1:90004"}
    later = (NOW + timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")       # tomorrow's kickoffs are now 3 days old
    assert tickets.soccer_market_picks(live, later) == []
    calls, real = [], sml.match_markets
    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(sml, "match_markets", lambda conn, **kw: (calls.append(kw), real(conn, **kw))[1])
    try:
        tickets.soccer_market_picks(live, STAMP)
    finally:
        mp.undo()
    since = (datetime.strptime(STAMP, "%Y-%m-%dT%H:%M:%SZ") - timedelta(days=sml.TICKET_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert calls and calls[0]["since"] == since and calls[0]["as_of"] == STAMP


def test_picture_and_caption_carry_line_source_no_proven_skill_and_push_for_market_legs(live, monkeypatch):
    # Review MEDIUM 2 (Nov 9): in picture-only delivery the text is not sent, so the card and caption must say it.
    from PIL import ImageDraw
    from app import telegram_card
    sml.run(live, day=TOMORROW.isoformat())
    picks = {p["market"]: p for p in tickets.soccer_market_picks(live, STAMP) if p["game_id"].endswith("90003")}
    model_leg = {**picks["corners_total"], "sport": "soccer", "rationale": "r"}
    book_leg = {**picks["goals_total"], "sport": "soccer", "rationale": "r", "game_id": "g-book",
                "quote_check": {"line_source": "bookmaker", "provider": "DraftKings", "price_now": -110, "line_now": 2.5,
                                "retrieved_at": STAMP}}
    dnb_leg = {**model_leg, "market": "draw_no_bet", "market_title": sm.TITLES["draw_no_bet"], "side": "home", "line": None,
               "pick_label": "Team 100 (draw no bet, 90 min)", "game_id": "g-dnb"}
    legs = [model_leg, book_leg, dnb_leg]
    texts, original = [], ImageDraw.ImageDraw.text
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", lambda self, xy, text, *a, **kw: (texts.append(text), original(self, xy, text, *a, **kw))[1])
    ticket = {"label": "Soccer markets", "ticket_id": "2026-11-09-abc", "legs": legs, "assessment": tickets.ticket_assessment(legs)}
    telegram_card.render_ticket_card("2026-11-09", [ticket])
    card = " ".join(texts)
    assert "Model line (no sportsbook line stored)" in card
    assert "DraftKings line" in card
    assert "No proven skill against the bookmaker" in card
    assert "push" in card.lower()
    caption = tickets.ticket_captions({"resolved": [ticket], "date": "2026-11-09"})[0]
    assert "model line" in caption.lower() and "no proven skill against the bookmaker" in caption.lower()
    assert "push" in caption.lower() and len(caption) <= 1024
    one_x_two = {"sport": "soccer", "market": "1x2_regulation", "pick_label": "A win (90 min)", "probability": 0.5,
                 "matchup": "A vs B", "game_id": "x", "forecast_id": 1, "quote_check": {}}
    plain = tickets.ticket_captions({"resolved": [{"label": "x", "legs": [one_x_two, {**one_x_two, "game_id": "y"}]}],
                                     "date": "2026-11-09"})[0]
    assert "no proven skill" not in plain.lower()          # 1X2-only tickets are unchanged
