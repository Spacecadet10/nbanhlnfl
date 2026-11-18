"""Research identities follow the same alias file as the agreement digest (reference/identity_aliases.json, key "research"):
the market_live.py change that only touches NCAAB branches must not restart NBA/NHL research or adaptive identities."""
from app import research_live
from test_live import store  # noqa: F401  (fixture)
from test_live_markets import sealed  # noqa: F401  (fixture: NHL store with its sealed decision)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture used by sealed)


def test_a_recorded_research_alias_keeps_the_earlier_identity_and_nothing_else_is_aliased(sealed, monkeypatch):
    monkeypatch.setattr(research_live, "_ALIASES", {})
    digest = research_live.definition(sealed)
    monkeypatch.setattr(research_live, "_ALIASES", {digest: "f862a44ddaa1e76f"})
    assert research_live.definition(sealed) == "f862a44ddaa1e76f"
    monkeypatch.setattr(research_live, "_ALIASES", {"0" * 16: "f862a44ddaa1e76f"})
    assert research_live.definition(sealed) == digest


def test_the_research_alias_entries_name_their_reason_and_ids():
    entries = research_live._load_aliases()
    assert set(entries.values()) == {"94844709fc4412a5", "f862a44ddaa1e76f"}
    for new, identity in entries.items():
        assert len(new) == 16 and len(identity) == 16


def test_an_alias_recorded_for_another_sport_does_not_apply(sealed, monkeypatch):
    monkeypatch.setattr(research_live, "_ALIASES", {})
    digest = research_live.definition(sealed)             # the sealed store is NHL
    monkeypatch.setattr(research_live, "_ALIASES", {digest: "94844709fc4412a5"})
    monkeypatch.setattr(research_live, "_ALIAS_SPORT", {digest: "nba"})
    assert research_live.definition(sealed) == digest
    monkeypatch.setattr(research_live, "_ALIAS_SPORT", {digest: "nhl"})
    assert research_live.definition(sealed) == "94844709fc4412a5"
