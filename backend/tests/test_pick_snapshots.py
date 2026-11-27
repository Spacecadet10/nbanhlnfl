"""app/pick_snapshots.py: the colour a pick had BEFORE its start, saved once and never recomputed. Synthetic data; a fake bot only."""
import copy
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import pick_snapshots as ps, telegram as tg, threshold_overview as to
from test_telegram import Bot, NOW, report as tg_report, soccer_report as _soccer_report

T = datetime(2026, 11, 25, 20, tzinfo=timezone.utc)
START = "2026-11-25T21:00:00Z"


def hist(sport="nba", market="winner", pct="60.0", w=3, l=1, p=0, v=0, side="a", src="none", tg=(0, 0, 0, 0), tg_ok=True):
    return {"sport": sport, "market": market, "percentage": pct, "side": side, "model_version": "m1", "source": "primary_recorded",
            "line_source": src, "wins": w, "losses": l, "pushes": p, "voids": v, "settled": w + l, "colour": to.bucket_colour(w, l),
            "colour_rule": to.COLOUR_RULE, "small_sample": True, "as_of": "2026-11-25T20:00:00Z",
            "history": {"wins": 1, "losses": 5, "colour": "red"},
            "telegram": {"available": tg_ok, "reason": None if tg_ok else "no_rows_for_sport", "no_history": (not any(tg)) if tg_ok else None, "wins": tg[0] if tg_ok else None,
                         "losses": tg[1] if tg_ok else None, "pushes": tg[2] if tg_ok else None, "voids": tg[3] if tg_ok else None,
                         "colour": to.bucket_colour(tg[0], tg[1]), "period": {"from": "2026-11-02", "to": "2026-11-16"}}}


def pick(gid="g1", market="winner", fid=1, pct=0.60, line=None, side="a", sport="nba", gen="2026-11-25T19:30:00Z", **kw):
    h = kw.pop("h", None) or hist(sport, market, to.displayed_percentage(pct), side=side)
    return {"game_id": gid, "forecast_id": fid, "market": market, "market_title": market, "matchup": "BOS @ ORL", "tipoff": START,
            "generated_at": gen, "status": "scheduled", "grade": "pending", "pick_label": "ORL win", "probability": pct, "line": line,
            "side": side, "model_version": "m1", "strong": False, "game_rank": None, "percentage_history": h, **kw}


def rep(*picks, sport="nba", at=T):
    return {"sport": sport, "as_of": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "picks": list(picks)}


def rows(sport="nba", **kw):
    return ps.load(sport, **kw)


# --- Recording ---------------------------------------------------------------------------------------------------------
def test_the_first_sight_of_a_pick_saves_its_colour_support_rule_forecast_and_time():
    assert ps.observe("nba", rep(pick(h=hist(tg=(0, 2, 1, 0)))), T) == 1
    r, = rows()
    assert (r["event"], r["version_no"], r["forecast_id"], r["game_id"], r["market"], r["side"], r["percentage"]) == \
        ("seen", 1, "1", "g1", "winner", "a", "60.0")
    assert (r["colour"], r["wins"], r["losses"], r["pushes"], r["voids"], r["colour_rule"]) == ("green", 3, 1, 0, 0, "bucket-record-v1")
    assert (r["recovered_colour"], r["recovered_wins"], r["recovered_losses"], r["recovered_pushes"]) == ("red", 0, 2, 1)
    assert (r["observed_at"], r["start_at"], r["generated_at"], r["basis"]) == ("2026-11-25T20:00:00Z", START, "2026-11-25T19:30:00Z", "live")


def test_a_refresh_writes_nothing_and_a_changed_record_is_a_later_version_that_leaves_the_first_one_alone():
    ps.observe("nba", rep(pick()), T)
    for _ in range(3):
        assert ps.observe("nba", rep(pick()), T + timedelta(minutes=1)) == 0
    assert ps.observe("nba", rep(pick(h=hist(w=3, l=4))), T + timedelta(minutes=5)) == 1      # red by now
    a, b = rows()
    assert (a["version_no"], a["colour"], b["version_no"], b["colour"]) == (1, "green", 2, "red")
    assert ps.observe("nba", rep(pick()), T + timedelta(minutes=9)) == 1                       # back to the first record: a third version
    assert [r["version_no"] for r in rows()] == [1, 2, 3]
    assert ps.original(rows())[("g1", "winner", "1")]["colour"] == "green"


def test_a_re_forecast_is_kept_as_its_own_forecast():
    ps.observe("nba", rep(pick(fid=1)), T)
    ps.observe("nba", rep(pick(fid=2, pct=0.57, gen="2026-11-25T20:10:00Z")), T + timedelta(minutes=15))
    assert [(r["forecast_id"], r["version_no"], r["percentage"]) for r in rows()] == [("1", 1, "60.0"), ("2", 1, "57.0")]


