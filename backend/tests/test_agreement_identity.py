"""Owner (Nov 17): an NBA injury-code change must not restart NHL's research identities. The injury files are hashed only
into NBA's agreement digest; NHL's digest after that change is aliased to its earlier identity (reference/identity_aliases.json)."""
from pathlib import Path

from app import agreement
from test_live import store  # noqa: F401  (fixture)
from test_live_markets import sealed  # noqa: F401  (fixture: NHL store with its sealed decision)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture used by sealed)

INJURY_FILES = {"availability.py", "availability_live.py"}


def _with_changed_injury_code(monkeypatch):
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: real(self) + (b"# changed" if self.name in INJURY_FILES else b""))


def test_injury_code_changes_the_nba_identity(store, monkeypatch):
    nba = agreement.models(store)
    before = agreement.definition_hash(nba)
    _with_changed_injury_code(monkeypatch)
    assert agreement.definition_hash(nba) != before


def test_injury_code_never_changes_the_nhl_identity(sealed, monkeypatch):
    nhl = agreement.models(sealed)
    before = agreement.definition_hash(nhl)
    _with_changed_injury_code(monkeypatch)
    assert agreement.definition_hash(nhl) == before


def test_a_recorded_alias_keeps_the_earlier_identity_and_nothing_else_is_aliased(sealed, monkeypatch):
    nhl = agreement.models(sealed)
    digest = agreement.definition_hash(nhl)
    monkeypatch.setattr(agreement, "_ALIASES", {digest: "d6f4f8d6b4f4fda8"})
    assert agreement.definition_hash(nhl) == "d6f4f8d6b4f4fda8"
    monkeypatch.setattr(agreement, "_ALIASES", {"0" * 16: "d6f4f8d6b4f4fda8"})
    assert agreement.definition_hash(nhl) == digest


def test_the_alias_file_names_its_reason_and_legacy_ids():
    for new, entry in agreement._load_aliases().items():
        assert len(new) == 16 and len(entry) == 16
