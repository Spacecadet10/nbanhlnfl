"""Plan B1 (M2) proposed regression tests, run on snap3 from a scratch copy. Expected to FAIL on snap3."""
import sys
sys.path.insert(0, "/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/snap3/backend/tests")

from app import coverage, near_tipoff  # noqa: E402
from test_live import T_GAME, event, ingest_events, client, store  # noqa: E402,F401
from test_near_tipoff import NOW, ready, refreshed  # noqa: E402,F401

G1 = "espn:g1"
RECORDED = "2026-10-21T23:05:00Z"        # canonical record written 35 min after the probability was computed
LATE_QUOTE = "2026-10-21T22:50:00Z"      # a moneyline retrieved after the computation, before the record


def _cycle_with_record_lag(conn):
    near_tipoff.predict_now(conn, now=NOW)                                   # computed 22:30
    ingest_events(conn, [event("g1", T_GAME, "1", "2", odds=("-400", "+300"))], LATE_QUOTE)
    out = coverage.record(conn, now=RECORDED)                              # recorded 23:05
    assert out["forecast"] >= 1, out


def test_canonical_record_carries_computation_time_and_lead_time_from_it(ready, client):
    _cycle_with_record_lag(ready)
    client.now = RECORDED
    body = client.get("/api/games", params={"date": "2026-10-21", "policy": "canonical"}).json()
    rec = next(g for g in body["games"] if g["game_id"] == G1)["canonical"]["record"]
    assert rec["generated_at"].startswith("2026-10-21T23:05")             # record time kept, never backdated
    assert rec["minutes_before_start"] == 60.0, rec["minutes_before_start"]  # snap3: 25.0 (record time)
    assert rec["computed_at"].startswith("2026-10-21T22:30")              # snap3: KeyError (field absent)


def test_winner_market_comparison_uses_the_quote_retrieved_by_the_computation(ready, client):
    _cycle_with_record_lag(ready)
    ingest_events(ready, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    client.now = "2026-10-22T04:00:00Z"
    picks = client.get("/api/canonical/ranked", params={"date": "2026-10-21"}).json()["picks"]
    w = next(p for p in picks if p["market"] == "winner")
    # -150/+130 retrieved 2026-10-19 (the quote the 22:30 computation could see): home de-vigged ~0.5798
    # picked side's bookmaker probability from that quote: 0.5798 (home) or 0.4202 (away)
    assert min(abs(w["market_prob"] - 0.5798), abs(w["market_prob"] - 0.4202)) < 2e-3, w["market_prob"]  # snap3: 0.238 (22:50 quote)
