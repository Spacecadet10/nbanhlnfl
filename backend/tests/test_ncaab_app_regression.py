"""Adding NCAAB must change nothing for the other four sports (docs/NCAAB_PLAN.md "Non-negotiables"): the NFL activation
definition, the NBA and NHL agreement identities, every sealed or hashed file byte-for-byte, and the market specs of the
other sports. The reference for bytes is the commit `ncaab` (the plan commit this work branched from) and, for the agreement
identities, the main commit it was cut from (BASE); both are read with `git show`. Without git history (an exported copy) the
git comparisons are skipped and the committed hashes below are the only reference."""
import dataclasses
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from app import agreement, markets, nfl_live, sports
from test_live import store  # noqa: F401  (fixture: NBA store)
from test_live_markets import sealed  # noqa: F401  (fixture: NHL store with its sealed decision)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture used by sealed)

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
BASE = "16c17c3"          # main when NCAAB work began
PLAN_COMMIT = "ncaab"
ACTIVATION = BACKEND / "tests" / "fixtures" / "nfl_activation_2026-11-02.json"

# Hashed or sealed files that NCAAB must never edit (docs/NCAAB_PLAN.md): the NFL activation set, the agreement identity
# (backtest, agreement, availability*, every model file), the best-pick recorder, the soccer evaluation files, metrics, and
# every frozen decision.
SEALED = ["app/coverage.py", "app/markets.py", "app/models/base.py", "app/models/elo.py", "app/models/points.py",
          "app/nfl_live.py", "app/nfl_eval.py", "app/backtest.py", "app/agreement.py", "app/availability.py",
          "app/availability_live.py", "app/availability_quality.py", "app/best_pick_record.py", "app/soccer_eval.py",
          "app/soccer_history.py", "app/soccer_clubs.py", "app/soccer_markets_eval.py", "app/metrics.py"]


def _git(*args) -> bytes | None:
    try:
        out = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, check=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout


def _have(ref: str) -> bool:
    return _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}") is not None


def _sealed_paths() -> list[str]:
    models = sorted(p.relative_to(BACKEND).as_posix() for p in (BACKEND / "app" / "models").iterdir()
                    if p.is_file() and p.suffix in (".py", ".json") and p.name not in NEW_NCAAB_FILES)
    return sorted(set(SEALED + models))


# The only new files NCAAB adds under app/models (the model agent's; never part of another sport's identity).
NEW_NCAAB_FILES = {"college.py", "ncaab_frozen_decision.json"}


def test_the_nfl_activation_definition_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    target = nfl_live.activation_path()
    target.parent.mkdir(parents=True)
    target.write_bytes(ACTIVATION.read_bytes())
    assert json.loads(ACTIVATION.read_text())["definition"] == "b6074877a7e7ccfe"
    assert nfl_live.definition() == "b6074877a7e7ccfe"
    assert nfl_live.activation() is not None


@pytest.mark.skipif(not _have(PLAN_COMMIT), reason="no git history with the `ncaab` plan commit here")
@pytest.mark.parametrize("path", _sealed_paths())
def test_a_sealed_or_hashed_file_is_byte_identical_to_the_plan_commit(path):
    expected = _git("show", f"{PLAN_COMMIT}:backend/{path}")
    assert expected is not None, f"{path} is not in {PLAN_COMMIT}"
    assert (BACKEND / path).read_bytes() == expected, f"{path} was edited: NFL, NBA or NHL identities would change"


def test_the_sealed_list_covers_every_model_and_decision_file():
    names = {Path(p).name for p in _sealed_paths() if p.startswith("app/models/")}
    assert {"base.py", "elo.py", "points.py", "frozen_decision.json"} <= names
    assert all(n.endswith(".json") for n in names if "decision" in n)
    assert "college.py" not in names and "ncaab_frozen_decision.json" not in names


