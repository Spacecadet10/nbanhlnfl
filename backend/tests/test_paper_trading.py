"""Paper trading report: bets only at quotes retrieved before the forecast, one bet per source/game/market/threshold,
CLV against the last same-line quote before the start, settlement through markets.settle."""
import pytest

from app import db, paper_trading as pt


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(tmp_path / "nhl.sqlite")
    db.migrate(c)
    db.claim_store(c, "nhl")
    c.execute("INSERT INTO raw_responses (raw_id, source, endpoint, request_key, content_hash, byte_size, path, "
              "importer_version, first_retrieved_at) VALUES (1,'test','test','k','h',0,'p','t','2026-10-01T00:00:00Z')")
    for t in ("1", "2"):
        c.execute("INSERT INTO teams VALUES (?,?,?,?)", (f"nhl:{t}", f"T{t}", f"Team {t}", "2026-10-01T00:00:00Z"))
    c.execute("INSERT INTO games (game_id, season, season_type, tipoff_at, home_team_id, away_team_id, neutral_site, status, "
              "home_score, away_score, result_available_at, result_availability_basis, first_seen_at, last_raw_id, updated_at) "
              "VALUES ('g1',2027,'regular','2026-10-10T23:00:00Z','nhl:1','nhl:2',0,'final',4,2,'2026-10-11T03:00:00Z',"
              "'observed_retrieval','2026-10-01T00:00:00Z',1,'2026-10-11T03:00:00Z')")
    c.commit()
    return c


def quote(c, at, pa, price_a=-110, price_b=-110, market="winner", line=None):
    fetch = c.execute("INSERT INTO raw_fetches (raw_id, retrieved_at) VALUES (1, ?)", (at,)).lastrowid
    c.execute("INSERT INTO market_quotes (sport, game_id, provider, market, price_label, line, price_a, price_b, p_a_devig, "
              "p_b_devig, retrieved_at, fetch_id, raw_id) VALUES ('nhl','g1','test',?,'current',?,?,?,?,?,?,?,1)",
              (market, line, price_a, price_b, pa, 1 - pa, at, fetch))


def forecasts(monkeypatch, rows):
    monkeypatch.setattr(pt, "_forecasts", lambda conn, since: rows)


def fc(at, pa, source="canonical", market="winner", line=None, tipoff="2026-10-10T23:00:00Z"):
    return (source, "g1", market, line, pa, 1 - pa, at, tipoff)


def test_the_first_qualifying_forecast_bets_at_the_earlier_quote_and_is_settled_with_clv(store, monkeypatch):
    quote(store, "2026-10-10T20:00:00Z", 0.50, price_a=+100, price_b=-120)
    quote(store, "2026-10-10T22:50:00Z", 0.56)                       # the close moved toward home
    quote(store, "2026-10-10T23:30:00Z", 0.90)                       # after the start: never the close
    forecasts(monkeypatch, [fc("2026-10-10T20:05:00Z", 0.53), fc("2026-10-10T20:30:00Z", 0.58), fc("2026-10-10T21:00:00Z", 0.60)])
    b = {x["threshold"]: x for x in pt.bets(store)}
    assert b[0.02]["forecast_at"] == "2026-10-10T20:05:00Z" and b[0.02]["side"] == "a"
    assert b[0.04]["forecast_at"] == "2026-10-10T20:30:00Z" and b[0.06]["forecast_at"] == "2026-10-10T20:30:00Z"
    assert b[0.02]["price"] == 2.0 and b[0.02]["result"] == "a" and b[0.02]["profit"] == 1.0
    assert b[0.02]["clv"] == pytest.approx(0.06)
    r = pt.report(store)["canonical"]["winner"]["2pt"]
    assert (r["bets"], r["wins"], r["losses"], r["roi"]) == (1, 1, 0, 1.0)


def test_no_quote_retrieved_before_the_forecast_means_no_bet_and_other_lines_are_never_used(store, monkeypatch):
    quote(store, "2026-10-10T21:00:00Z", 0.40)                       # retrieved after the forecast
    quote(store, "2026-10-10T19:00:00Z", 0.40, market="spread", line=-2.5)
    forecasts(monkeypatch, [fc("2026-10-10T20:00:00Z", 0.60), fc("2026-10-10T20:00:00Z", 0.60, market="spread", line=-1.5)])
    assert pt.bets(store) == []


