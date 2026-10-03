"""Record counts: every selected forecast is exactly one of settled / pending / void / unscorable (synthetic stores only).

Winner:  selected = settled + pending + void + unscorable, settled = wins + losses.
Markets: selected = wins + losses + pushes + voids + pending + unscorable, settled = wins + losses + pushes.
Pending means awaiting a final result; a final game that cannot be scored is unscorable, never pending."""
import pytest

from test_api_markets import DAY, api  # noqa: F401  (fixtures)
from test_live import client, confidence_store, event, history, ingest_events, store  # noqa: F401  (fixtures)
from test_live_markets import add_upcoming, quote, sealed  # noqa: F401
from test_nhl_eval import nhl_store  # noqa: F401

WINNER_KEYS = {"min_confidence", "selected", "settled", "wins", "losses", "pending", "void", "unscorable", "win_rate"}


def winner_identity(rec):
    assert set(rec) == WINNER_KEYS
    assert rec["selected"] == rec["settled"] + rec["pending"] + rec["void"] + rec["unscorable"]
    assert rec["settled"] == rec["wins"] + rec["losses"]


def market_identity(rec):
    assert {"pending", "voids", "unscorable", "pushes"} <= set(rec)
    assert rec["selected"] == (rec["wins"] + rec["losses"] + rec["pushes"] + rec["voids"] + rec["pending"]
                               + rec["unscorable"])
    assert rec["settled"] == rec["wins"] + rec["losses"] + rec["pushes"]


def test_winner_record_separates_pending_and_void(store, client):
    confidence_store(store)                      # c1-c4 final, c5 and c6 scheduled
    ingest_events(store, [event("c6", "2026-11-15T00:30:00Z", "5", "1", "STATUS_POSTPONED")],
                  "2026-11-21T12:00:00Z", month="202611")
    rec = history(client)["record"]
    winner_identity(rec)
    assert (rec["selected"], rec["settled"], rec["pending"], rec["void"], rec["unscorable"]) == (6, 4, 1, 1, 0)
    for level in (0.6, 0.7, 0.8, 0.9):           # the identity holds at every confidence level, not only for All
        winner_identity(history(client, min_confidence=level)["record"])
    one = history(client, limit=1)
    assert len(one["predictions"]) == 1 and one["record"] == rec       # the record covers every row, not the page


def test_winner_record_counts_a_moved_game_as_void_not_pending(store, client):
    confidence_store(store)
    # c5 moves to another Eastern date after its forecast: the night-before-style row is void, never pending.
    ingest_events(store, [event("c5", "2026-11-25T00:30:00Z", "2", "4")], "2026-11-21T12:00:00Z", month="202611")
    rec = history(client)["record"]
    winner_identity(rec)
    assert (rec["pending"], rec["void"]) == (1, 1)


@pytest.fixture
def settled_markets(api, sealed):
    """Game 1 is final but its regulation decision is unknown; game 2's start moved after the forecasts."""
    add_upcoming(sealed, gid=2026020001, start="2026-10-11T19:00:00", state=7, hs=3, vs=1, period=3,
                 at="2026-10-12T02:00:00Z")
    sealed.execute("DELETE FROM game_decisions WHERE game_id = 'nhl:2026020001'")   # the source gave no decision
    add_upcoming(sealed, gid=2026020002, start="2026-10-11T23:00:00", home=8, away=6, at="2026-10-11T12:00:00Z")
    return api


def test_reg3_final_without_decision_is_unscorable_not_pending(settled_markets):
    rec = settled_markets.get("/api/market-predictions", params={"sport": "nhl", "market": "reg3"}).json()["record"]
    market_identity(rec)
    assert (rec["selected"], rec["unscorable"], rec["voids"], rec["pending"], rec["settled"]) == (2, 1, 1, 0, 0)
    perf = settled_markets.get("/api/market-performance", params={"sport": "nhl", "market": "reg3"}).json()["record"]
    assert perf == rec


def test_line_market_record_identity(settled_markets):
    for market in ("spread", "total", "team_total_home", "team_total_away"):
        rec = settled_markets.get("/api/market-predictions", params={"sport": "nhl", "market": market}).json()["record"]
        market_identity(rec)
        assert (rec["selected"], rec["settled"], rec["voids"], rec["pending"], rec["unscorable"]) == (2, 1, 1, 0, 0)
