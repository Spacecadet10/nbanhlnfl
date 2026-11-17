"""One-shot guards for reserved test runs (app/test_run_guard.py), enforced at the CLI without editing the sealed
evaluation files. Review findings (Nov 15): the NFL test could be repeated with a new seal or another data folder's log;
soccer markets allowed a repeated development run after a crash and a test of a seal no logged development produced."""
import json

import pytest

from app import soccer_markets_live as sml
from app import test_run_guard as guard


def _write(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))


# --- NFL -------------------------------------------------------------------------------------------------------------
def test_nfl_is_refused_after_any_earlier_test_run_whatever_its_seal(tmp_path):
    log = tmp_path / "access.jsonl"
    _write(log, [{"choice_sha256": "aaa", "purpose": "test run: start"}])
    with pytest.raises(guard.Refused):
        guard.nfl_test(log, activation=tmp_path / "none.json")


def test_nfl_is_refused_when_another_data_folder_already_ran_it(tmp_path):
    guard.record_nfl_start({"sha256": "aaa"}, data_dir=tmp_path / "copy")      # machine-wide registry (tmp in tests)
    with pytest.raises(guard.Refused):
        guard.nfl_test(tmp_path / "fresh-log.jsonl", activation=tmp_path / "none.json")


def test_nfl_is_refused_when_already_activated_even_if_the_logs_were_lost(tmp_path):
    act = tmp_path / "activation.json"
    act.write_text("{}")
    with pytest.raises(guard.Refused):
        guard.nfl_test(tmp_path / "fresh-log.jsonl", activation=act)


def test_nfl_first_run_is_allowed_and_initialization_reads_do_not_count(tmp_path):
    log = tmp_path / "access.jsonl"
    _write(log, [{"choice_sha256": "aaa", "purpose": "prospective NFL state initialization"}])
    guard.nfl_test(log, activation=tmp_path / "none.json")                     # no exception


# --- Soccer markets ----------------------------------------------------------------------------------------------------
@pytest.fixture
def no_pins(monkeypatch, tmp_path):
    monkeypatch.setattr(sml, "CANDIDATES", {})
    monkeypatch.setattr(sml, "HERE", tmp_path / "app")
    return tmp_path


def test_a_started_development_run_blocks_another_even_if_it_never_completed(no_pins):
    xlog = no_pins / "experiments.jsonl"
    _write(xlog, [{"stage": "develop_started", "family": "goals"}])             # crashed after reading validation
    with pytest.raises(guard.Refused):
        guard.soccer_markets_develop("goals", xlog)
    guard.soccer_markets_develop("corners", xlog)                               # another family is unaffected


def test_a_retired_completed_run_frees_its_start(no_pins):
    xlog = no_pins / "experiments.jsonl"
    _write(xlog, [{"stage": "develop_started", "family": "goals"},
                  {"stage": "develop_completed", "family": "goals", "candidate_sha256": "c1"},
                  {"stage": "candidate_retired", "candidate_sha256": "c1"}])
    guard.soccer_markets_develop("goals", xlog)


def test_a_pinned_candidate_proves_development_happened_even_if_the_log_was_lost(monkeypatch, tmp_path):
    monkeypatch.setattr(sml, "CANDIDATES", {"goals": "c1"})
    with pytest.raises(guard.Refused):
        guard.soccer_markets_develop("goals", tmp_path / "lost.jsonl")


def test_the_test_needs_the_seal_of_the_single_logged_development_run(no_pins):
    xlog = no_pins / "experiments.jsonl"
    _write(xlog, [{"stage": "develop_started", "family": "goals"},
                  {"stage": "develop_completed", "family": "goals", "candidate_sha256": "c1"}])
    guard.soccer_markets_test({"family": "goals", "sha256": "c1"}, xlog)        # allowed
    with pytest.raises(guard.Refused):                                          # a seal no logged run produced
        guard.soccer_markets_test({"family": "goals", "sha256": "other"}, xlog)
    _write(xlog, [{"stage": "develop_started", "family": "goals"}, {"stage": "develop_started", "family": "goals"},
                  {"stage": "develop_completed", "family": "goals", "candidate_sha256": "c1"}])
    with pytest.raises(guard.Refused):                                          # tuned over aborted runs
        guard.soccer_markets_test({"family": "goals", "sha256": "c1"}, xlog)


def test_the_test_is_refused_once_its_reading_exists(no_pins):
    xlog = no_pins / "experiments.jsonl"
    _write(xlog, [{"stage": "develop_started", "family": "goals"},
                  {"stage": "develop_completed", "family": "goals", "candidate_sha256": "c1"}])
    (no_pins / "app").mkdir()
    (no_pins / "app" / "soccer_markets_reading_goals.json").write_text("{}")
    with pytest.raises(guard.Refused):
        guard.soccer_markets_test({"family": "goals", "sha256": "c1"}, xlog)