@pytest.mark.parametrize("change", [{"tipoff": "2026-11-25T20:00:00Z"}, {"tipoff": "2026-11-25T19:59:59Z"}, {"status": "in_progress"},
                                    {"status": "final", "grade": "win"}, {"generated_at": "2026-11-25T20:00:01Z"}])
def test_nothing_is_saved_at_or_after_the_start_or_before_the_forecast_exists(change):
    p = pick()
    p.update(change)
    assert ps.observe("nba", rep(p), T) == 0 and rows() == []


def test_the_table_refuses_updates_deletes_replacements_and_post_start_rows():
    ps.observe("nba", rep(pick()), T)
    conn = sqlite3.connect(ps.path())
    for sql in ("UPDATE pick_snapshots SET colour='red'", "DELETE FROM pick_snapshots",
                "INSERT OR REPLACE INTO pick_snapshots SELECT * FROM pick_snapshots",
                "INSERT INTO pick_snapshots (event, sport, game_id, market, forecast_key, forecast_id, version_no, observed_at, start_at, "
                "generated_at, probability, percentage, side, colour_rule, basis, recorded_at) VALUES ('seen','nba','g9','winner','9','9',1,"
                f"'{START}','{START}','2026-11-25T19:30:00Z',.6,'60.0','a','r','live','x')"):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql)
    conn.close()
    assert rows()[0]["colour"] == "green"


def test_a_pick_without_an_exact_record_is_saved_without_a_colour():
    p = pick(sport="soccer", market="btts", fid="goals:7:btts:yes", side="yes", pct=0.55, row_id=7, cutoff_at=START)
    p["percentage_history"] = None
    assert ps.observe("soccer", rep(p, sport="soccer"), T) == 1
    r, = rows("soccer")
    assert r["colour"] is None and r["forecast_key"] == "7" and r["wins"] is None


def test_a_missing_recovered_record_is_not_a_zero_record():
    ps.observe("ncaab", rep(pick(sport="ncaab", h=hist("ncaab", tg_ok=False)), sport="ncaab"), T)
    r, = rows("ncaab")
    assert r["recovered_colour"] is None and r["recovered_wins"] is None and r["recovered_reason"] == "no_rows_for_sport"


