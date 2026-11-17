"""One-shot guards for reserved test runs, checked by the CLI before it calls the evaluation code.

The evaluation files are sealed: nfl_eval.py is part of the approved NFL activation (nfl_live.definition) and
soccer_markets_eval.py is hashed into each sealed soccer-markets candidate, so editing them would switch live forecasts
off. These checks therefore live here and run first. They also use evidence that survives a lost log (the Nov 15 reset
lost both the NFL test log and the soccer-markets logs): an NFL activation, a pinned soccer-markets candidate, and a
stored test reading.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path


class Refused(PermissionError):
    pass


def _entries(path: Path) -> list:
    if not Path(path).is_file():
        return []
    out = []
    for line in Path(path).read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# --- NFL ---------------------------------------------------------------------------------------------------------------
def nfl_registry() -> Path:
    """One record of NFL test runs for this machine, outside every data folder (a development copy and the full store
    share it). Override with NFL_EVAL_LOG_DIR."""
    root = os.environ.get("NFL_EVAL_LOG_DIR", Path.home() / "nba-predictor-research-data" / "nfl-eval-logs")
    return Path(root) / "nfl_test_runs.jsonl"


def nfl_test(access_log: Path, *, activation: Path) -> None:
    """Refuses the reserved NFL test if any test run was started before, whatever its seal or data folder, or if NFL is
    already activated (the activation holds a completed test's report)."""
    if any(str(e.get("purpose", "")).startswith("test run") for e in _entries(access_log)):
        raise Refused("a reserved NFL test run was already started in this data folder's log (one run, whatever the seal)")
    if _entries(nfl_registry()):
        raise Refused(f"a reserved NFL test run was already started on this machine ({nfl_registry()})")
    if Path(activation).exists():
        raise Refused("NFL is already activated from a completed test run; the reserved seasons are not read again")


def record_nfl_start(sealed: dict, *, data_dir: Path) -> None:
    path = nfl_registry()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "purpose": "test run: start",
                            "choice_sha256": sealed.get("sha256"), "data_dir": str(data_dir)}) + "\n")
        f.flush()
        os.fsync(f.fileno())


# --- Soccer markets ------------------------------------------------------------------------------------------------------
def _runs(family: str, experiments_log: Path) -> tuple:
    log = _entries(experiments_log)
    retired = {e.get("candidate_sha256") for e in log if e.get("stage") == "candidate_retired"}
    starts = [e for e in log if e.get("stage") == "develop_started" and e.get("family") == family]
    done = [e for e in log if e.get("stage") == "develop_completed" and e.get("family") == family]
    return starts, done, retired


def soccer_markets_develop(family: str, experiments_log: Path) -> None:
    """One development run per family: a started run counts even if it crashed after reading validation. A start is freed
    only by a completed run whose candidate was retired (a logged, user-approved protocol amendment)."""
    from . import soccer_markets_live as sml
    if sml.CANDIDATES.get(family):
        raise Refused(f"the {family} family already has a pinned candidate: its development run happened")
    starts, done, retired = _runs(family, experiments_log)
    freed = sum(1 for e in done if e.get("candidate_sha256") in retired)
    if len(starts) > freed:
        raise Refused(f"a {family} development run was already started ({len(starts)} start(s), {freed} retired); "
                      "it is not repeated, even after a crash, without a logged protocol amendment")


def soccer_markets_test(sealed: dict, experiments_log: Path) -> None:
    """The reserved test only for the seal of the single logged development run of its family, and only once."""
    from . import soccer_markets_live as sml
    family = sealed.get("family")
    if sml._file("reading", family).exists():
        raise Refused(f"the {family} reserved test has already been read; it is never repeated")
    starts, done, retired = _runs(family, experiments_log)
    live = [e for e in done if e.get("candidate_sha256") not in retired]
    freed = sum(1 for e in done if e.get("candidate_sha256") in retired)
    if len(starts) - freed != 1 or len(live) != 1:
        raise Refused(f"the {family} test needs exactly one logged development run (found {len(starts)} start(s), "
                      f"{len(live)} active completion(s))")
    if live[0].get("candidate_sha256") != sealed.get("sha256"):
        raise Refused(f"this seal was not produced by the logged {family} development run")