def _as_at_base(monkeypatch):
    """Every backend/app file read through Path.read_bytes is read from BASE instead (the other files are untouched)."""
    real = Path.read_bytes
    app_dir = BACKEND / "app"

    def read(self):
        try:
            relative = Path(self).resolve().relative_to(REPO.resolve()).as_posix()
        except ValueError:
            return real(self)
        if Path(self).resolve().is_relative_to(app_dir.resolve()):
            old = _git("show", f"{BASE}:{relative}")
            if old is not None:
                return old
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", read)


@pytest.mark.skipif(not _have(BASE), reason="no git history with the base commit here")
def test_the_base_reader_is_not_vacuous(monkeypatch):
    live_py = BACKEND / "app" / "live.py"
    edited = live_py.read_bytes()
    _as_at_base(monkeypatch)
    assert live_py.read_bytes() == _git("show", f"{BASE}:backend/app/live.py") != edited       # live.py gained an NCAAB engine branch


@pytest.mark.skipif(not _have(BASE), reason="no git history with the base commit here")
def test_nba_agreement_definition_is_unchanged_since_main(store, monkeypatch):  # noqa: F811
    members = agreement.models(store)
    now = agreement.definition_hash(members)
    _as_at_base(monkeypatch)
    assert agreement.definition_hash(members) == now


@pytest.mark.skipif(not _have(BASE), reason="no git history with the base commit here")
def test_nhl_agreement_definition_is_unchanged_since_main(sealed, monkeypatch):  # noqa: F811
    members = agreement.models(sealed)
    now = agreement.definition_hash(members)
    _as_at_base(monkeypatch)
    assert agreement.definition_hash(members) == now


def test_market_specs_of_the_other_sports_are_unchanged():
    by_sport = {s: {m: markets.spec(s, m).overtime for (sp, m) in markets.SPEC if sp == s}
                for s in ("nba", "nhl", "nfl")}
    assert by_sport["nba"] == {m: "overtime counts" for m in ("winner", "spread", "total", "team_total_home", "team_total_away")}
    assert set(by_sport["nhl"]) == {"winner", "reg3", "spread", "total", "team_total_home", "team_total_away"}
    assert set(by_sport["nfl"]) == {"winner", "spread", "total", "team_total_home", "team_total_away"}
    assert all(v == "overtime counts; winner ties void" for v in by_sport["nfl"].values())
    assert {s for s, _ in markets.SPEC} == {"nba", "nhl", "nfl", "ncaab"}
    for sport in ("nba", "nhl", "nfl"):
        assert ({k: dataclasses.astuple(v) for k, v in markets.SPEC.items() if k[0] == sport}
                == {k: dataclasses.astuple(v) for k, v in _base_specs().items() if k[0] == sport})


def _base_specs() -> dict:
    """markets.SPEC as the sealed module itself defines it (the dict literal), before ncaab_markets adds its entries."""
    source = (_git("show", f"{BASE}:backend/app/markets.py") or (BACKEND / "app" / "markets.py").read_bytes()).decode()
    namespace: dict = {}
    code = source[:source.index("def spec(")]
    exec(compile(code, "markets_at_base", "exec"), namespace)      # noqa: S102  (a literal table from a committed file)
    return namespace["SPEC"]


def test_ncaab_specs_only_add_entries_and_markets_py_defines_none_of_them():
    assert not any(k[0] == "ncaab" for k in _base_specs())
    assert {m for (s, m) in markets.SPEC if s == "ncaab"} == set(sports.NCAAB.markets)
    assert hashlib.sha256((BACKEND / "app" / "markets.py").read_bytes()).hexdigest() == hashlib.sha256(
        _git("show", f"{PLAN_COMMIT}:backend/app/markets.py") or (BACKEND / "app" / "markets.py").read_bytes()).hexdigest()


def test_served_sports_still_refuse_unknown_and_data_only_ones():
    assert sports.SPORT_IDS == ("nba", "nhl", "ncaab") and set(sports.DATA_ONLY) == {"nfl", "soccer"}
    for bad in ("nfl", "soccer", "cricket", ""):
        with pytest.raises(sports.UnsupportedSport):
            sports.get(bad)
    assert sports.store("ncaab").db_file == "ncaab.sqlite"
