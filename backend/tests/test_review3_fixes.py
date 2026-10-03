"""Review fixes 2026-10-03: NBA market comparison uses the current price, forecast notes no longer promise the discontinued
daily job, a game card's agreement never mixes research definitions, and the injury-aware member's own refusal does not
mark a two-hour run failed (synthetic stores only)."""
import json
from datetime import datetime, timezone

import pytest

from app import agreement, jobs, live
from app.ledger import Forecast, record
from app.providers.real import RealProvider
from test_live import T_GAME, T_OTHER, client, event, ingest_events, store  # noqa: F401  (fixtures)
from test_near_tipoff import NOW, ready  # noqa: F401  (fixture)


def test_nba_market_comparison_prefers_the_current_price_at_equal_retrieval_times(store, client):
    """Every NBA odds snapshot stores an opening and a current price with one retrieval time: the comparison uses current.
    The old query (ORDER BY retrieved_at only) returned whichever row SQLite's index walk met first: 'current' sorts before
    'open' within one provider, so it was right by accident. g0's opening row is therefore stored under a provider name
    that sorts first, which makes the old query return the opening price."""
    ingest_events(store, [event("g0", T_OTHER, "3", "4")], "2026-10-19T11:00:00Z")
    raw = store.execute("SELECT MAX(raw_id) FROM raw_responses").fetchone()[0]
    at = "2026-10-19T14:00:00Z"                    # later than g1's 12:00Z quote, before the 15:00Z forecast
    for gid, order in (("espn:g0", ("open", "current")), ("espn:g1", ("current", "open"))):   # either insertion order
        for label in order:
            provider = "A-Book" if (gid, label) == ("espn:g0", "open") else "Book"
            store.execute("INSERT INTO odds_quotes (game_id, provider, market, price_label, home_american, away_american, "
                          "home_prob_devig, quote_time, retrieved_at, raw_id) VALUES (?, ?, 'moneyline', ?, -150, 130, ?, NULL, ?, ?)",
                          (gid, provider, label, 0.6 if label == "current" else 0.9, at, raw))
    live.predict(store, now="2026-10-19T15:00:00Z")
    ingest_events(store, [event("g0", T_OTHER, "3", "4", "STATUS_FINAL", 101, 99),
                          event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    market = client.get("/api/performance", params={"run_type": "live"}).json()["market"]
    assert market["matched_games"] == 2
    assert market["market_brier"] == pytest.approx((1 - 0.6) ** 2)        # both home wins, both scored at the current 0.6


def test_forecast_note_does_not_promise_the_discontinued_daily_job(store, client):
    client.now = "2026-10-01T12:00:00Z"                                    # weeks before the game: nothing recorded
    note = client.get("/api/games/espn:g1").json()["forecast_note"]
    assert "discontinued" in note and "within two hours" in note
    assert "records it" not in note and "by the daily job" not in note


def test_game_card_agreement_never_mixes_research_definitions(store):
    """A code change on unchanged data gives a new definition with the same dataset version: members of the earlier
    definition are not combined with it."""
    def put(name, definition, at, p):
        record(store, Forecast("espn:g1", "live", f"research-v2:{name}:{definition}", "a", agreement.FEATURE_VERSION,
                               T_GAME, at, p))
    for name in agreement.NAMES["nba"]:
        put(name, "old", "2026-10-21T21:35:00Z", 0.7)
    for name in agreement.NAMES["nba"][:2]:
        put(name, "new", "2026-10-21T21:45:00Z", 0.4)
    mixed = agreement.summary(store, "espn:g1", "nba", "2026-10-21T22:45:00Z")
    assert len({m["model_version"].rsplit(":", 1)[-1] for m in mixed["models"]}) == 2      # the unguarded summary mixes
    card = RealProvider(store, now="2026-10-21T22:45:00Z")._agreement("espn:g1", "near_tipoff")
    assert {m["model_version"].rsplit(":", 1)[-1] for m in card["models"]} == {"new"}
    assert (card["status"], card["available"], card["home_votes"], card["away_votes"]) == ("partial", 2, 0, 2)
    before = RealProvider(store, now="2026-10-21T21:40:00Z")._agreement("espn:g1", "near_tipoff")   # one definition: unchanged
    assert before["status"] == "complete" and before["home_votes"] == len(agreement.NAMES["nba"])


# --- The injury-aware member's own refusal is not a failed run (jobs.py; agreement.py is identity-hashed and unchanged) ---
REFUSAL = "AvailabilityError: No injury report actually received in the last 60 minutes"


def agreement_run(errors, skipped=None, **extra):
    return {"sport": "nba", "status": "failed", "eligible_games": 1, "created": 5, "already_recorded": 0,
            "errors": errors, "skipped": skipped or {}, "forecasts": [], **extra}


def test_only_a_refusing_members_refusal_is_downgraded():
    ok = jobs._refusal_is_not_failure(agreement_run({"availability_two_hour": REFUSAL}))
    assert ok["status"] == "partial" and ok["refused"] == {"availability_two_hour": REFUSAL}
    assert ok["errors"] == {"availability_two_hour": REFUSAL}              # still reported per member; cohorts see it missing
    for run in (agreement_run({"availability_two_hour": REFUSAL, "gbt_v1": "ValueError: boom"}),     # another member failed
                agreement_run({"availability_two_hour": "KeyError: 'x'"}),                             # not a refusal: a crash
                agreement_run({"gbt_v1": "AvailabilityError: not its error to raise"}),                # not a refusing member
                agreement_run({"availability_two_hour": REFUSAL}, skipped={"efficiency_margin": ["espn:g1"]}),
                {"status": "failed", "error": "RuntimeError: the whole step crashed"},
                agreement_run({}, skipped={"logistic": ["espn:g1"]})):
        assert jobs._refusal_is_not_failure(run) == run and run["status"] == "failed"
    fine = {"sport": "nba", "status": "success", "errors": {}, "skipped": {}}
    assert jobs._refusal_is_not_failure(fine) is fine


def test_two_hour_run_is_not_failed_by_the_injury_members_refusal(ready, monkeypatch):
    empty = json.dumps({"events": []}).encode()
    at = datetime(2026, 10, 21, 22, 28, tzinfo=timezone.utc)
    kw = dict(refresh_kw={"fetch": lambda m: empty, "sleep": lambda s: None, "now": at},
              odds_kw={"fetch": lambda d: empty, "sleep": lambda s: None, "now": at},
              box_kw={"fetch": lambda g: b"{}", "sleep": lambda s: None, "now": at})
    monkeypatch.setattr(jobs, "research_forecasts", lambda conn, **k: agreement_run({"availability_two_hour": REFUSAL}))
    out = jobs.predict_now(ready, now=NOW, **kw)
    assert out["created"] == 1 and out["agreement"]["status"] == "partial" and out["status"] != "failed"
    assert out["agreement"]["refused"] == {"availability_two_hour": REFUSAL}
    monkeypatch.setattr(jobs, "research_forecasts",
                        lambda conn, **k: agreement_run({"availability_two_hour": REFUSAL, "gbt_v1": "ValueError: boom"}))
    again = jobs.predict_now(ready, now=NOW, **kw)
    assert again["status"] == "failed" and again["agreement"]["status"] == "failed"      # a real member failure still fails