def test_recording_never_raises_and_never_changes_the_report(monkeypatch, capsys):
    r = rep(pick())
    before = copy.deepcopy(r)
    ps.observe("nba", r, T)
    assert r == before
    monkeypatch.setattr(ps, "_connect", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    assert ps.observe("nba", rep(pick(fid=5)), T) == -1
    assert "pick-snapshots" in capsys.readouterr().err


def test_a_development_process_never_writes_into_a_live_data_folder(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("NBA_RELEASE_GUARD", raising=False)
    assert ps.observe("nba", rep(pick()), T) == -1
    assert not (tmp_path / "data" / "pick_snapshots.sqlite").exists()


def test_the_recommended_pick_is_the_one_the_card_names():
    a, b = pick(fid=1), pick(fid=2, market="total", line=220.5, h=hist(market="total", src="bookmaker"))
    from app.tickets import card_choice
    chosen = card_choice("nba", [a, b])["pick"]["forecast_id"]
    ps.observe("nba", rep(a, b), T)
    got = {int(r["forecast_id"]): (r["on_card"], r["recommended"]) for r in rows()}
    assert got == {fid: (1, int(fid == chosen)) for fid in (1, 2)}


# --- Telegram: only confirmed sends ------------------------------------------------------------------------------------
def test_only_a_confirmed_send_is_saved_as_sent(tmp_path):
    with tg.ledger(tmp_path / "l.sqlite") as conn:
        for key, status in (("ok", "sent"), ("bad", "failed"), ("maybe", "uncertain")):
            conn.execute("INSERT INTO deliveries (delivery_key, chat_id, text, status, attempted_at, message_id) VALUES (?,?,?,?,?,?)",
                         (key, "1", "t", status, "2026-11-25T20:00:00Z", 5))
            ps.record_sent(conn, "nba", key, "g1", rep(pick()), T)
        ps.record_sent(conn, "nba", "ok", "g1", rep(pick()), T)                        # repeat: ignored
    sent = [r for r in rows() if r["event"] == "sent"]
    assert [(r["delivery_key"], r["message_id"], r["colour"]) for r in sent] == [("ok", 5, "green")]


def test_the_send_loop_saves_seen_and_sent_for_soccer_and_a_repeat_adds_nothing(tmp_path, monkeypatch):
    from app import db, tickets
    monkeypatch.setattr(tg, "controls", lambda *a: True)
    monkeypatch.setattr(db, "connect_readonly", lambda *a: (_ for _ in ()).throw(FileNotFoundError()))
    (tmp_path / "soccer.sqlite").touch()
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    r = _soccer_report()
    r["picks"][0].update(side="home_draw", row_id=1)
    monkeypatch.setattr(tickets, "soccer_section", lambda now, contract=False: {"report": r, "refused": {}})
    now = datetime.fromisoformat(NOW.replace("Z", "+00:00"))
    with tg.ledger(tmp_path / "receipts.sqlite") as conn:
        bot = Bot()
        first = tg.run_once(bot, {"TELEGRAM_CHAT_ID": "123"}, conn, now=now)
        assert first["sports"]["soccer"]["sent"] == 1
        n = [(x["event"], x["market"]) for x in rows("soccer")]
        assert {e for e, _ in n} == {"seen", "sent"}
        tg.run_once(bot, {"TELEGRAM_CHAT_ID": "123"}, conn, now=now)
    assert [(x["event"], x["market"]) for x in rows("soccer")] == n


def test_a_broken_recorder_never_stops_a_card(tmp_path, monkeypatch):
    from app import db, tickets
    monkeypatch.setattr(tg, "controls", lambda *a: True)
    monkeypatch.setattr(db, "connect_readonly", lambda *a: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(ps, "_connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    (tmp_path / "soccer.sqlite").touch()
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(tickets, "soccer_section", lambda now, contract=False: {"report": _soccer_report(), "refused": {}})
    with tg.ledger(tmp_path / "receipts.sqlite") as conn:
        out = tg.run_once(Bot(), {"TELEGRAM_CHAT_ID": "123"}, conn, now=datetime.fromisoformat(NOW.replace("Z", "+00:00")))
    assert out["sports"]["soccer"]["sent"] == 1 and out["sports"]["soccer"]["pick_snapshots"] == "failed"


# --- Counting ----------------------------------------------------------------------------------------------------------
def item(gid="g1", market="winner", fid="1", pct="60.0", side="a", grade="win", start=START, sport="nba", **kw):
    return {"sport": sport, "game_id": gid, "market": market, "forecast_id": fid, "forecast_key": fid, "percentage": pct, "side": side,
            "line": None, "grade": grade, "start_at": start, "game_date": start[:10], "generated_at": "2026-11-25T19:30:00Z",
            "matchup": "BOS @ ORL", "pick": "ORL win", "model_version": "m1", "market_title": "Winner", "final_score": None, **kw}


def classify(items, sport="nba", regrade=None, **kw):
    return ps.classify(sport, items, rows(sport), [], regrade or (lambda s, i: i["grade"]), **kw)


def test_the_colour_is_the_one_saved_before_the_start_whatever_the_bucket_says_later():
    ps.observe("nba", rep(pick()), T)                                         # green at 3-1
    ps.observe("nba", rep(pick(h=hist(w=3, l=9))), T + timedelta(minutes=30))   # the bucket has turned red
    got, = classify([item(grade="loss")])["all"]
    assert (got["colour"], got["support"]["wins"], got["support"]["losses"], got["grade"]) == ("green", 3, 1, "loss")
    assert [v["colour"] for v in got["versions"]] == ["green", "red"]


def test_one_prediction_per_game_and_market_the_forecast_of_record_and_earlier_forecasts_stay_listed():
    ps.observe("nba", rep(pick(fid=1)), T)                                                       # green
    ps.observe("nba", rep(pick(fid=2, pct=0.57, h=hist(pct="57.0", w=0, l=2), gen="2026-11-25T20:10:00Z")), T + timedelta(minutes=15))
    out = classify([item(fid="2", pct="57.0")])["all"]
    assert len(out) == 1 and out[0]["colour"] == "red"
    assert [(v["forecast_id"], v["colour"]) for v in out[0]["versions"]] == [("1", "green"), ("2", "red")]


def test_a_later_forecast_that_was_not_saved_is_unknown_never_the_earlier_colour():
    ps.observe("nba", rep(pick(fid=1)), T)
    got, = classify([item(fid="2")])["all"]
    assert got["colour"] == "unknown" and got["unknown_reason"] == "later_forecast_not_saved"
    assert [v["forecast_id"] for v in got["versions"]] == ["1"]


def test_unknown_reasons_before_the_rule_before_tracking_and_gaps():
    ps.observe("nba", rep(pick()), T)
    out = classify([item(gid="old", start="2026-11-20T00:00:00Z"), item(gid="mid", start="2026-11-24T00:00:00Z"),
                    item(gid="gap", start="2026-11-26T00:00:00Z")])["all"]
    assert {x["game_id"]: x["unknown_reason"] for x in out} == {
        "old": "before_colour_rule", "mid": "before_tracking", "gap": "not_recorded"}
    assert all(x["colour"] == "unknown" and x["recovered_colour"] == "unknown" for x in out)


def test_the_two_records_can_disagree_and_are_counted_apart():
    ps.observe("nba", rep(pick(gid="g1", h=hist(w=4, l=0, tg=(0, 3, 0, 0))), pick(gid="g2", fid=2, h=hist(w=0, l=2, tg=(5, 1, 0, 0)))), T)
    out = classify([item("g1", grade="win"), item("g2", fid="2", grade="loss")])["all"]
    assert [(x["colour"], x["recovered_colour"]) for x in out] == [("green", "red"), ("red", "green")]
    s = ps.summarise(out)
    assert s["live"]["green"]["win"] == 1 and s["live"]["green"]["loss"] == 0
    assert s["recovered"]["green"]["win"] == 0 and s["recovered"]["green"]["loss"] == 1
    assert s["cross"] == {"green|red": 1, "red|green": 1}


def test_no_recovered_record_is_its_own_group_not_neutral():
    ps.observe("ncaab", rep(pick(sport="ncaab", h=hist("ncaab", tg_ok=False)), sport="ncaab"), T)
    got, = classify([item(sport="ncaab")], "ncaab")["all"]
    assert got["recovered_colour"] == "no_record" and got["colour"] == "green"


def test_every_grade_has_its_own_count_and_the_groups_add_up():
    grades = ["win", "loss", "push", "void", "pending", "unscorable", "unresolved", "half_win", "half_loss"]
    ps.observe("nba", rep(*[pick(gid=f"g{i}", fid=i + 1) for i in range(len(grades))]), T)
    out = classify([item(f"g{i}", fid=str(i + 1), grade=g) for i, g in enumerate(grades)])["all"]
    t = ps.summarise(out)["live"]["green"]
    assert (t["predictions"], t["win"], t["loss"], t["push"], t["void"], t["half"], t["unsettled"]) == (9, 1, 1, 1, 1, 2, 3)
    assert t["settled"] == 2 and t["win_rate"] == 0.5 and t["games"] == 9
    assert ps.summarise(out)["adds_up"] is True


def test_the_original_pick_is_graded_not_the_records_current_selection():
    """Soccer: the record's selection of one stored forecast can move to another line; the saved side and line are what is graded."""
    p = pick(sport="soccer", market="goals_total", fid="goals:7:goals_total:over:2.5", side="over", line=2.5, pct=0.55, row_id=7, cutoff_at=START,
             h=hist("soccer", "goals_total", "55.0", side="over"))
    ps.observe("soccer", rep(p, sport="soccer"), T)
    seen = []

    def regrade(snap, it):
        seen.append((snap["forecast_id"], snap["side"], snap["line"]))
        return "loss"
    it = item(sport="soccer", market="goals_total", fid="goals:7:goals_total:under:3.5", side="under", pct="58.0", grade="win")
    it.update(forecast_key="7", line=3.5)
    got, = ps.classify("soccer", [it], rows("soccer"), [], regrade)["all"]
    assert seen == [("goals:7:goals_total:over:2.5", "over", 2.5)]
    assert (got["side"], got["line"], got["percentage"], got["grade"], got["selection_differs"]) == ("over", 2.5, "55.0", "loss", True)


def test_recommended_and_sent_are_separate_views_and_a_game_counts_once_in_each():
    ps.observe("nba", rep(pick(fid=1)), T)
    pub = [dict(delivery_key=k, game_id="g1", market="winner", forecast_id="1", side="a", line=None, probability=.6, model_version="m1",
                generated_at="2026-11-25T19:30:00Z", sent_at=at, message_id=n, basis="live")
           for n, (k, at) in enumerate((("k1", "2026-11-25T20:05:00Z"), ("k2", "2026-11-25T20:20:00Z"), ("late", "2026-11-25T21:30:00Z")))]
    out = ps.classify("nba", [item()], rows(), pub, lambda s, i: "win", starts={"g1": START})
    assert len(out["all"]) == len(out["recommended"]) == 1
    assert [(x["delivery_key"], x["grade"]) for x in out["sent"]] == [("k2", "win")]      # the last confirmed card before the start
    assert out["sent"][0]["colour"] == "unknown"                                       # no colour saved for that card: not borrowed
    assert out["excluded"]["sent_at_or_after_start"] == 1 and out["excluded"]["earlier_cards"] == 1


# --- Prices ------------------------------------------------------------------------------------------------------------
QUOTES = """CREATE TABLE market_quotes (quote_id INTEGER PRIMARY KEY, sport, game_id, provider, market, price_label, line REAL, price_a, price_mid,
 price_b, p_a_devig, p_mid_devig, p_b_devig, retrieved_at);
CREATE TABLE odds_quotes (quote_id INTEGER PRIMARY KEY, game_id, provider, price_label, retrieved_at, home_prob_devig, home_american, away_american);
CREATE TABLE game_forecasts (forecast_id INTEGER PRIMARY KEY, game_id, market, line REAL, line_source, model_version);"""


def quotes(*rows_, odds=()):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(QUOTES)
    c.executemany("INSERT INTO market_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows_)
    c.executemany("INSERT INTO odds_quotes VALUES (?,?,?,?,?,?,?,?)", odds)
    return c


def q(i=1, line=220.5, at="2026-11-25T19:50:00Z", label="current", a=-110, b=-105):
    return (i, "nba", "g1", "DraftKings", "total", label, line, a, None, b, .51, None, .49, at)


def total(**kw):
    return pick(market="total", line=220.5, side="b", pct=0.6, h=hist(market="total", src="bookmaker", side="b"), **kw)


def test_the_saved_price_is_the_picked_sides_price_at_the_exact_line():
    ps.observe("nba", rep(total()), T, store=quotes(q()))
    r, = rows()
    assert (r["price_american"], r["price_provider"], r["price_quote_id"], r["price_retrieved_at"]) == (-105, "DraftKings", 1, "2026-11-25T19:50:00Z")


@pytest.mark.parametrize("bad, why", [(q(line=221.5), "no_quote_at_this_line"), (q(at="2026-11-25T19:39:59Z"), "price_older_than_20_minutes"),
                                      (q(at="2026-11-25T20:00:01Z"), "no_quote_at_this_line"), (q(label="open"), "no_quote_at_this_line")])
def test_another_line_an_old_a_later_or_an_opening_price_is_not_a_price(bad, why):
    ps.observe("nba", rep(total()), T, store=quotes(bad))
    r, = rows()
    assert r["price_american"] is None and r["price_reason"] == why


def test_an_nba_moneyline_comes_from_the_odds_table():
    ps.observe("nba", rep(pick(side="b")), T, store=quotes(odds=[(9, "g1", "DraftKings", "current", "2026-11-25T19:55:00Z", .4, 130, -150)]))
    assert rows()[0]["price_american"] == -150


def test_flat_stake_returns_use_priced_picks_only_and_report_the_coverage():
    store = quotes(q())
    ps.observe("nba", rep(total(), pick(gid="g2", fid=2, side="b", market="total", line=200.5, pct=0.6,
                                        h=hist(market="total", src="bookmaker", side="b"))), T, store=store)
    out = classify([item(market="total", grade="win", side="b"), item("g2", market="total", fid="2", grade="loss", side="b")])["all"]
    t = ps.summarise(out)["live"]["green"]
    assert (t["predictions"], t["priced"], t["priced_settled"]) == (2, 1, 1)
    assert t["profit_units"] == pytest.approx(100 / 105) and t["roi"] == pytest.approx(100 / 105)
    none = ps.summarise(classify([item("g2", market="total", fid="2", grade="loss", side="b")])["all"])["live"]["green"]
    assert none["priced"] == 0 and none["profit_units"] is None and none["roi"] is None


@pytest.mark.parametrize("grade, american, profit", [("win", 150, 1.5), ("win", -200, .5), ("loss", -200, -1), ("push", -110, 0), ("void", -110, 0),
                                                     ("half_win", 100, .5), ("half_loss", -110, -.5), ("pending", -110, None)])
def test_flat_stake_profit_per_grade(grade, american, profit):
    assert ps.profit(grade, american) == profit


# --- Backfill from saved card text -------------------------------------------------------------------------------------
KEY = tg.COLOUR_PREFIX + tg.COLOUR_KEY
CARD = """<b>PROP51 PICKS · NBA</b>
<b>BOS @ ORL</b>
Starts Nov 25, 04:00 PM EST

<b>Recommended pick</b>
<b>ORL win</b>
<b>Market breakdown</b>

<b>Winner</b>
ORL win
<b>Model 60.0%</b> 🟢 · this exact % has gone 3–1 (75%) · 1 push · small sample (4 settled)
Recovered Telegram cards 11-02 to 11-16: this exact % has gone 0–2 (0%) · small sample (2 settled) · red record · model version not recorded

<b>Game total points</b>
Under 220.5 · <i>bookmaker line at forecast</i>
<b>Model 57.0%</b> · this exact % has gone 2–2 (50%) · small sample (4 settled)
Recovered Telegram cards 11-02 to 11-16: no recovered history at this exact % · model version not recorded

<b>Home team points</b>
ORL over 109.5 · <i>model line</i>
<b>Model 51.0%</b> · no settled history at this exact %
"""


def published(market="winner", prob=.60, side="a", fid="1", key="k1", at="2026-11-25T20:00:00Z", line=None):
    return dict(delivery_key=key, sport="nba", game_id="g1", market=market, forecast_id=fid, side=side, line=line, probability=prob,
                model_version="m1", generated_at="2026-11-25T19:30:00Z", sent_at=at, message_id=5, basis="live")


def delivery(text=None, status="sent", at="2026-11-25T20:00:00Z"):
    return {"delivery_key": "k1", "text": CARD + KEY if text is None else text, "status": status, "attempted_at": at, "message_id": 5}


def evidence(pub=None, d=None, start=START):
    return ps.card_evidence("nba", pub or published(), d or delivery(), start)


def test_card_text_proves_the_colour_and_both_records():
    e, why = evidence()
    assert why is None
    assert (e["colour"], e["wins"], e["losses"], e["pushes"], e["recovered_colour"], e["recovered_wins"], e["recovered_losses"]) == \
        ("green", 3, 1, 1, "red", 0, 2)
    assert (e["recommended"], e["on_card"], e["basis"], e["observed_at"]) == (1, 1, "card_text", "2026-11-25T20:00:00Z")
    assert "k1" in e["provenance"] and "sha256" in e["provenance"]


def test_a_neutral_block_is_proven_by_its_printed_record():
    e, _ = evidence(published("total", .57, "b", "2", line=220.5))
    assert (e["colour"], e["wins"], e["losses"], e["recovered_colour"], e["recovered_wins"], e["recommended"]) == ("neutral", 2, 2, None, None, 0)
    assert e["recovered_reason"] == "no_history" and ps.recovered_colour(e) == "no_record"
    e, _ = evidence(published("team_total_home", .51, "a", "3", line=109.5))
    assert (e["colour"], e["wins"], e["losses"], e["recovered_colour"]) == ("neutral", 0, 0, None)     # no recovered line on the card: not known


@pytest.mark.parametrize("pub, d, why", [
    (None, delivery(status="uncertain"), "delivery_not_confirmed"),
    (published(at="2026-11-25T21:00:00Z"), delivery(at="2026-11-25T21:00:00Z"), "sent_at_or_after_start"),
    (None, delivery(text=CARD + "Colour key · Line markets: Green = 59.9% and above"), "another_colour_rule"),
    (published(at="2026-11-22T17:15:40Z"), delivery(text=CARD.replace("🟢 ", ""), at="2026-11-22T17:15:40Z"), "before_colour_rule"),
    (published(prob=.61), None, "percentage_not_on_card"),
    (published("spread", .60), None, "market_not_on_card"),
    (None, delivery(text=CARD.replace("3–1 (75%)", "1–3 (25%)") + KEY), "mark_contradicts_record"),
    (None, delivery(text=CARD.replace(" · this exact % has gone 3–1 (75%) · 1 push · small sample (4 settled)", "") + KEY), "no_record_on_card"),
])
def test_insufficient_or_contradictory_evidence_is_never_backfilled(pub, d, why):
    assert evidence(pub, d) == (None, why)


def test_the_backfill_is_repeatable_labelled_and_never_replaces_a_live_row(tmp_path):
    ps.observe("nba", rep(pick(h=hist(w=0, l=2))), T)                           # observed live: red
    with tg.ledger(tmp_path / "l.sqlite") as conn:
        d = delivery()
        conn.execute("INSERT INTO deliveries (delivery_key, chat_id, text, status, attempted_at, message_id) VALUES ('k1','1',?,?,?,5)",
                     (d["text"], "sent", d["attempted_at"]))
        p = published()
        conn.execute(f"INSERT INTO published_picks ({','.join(p)}) VALUES ({','.join('?' * len(p))})", tuple(p.values()))
        first = ps.backfill(conn, "nba", {"g1": START}, apply=True)
        again = ps.backfill(conn, "nba", {"g1": START}, apply=True)
    assert first["written"] == 1 and again["written"] == 0
    assert [(r["event"], r["basis"], r["colour"]) for r in rows()] == [("seen", "live", "red"), ("sent", "card_text", "green")]
    assert ps.tracking_start(rows()) == "2026-11-25T20:00:00Z"


# --- Independent review (Nov 25) ---------------------------------------------------------------------------------------
def test_a_bucket_without_recovered_rows_is_no_record_never_0_0():
    ps.observe("nba", rep(pick(h=hist(tg=(0, 0, 0, 0)))), T)
    r, = rows()
    assert (r["recovered_colour"], r["recovered_wins"], r["recovered_losses"], r["recovered_reason"]) == (None, None, None, "no_history")
    assert ps.recovered_colour(r) == "no_record" and r["colour"] == "green"


def test_a_recovered_bucket_with_only_pushes_is_a_neutral_record():
    ps.observe("nba", rep(pick(h=hist(tg=(0, 0, 2, 0)))), T)
    r, = rows()
    assert (r["recovered_colour"], r["recovered_pushes"]) == ("neutral", 2)


def test_a_price_at_a_line_the_book_has_left_is_not_executable():
    ps.observe("nba", rep(total()), T, store=quotes(q(1, 220.5, "2026-11-25T19:50:00Z"), q(2, 219.5, "2026-11-25T19:58:00Z")))
    r, = rows()
    assert r["price_american"] is None and r["price_reason"] == "line_moved"


def test_another_book_still_at_the_line_is_the_price():
    other = list(q(1, 220.5, "2026-11-25T19:49:00Z", b=-102))
    other[3] = "FanDuel"
    ps.observe("nba", rep(total()), T, store=quotes(tuple(other), q(2, 220.5, "2026-11-25T19:50:00Z"), q(3, 219.5, "2026-11-25T19:58:00Z")))
    r, = rows()
    assert (r["price_provider"], r["price_american"]) == ("FanDuel", -102)


def test_one_malformed_pick_never_stops_the_others_and_is_reported():
    bad = pick("bad", fid=9)
    bad["percentage_history"] = {"sport": "nba"}
    none = pick("none", fid=8)
    none["probability"] = None
    assert ps.observe("nba", rep(bad, pick("good", fid=1), none), T) == -1            # visible as a failure
    assert [r["game_id"] for r in rows()] == ["good"]


def test_a_recommendation_that_cannot_be_read_is_unknown_not_no(monkeypatch):
    from app import tickets
    monkeypatch.setattr(tickets, "card_choice", lambda *a: (_ for _ in ()).throw(KeyError("x")))
    ps.observe("nba", rep(pick()), T)
    assert rows()[0]["recommended"] is None


def test_a_sent_row_without_a_saved_colour_is_labelled_as_published():
    pub = [dict(published(market="total", prob=.55, side="b", fid="5", line=113.5), published_id=1)]
    it = item(market="total", fid="6", side="a", pick="PHX over 113.5")
    out = ps.classify("nba", [it], [], pub, lambda s, i: "win", starts={"g1": START}, label=lambda p: "PHX under 113.5")
    assert (out["sent"][0]["pick"], out["sent"][0]["side"], out["sent"][0]["grade"]) == ("PHX under 113.5", "b", "win")
    assert ps.classify("nba", [it], [], pub, lambda s, i: "win", starts={"g1": START})["sent"][0]["pick"] is None


def test_unconfirmed_sends_are_counted_as_excluded_and_every_ledger_row_is_accounted_for():
    pub = [published(), dict(published(key="k2", market="total"), basis="uncertain_resolved_message_id_gap")]
    out = ps.classify("nba", [item()], [], pub, lambda s, i: "win", starts={"g1": START})
    assert len(out["sent"]) == 1 and out["excluded"] == {"delivery_not_confirmed": 1}


def test_at_most_one_recommended_pick_per_game_the_last_one_before_the_start():
    ps.observe("nba", rep(pick(fid=1)), T)                                                         # winner is recommended
    a = pick(fid=1)
    b = pick(fid=2, market="total", line=220.5, h=hist(market="total", src="bookmaker", w=9, l=0), gen="2026-11-25T20:10:00Z")
    b["percentage_history"].update(rank=1, ranked=1)
    ps.observe("nba", rep(a, b), T + timedelta(minutes=15))                                         # now the total is
    last = {r["market"]: r["recommended"] for r in rows()}
    out = classify([item(), item(market="total", fid="2", line=220.5)])
    assert last == {"winner": 0, "total": 1} and [r["market"] for r in out["recommended"]] == ["total"]
    assert out["all"][0]["colour"] == "green"                                                       # its colour is still the first one


def test_a_forecast_first_seen_after_a_start_that_moved_earlier_says_so():
    ps.observe("nba", rep(pick()), T)
    got, = classify([item(start="2026-11-25T19:45:00Z")])["all"]
    assert (got["colour"], got["unknown_reason"], got["recommended"]) == ("unknown", "saved_after_the_moved_start", None)


# --- Re-review (Nov 25) -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("fault", [{"tipoff": None}, {"generated_at": None}, {"tipoff": "2026-11-25 nonsense"}, {"market": None}, {"pick_label": object()}])
def test_no_single_bad_pick_drops_the_batch(fault):
    bad = {**pick("bad", fid=9), **fault}
    assert ps.observe("nba", rep(bad, pick("good", fid=1)), T) == -1
    assert "good" in [r["game_id"] for r in rows()]


def test_an_unexpected_price_error_saves_the_pick_unpriced(monkeypatch):
    from app import odds
    monkeypatch.setattr(odds, "provider_key", lambda n: (_ for _ in ()).throw(RuntimeError("x")))
    assert ps.observe("nba", rep(total()), T, store=quotes(q())) == 1
    assert rows()[0]["price_reason"] == "quotes_unreadable"


def test_an_unreadable_recommendation_never_erases_a_known_one(monkeypatch):
    from app import tickets
    ps.observe("nba", rep(pick()), T)
    monkeypatch.setattr(tickets, "card_choice", lambda *a: (_ for _ in ()).throw(KeyError("x")))
    assert ps.observe("nba", rep(pick()), T + timedelta(minutes=1)) == 0              # not a new version
    ps.observe("nba", rep(pick(h=hist(w=4, l=1))), T + timedelta(minutes=2))           # a real change, recommendation unreadable
    assert len(rows()) == 2
    assert [r["game_id"] for r in classify([item()])["recommended"]] == ["g1"]


def test_a_pick_that_left_the_card_gives_way_to_the_later_recommendation():
    ps.observe("nba", rep(pick(fid=1)), T)
    later = pick(fid=2, market="total", line=220.5, h=hist(market="total", src="bookmaker"))
    ps.observe("nba", rep(later), T + timedelta(minutes=10))                          # the winner pick is gone from the report
    out = classify([item(), item(market="total", fid="2", line=220.5)])
    assert [r["market"] for r in out["recommended"]] == ["total"]
    assert {r["market"]: r["recommended"] for r in out["all"]} == {"winner": False, "total": True}


def test_git_is_never_asked_while_the_write_lock_is_held(monkeypatch):
    asked = []

    def commit():
        c = sqlite3.connect(ps.path(), timeout=0.1, isolation_level=None)
        c.execute("BEGIN IMMEDIATE")               # fails if the recorder holds the lock
        c.execute("ROLLBACK")
        c.close()
        asked.append(1)
        return "abc"
    monkeypatch.setattr(ps, "_commit", commit)
    assert ps.observe("nba", rep(pick()), T) == 1 and asked and rows()[0]["code_commit"] == "abc"


def test_a_locked_file_delays_a_run_once_not_once_per_pick(monkeypatch):
    import time
    ps.observe("nba", rep(pick("g0")), T)
    real, holder = ps._recommended, []

    def lock(*a):                                                  # taken after the recorder connected
        c = sqlite3.connect(ps.path(), isolation_level=None)
        c.execute("BEGIN EXCLUSIVE")
        holder.append(c)
        return real(*a)
    monkeypatch.setattr(ps, "_recommended", lock)
    began = time.monotonic()
    assert ps.observe("nba", rep(*[pick(f"g{i}", fid=i) for i in range(1, 6)]), T) == -1
    assert time.monotonic() - began < 4
    holder[0].execute("ROLLBACK")


def test_an_unstorable_number_or_a_text_price_costs_only_itself():
    bad = {**pick("bad", fid=9), "line": 2 ** 70}
    assert ps.observe("nba", rep(bad, pick("good", fid=1)), T) == -1
    assert [r["game_id"] for r in rows()] == ["good"]
    assert ps.observe("nba", rep({**total(), "game_id": "g1", "forecast_id": 5}), T, store=quotes(q(a="x", b="y"))) == 1
    assert rows()[-1]["price_reason"] == "quotes_unreadable"


def test_a_superseded_recommendation_is_shown_not_counted_and_the_latest_one_stands(monkeypatch):
    """Owner 2026-11-26: both cards keep their own result; only the latest recommendation of a match is the counted one."""
    a, b = pick(fid=1), pick(fid=2, market="total", line=220.5, h=hist(market="total", src="bookmaker"))
    monkeypatch.setattr(ps, "_recommended", lambda report, picks: {"g1": ("winner", "1")})
    ps.observe("nba", rep(a), T)
    monkeypatch.setattr(ps, "_recommended", lambda report, picks: {"g1": ("total", "2")})
    ps.observe("nba", rep(b), T + timedelta(minutes=25))
    out = classify([item(grade="win"), item(market="total", fid="2", grade="loss", side="b")])
    by = {r["market"]: r for r in out["all"]}
    assert [r["market"] for r in out["recommended"]] == ["total"]                         # one counted per match: the latest
    assert (by["total"]["recommendation_status"], by["winner"]["recommendation_status"]) == ("counted", "superseded")
    assert by["winner"]["grade"] == "win" and by["winner"]["recommended"] is False        # its own result is still shown
    assert by["winner"]["recommendation_note"] == "superseded, not counted"
    assert len(rows()) == 2                                                               # nothing rewritten
