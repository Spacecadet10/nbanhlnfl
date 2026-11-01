"""Frozen paper rules (app/paper_rules.py, reference/paper_rules.json): the file is sealed, a rule bets the pick or the
opposite side at the price the forecast could see, only forecasts made after the freeze count, and nothing is a verdict
before the regular season is over. Synthetic NBA store, no network."""
import hashlib
import json

import pytest

from app import paper_rules
from test_canonical import AFTER, record_now
from test_live import T_GAME, event, ingest_events
from test_live import client, store  # noqa: F401  (fixtures)
from test_near_tipoff import ready  # noqa: F401  (fixture)

SPEC = {"frozen_at": "2026-10-01T00:00:00Z", "season_types": ["regular"], "min_bets": 100}


def rule(**kw):
    return {"id": "t", "sport": "nba", "market": "winner", "min_confidence": 0.65, "side": "pick", **kw}


def test_the_committed_rules_are_sealed_and_a_changed_file_is_refused(tmp_path, monkeypatch):
    spec = paper_rules.load()
    assert hashlib.sha256(paper_rules.PATH.read_bytes()).hexdigest() == paper_rules.FROZEN_SHA256
    assert spec["frozen_at"] == "2026-11-01T06:30:00Z" and spec["min_bets"] == 100
    assert {(r["id"], r["sport"], r["market"], r["min_confidence"], r["side"]) for r in spec["rules"]} == {
        ("nhl-winner-65", "nhl", "winner", 0.65, "pick"), ("nba-spread-against-65", "nba", "spread", 0.65, "opposite")}
    changed = tmp_path / "paper_rules.json"
    changed.write_text(json.dumps({**spec, "min_bets": 50}))
    monkeypatch.setattr(paper_rules, "PATH", changed)
    with pytest.raises(ValueError, match="changed after it was frozen"):
        paper_rules.load()


def settled(conn):
    record_now(conn)
    ingest_events(conn, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")


def test_a_rule_bets_the_pick_or_the_opposite_side_at_the_price_the_forecast_could_see(ready):
    settled(ready)
    [pick], _ = paper_rules.bets(ready, rule(), SPEC, AFTER)
    [opp], _ = paper_rules.bets(ready, rule(side="opposite"), SPEC, AFTER)
    assert pick["side"] != opp["side"] and {pick["grade"], opp["grade"]} == {"win", "loss"}
    # The quote retrieved before the forecast: home -150, away +130 (decimal 1.6667 / 2.30).
    prices = {"a": 1 + 100 / 150, "b": 2.30}
    assert pick["price"] == pytest.approx(prices[pick["side"]]) and opp["price"] == pytest.approx(prices[opp["side"]])
    for b in (pick, opp):
        assert b["profit"] == pytest.approx(b["price"] - 1 if b["grade"] == "win" else -1.0)


def test_only_forecasts_after_the_freeze_above_the_threshold_and_at_the_bookmakers_line_count(ready):
    settled(ready)
    assert paper_rules.bets(ready, rule(min_confidence=0.95), SPEC, AFTER)[1]["below threshold"] == 1
    late = {**SPEC, "frozen_at": "2026-10-21T22:45:00Z"}          # the forecast was made at 22:30, before this freeze
    bets, skipped = paper_rules.bets(ready, rule(), late, AFTER)
    assert bets == [] and skipped["made before the freeze"] == 1
    bets, skipped = paper_rules.bets(ready, rule(market="spread", min_confidence=0.5), SPEC, AFTER)
    assert bets == [] and skipped["not at the bookmaker's line"] == 1     # the synthetic spread is at the model's line


def test_nothing_is_a_verdict_before_the_season_is_over_and_too_few_bets_are_inconclusive():
    win = {"profit": 0.5, "tipoff": "2026-11-02T00:00:00Z"}
    lose = {"profit": -1.0, "tipoff": "2026-11-09T00:00:00Z"}
    assert paper_rules.verdict([win] * 150, 100, final=False)[0] == "interim"
    assert paper_rules.verdict([win] * 20, 100, final=True)[0] == "inconclusive"
    assert paper_rules.verdict([win] * 150, 100, final=True)[0] == "pass"
    assert paper_rules.verdict([win, lose] * 75, 100, final=True)[0] == "fail"      # mean -0.25 per bet


def test_the_api_reports_the_sports_rules_since_the_freeze(ready, client):
    settled(ready)
    client.now = AFTER
    body = client.get("/api/paper-rules").json()
    assert body["frozen_at"] == "2026-11-01T06:30:00Z" and [r["id"] for r in body["rules"]] == ["nba-spread-against-65"]
    r = body["rules"][0]
    assert r["bets"] == 0 and r["state"] == "interim"          # the synthetic games are before the freeze