def test_below_threshold_is_no_bet_the_away_side_can_be_bet_and_a_moved_start_voids(store, monkeypatch):
    quote(store, "2026-10-10T19:00:00Z", 0.60)
    forecasts(monkeypatch, [fc("2026-10-10T20:00:00Z", 0.61),                                  # 1 point: no bet
                            fc("2026-10-10T20:00:00Z", 0.55, source="adaptive:x", tipoff="2026-10-10T22:00:00Z")])
    b = pt.bets(store, thresholds=(0.02,))
    assert len(b) == 1 and b[0]["source"] == "adaptive:x" and b[0]["side"] == "b"
    assert b[0]["result"] == "void" and b[0]["profit"] is None
    assert pt.report(store, thresholds=(0.02,))["adaptive:x"]["winner"]["2pt"]["voids"] == 1


def test_a_repeated_opening_row_is_never_the_bet_price_or_the_close(store, monkeypatch):
    # Review: ESPN repeats the open row in every fetch; with the line moved, a forecast at the opening line must not bet
    # at the stale opening price, and no same-line close exists.
    for at in ("2026-10-10T19:00:00Z", "2026-10-10T22:00:00Z"):
        fetch = store.execute("INSERT INTO raw_fetches (raw_id, retrieved_at) VALUES (1, ?)", (at,)).lastrowid
        for label, line in (("open", 5.5), ("current", 6.5)):
            store.execute("INSERT INTO market_quotes (sport, game_id, provider, market, price_label, line, price_a, price_b, "
                          "p_a_devig, p_b_devig, retrieved_at, fetch_id, raw_id) VALUES ('nhl','g1','test','total',?,?,"
                          "-110,-110,0.5,0.5,?,?,1)", (label, line, at, fetch))
    forecasts(monkeypatch, [fc("2026-10-10T20:00:00Z", 0.60, market="total", line=5.5)])
    assert pt.bets(store) == []


def test_a_price_without_a_decimal_equivalent_is_no_bet(store, monkeypatch):
    quote(store, "2026-10-10T19:00:00Z", 0.50, price_a=50, price_b=-110)            # |American| < 100: not a price
    forecasts(monkeypatch, [fc("2026-10-10T20:00:00Z", 0.60)])
    assert pt.bets(store) == []



def observe(c, rows, market="winner", line=None, recorded="2026-10-10T21:40:00Z"):
    """Records (group_id, kind, vector) rows through one research cohort of g1, as the live agreement tracker does."""
    final = c.execute("SELECT home_score, away_score, result_available_at, result_availability_basis FROM games").fetchone()
    c.execute("UPDATE games SET status = 'scheduled', home_score = NULL, away_score = NULL, result_available_at = NULL, "
              "result_availability_basis = NULL WHERE game_id = 'g1'")      # cohorts are only made for scheduled games
    cohort = c.execute("INSERT INTO research_cohorts (idempotency_key, sport, game_id, market, line, line_source, definition, "
                       "primary_name, information_at, generated_at, tipoff_at, members, agreement) VALUES (?,'nhl','g1',?,?,?,"
                       "'d','p','2026-10-10T21:30:00Z','2026-10-10T21:31:00Z','2026-10-10T23:00:00Z','[]','{}')",
                       (f"{market}{line}{recorded}", market, line, None if line is None else "model")).lastrowid
    for n, (group, kind, vector) in enumerate(rows):
        c.execute("INSERT INTO agreement_observations (registry, group_id, sport, game_id, market, line, line_source, cohort_id, "
                  "source_cohort_id, definition, kind, members, version_sig, inputs_digest, status, side, statistic, vector, "
                  "information_at, tipoff_at, recorded_at) VALUES ('r',?,'nhl','g1',?,?,?,?,?,'d',?,'[]','v',?,'pick','a',0.6,?,"
                  "'2026-10-10T21:30:00Z','2026-10-10T23:00:00Z',?)",
                  (group, market, line, None if line is None else "model", cohort, cohort, kind, f"i{n}", vector, recorded))
    c.execute("UPDATE games SET status = 'final', home_score = ?, away_score = ?, result_available_at = ?, "
              "result_availability_basis = ? WHERE game_id = 'g1'", final)


def singles(c, **kw):
    return [b for b in pt.bets(c, thresholds=(0.04,), **kw) if b["source"] != "canonical"]


def test_a_single_model_agreement_observation_is_paper_traded_from_its_recording_time(store):
    # The live agreement tracker records each registered single member's vector (e.g. the NHL shadow member
    # hockey_logistic_xg) within two hours of the start: paper-traded as `single:<member>` at the quote retrieved at or
    # before the row was recorded (not its information time). Other group kinds and incomplete (null) vectors are not.
    quote(store, "2026-10-10T21:35:00Z", 0.50, price_a=+100, price_b=-120)
    quote(store, "2026-10-10T21:45:00Z", 0.40)                       # retrieved after the recording: never the bet price
    observe(store, [("single|hockey_logistic_xg", "single", "[0.6, 0.0, 0.4]"), ("avg|x", "avg", "[0.9, 0.0, 0.1]"),
                    ("single|incomplete", "single", "null"), ("single|dog", "single", "[0.3, 0.0, 0.7]")])
    b = singles(store)
    assert [(x["source"], x["forecast_at"], x["side"], x["price"], x["profit"]) for x in b] == [
        ("single:dog", "2026-10-10T21:40:00Z", "b", 1 + 100 / 120, -1.0),
        ("single:hockey_logistic_xg", "2026-10-10T21:40:00Z", "a", 2.0, 1.0)]
    assert singles(store, since="2026-10-10T21:41:00Z") == []      # the `since` filter applies to recorded singles too


def test_rows_of_one_cycle_are_taken_in_a_fixed_order_and_unusable_vectors_never_abort_the_report(store):
    # Review: rows of one recording cycle share `recorded_at`; the first in storage order is the bet, every run. A vector
    # with all mass on the push, or not valid JSON, is skipped without taking the report down.
    quote(store, "2026-10-10T21:35:00Z", 0.50)
    observe(store, [("single|m", "single", "[0.0, 1.0, 0.0]"), ("single|m", "single", "[NaN, 0.0, 1.0]"),
                    ("single|m", "single", "[0.60, 0.0, 0.40]"), ("single|m", "single", "[0.70, 0.0, 0.30]")])
    b = singles(store)
    assert len(b) == 1 and b[0]["edge"] == pytest.approx(0.10)
    assert pt.report(store, thresholds=(0.04,))["single:m"]["winner"]["4pt"]["bets"] == 1


def test_a_single_spread_vector_with_push_mass_is_renormalised_at_its_exact_line(store):
    quote(store, "2026-10-10T21:35:00Z", 0.50, market="spread", line=-1.0)
    observe(store, [("single|goals_glm", "single", "[0.55, 0.05, 0.40]")], market="spread", line=-1.0)
    b = singles(store)
    assert len(b) == 1 and b[0]["edge"] == pytest.approx(0.55 / 0.95 - 0.5) and b[0]["side"] == "a"
    assert b[0]["result"] == "a"                                     # home won by 2 at -1.0


def early(c, at, pa, arm="open", member="hockey_logistic_xg"):
    """Appends an early forecast of g1 (docs/evidence/early-forecast-test) while it is still scheduled."""
    from app import early_forecasts as ef
    final = c.execute("SELECT home_score, away_score, result_available_at, result_availability_basis FROM games").fetchone()
    c.execute("UPDATE games SET status = 'scheduled', home_score = NULL, away_score = NULL, result_available_at = NULL, "
              "result_availability_basis = NULL WHERE game_id = 'g1'")
    ef.store_row(c, {"sport": "nhl", "arm": arm, "member": member, "model_version": "v", "game_id": "g1", "market": "winner",
                     "status": "forecast", "reason": None, "p_a": pa, "p_b": 1 - pa, "information_at": at,
                     "trigger_detail": "{}", "provenance": "{}", "tipoff_at": "2026-10-10T23:00:00Z", "generated_at": at,
                     "idempotency_key": f"{arm}{at}"})
    c.execute("UPDATE games SET status = 'final', home_score = ?, away_score = ?, result_available_at = ?, "
              "result_availability_basis = ? WHERE game_id = 'g1'", final)


def test_an_early_forecast_bets_at_the_first_quote_retrieved_after_it_and_reports_the_excess_clv(store):
    # Pre-registered price rule: a bettor acts after the forecast, so the bet price is the first odds snapshot retrieved
    # after it (polled every 30 min), never the last one before it. Excess CLV = de-vigged close of the bet side minus the
    # bet price's implied probability (> 0 only when the close beats the price paid, margin included).
    quote(store, "2026-10-10T08:00:00Z", 0.50, price_a=+100, price_b=-120)      # before the forecast: never the price
    quote(store, "2026-10-10T08:40:00Z", 0.48, price_a=+105, price_b=-125)      # the first after it: the bet price
    quote(store, "2026-10-10T22:50:00Z", 0.56)                                 # the close
    early(store, "2026-10-10T08:30:00Z", 0.60)
    b = [x for x in pt.bets(store, thresholds=(0.06,)) if x["source"] == "early:open:hockey_logistic_xg"]
    assert len(b) == 1 and (b[0]["quote_at"], b[0]["side"], b[0]["price"]) == ("2026-10-10T08:40:00Z", "a", 2.05)
    assert b[0]["edge"] == pytest.approx(0.12) and b[0]["clv"] == pytest.approx(0.08)
    assert b[0]["excess_clv"] == pytest.approx(0.56 - 1 / 2.05) and b[0]["result"] == "a"
    r = pt.report(store, thresholds=(0.06,))["early:open:hockey_logistic_xg"]["winner"]["6pt"]
    assert r["excess_clv_mean"] == pytest.approx(0.56 - 1 / 2.05) and r["bets"] == 1


def test_an_early_forecast_without_a_later_quote_before_the_start_is_no_bet(store):
    quote(store, "2026-10-10T08:00:00Z", 0.40)
    quote(store, "2026-10-10T23:10:00Z", 0.40)                                  # after the start: never a bet price
    early(store, "2026-10-10T08:30:00Z", 0.60, arm="news", member="nhl_players_injuries@adaptive")
    assert [x for x in pt.bets(store) if x["source"].startswith("early:")] == []


def test_an_early_bet_priced_at_the_closing_quote_counts_at_minus_the_margin(store):
    # Review: with one quote after the forecast, the bet price is also the close; excluding such bets from the excess CLV
    # (always minus the margin) would bias the pre-registered metric upward. A quote retrieved in the same second as
    # the forecast is not after it.
    quote(store, "2026-10-10T22:20:00Z", 0.40)                                  # same second as the forecast: not after
    quote(store, "2026-10-10T22:40:00Z", 0.45, price_a=+115, price_b=-135)      # the bet price and the close
    early(store, "2026-10-10T22:20:00Z", 0.60, arm="news", member="m")
    b = [x for x in pt.bets(store, thresholds=(0.06,)) if x["source"] == "early:news:m"]
    assert len(b) == 1 and b[0]["quote_at"] == "2026-10-10T22:40:00Z"
    assert b[0]["clv"] == 0 and b[0]["excess_clv"] == pytest.approx(0.45 - 1 / 2.15) and b[0]["excess_clv"] < 0
    observe(store, [("single|m", "single", "[0.70, 0.0, 0.30]")], recorded="2026-10-10T22:30:00Z")
    single = [x for x in pt.bets(store, thresholds=(0.06,)) if x["source"] == "single:m"]
    assert len(single) == 1 and single[0]["quote_at"] == "2026-10-10T22:20:00Z"    # existing rule: the quote before
    assert single[0]["clv"] is not None                                         # its close (22:40) is later
    assert pt.bets(store, thresholds=(0.06,), prefix="early:") == b             # other sources left out
    observe(store, [("single|n", "single", "[0.70, 0.0, 0.30]")], recorded="2026-10-10T22:45:00Z")
    late = [x for x in pt.bets(store, thresholds=(0.06,)) if x["source"] == "single:n"]
    assert late[0]["quote_at"] == "2026-10-10T22:40:00Z" and late[0]["clv"] is None and late[0]["excess_clv"] is None
