"""scripts/learning_challengers_cycle.sh with a fake interpreter: per-sport JSON line, a failing sport does not stop the rest,
exit nonzero if any failed, one backup of an existing store, no backup on a dry run or when the store does not exist."""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "learning_challengers_cycle.sh"
FAKE = f"""#!/bin/sh
# fake interpreter: -c runs the real one; -m app.learning_run records the light call and runs what follows "--"
case "$1" in
  -c) exec {sys.executable} "$@" ;;
  -m)
    case "$2" in
      app.learning_run) echo "$*" >> "$LC_CALLS"; while [ "$1" != "--" ]; do shift; done; shift; exec "$@" ;;
      app.learning_challengers)
        sport=$5
        case " $LC_FAIL " in *" $sport "*) echo "boom $sport" >&2; echo '{{"status":"failed"}}'; exit 1 ;; esac
        echo '{{"status":"success","dry_run":false,"eligible_games":2,"created":3,"would_record":0,"already_recorded":1}}' ;;
    esac ;;
esac
"""


@pytest.fixture
def env(tmp_path):
    fake = tmp_path / "fakepy"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "data" / "research").mkdir(parents=True)
    return dict(os.environ, LC_PY=str(fake), LC_DATA=str(tmp_path / "data"), LC_BACKEND=str(tmp_path),
                LC_CALLS=str(tmp_path / "calls"), LC_FAIL="", TMPDIR=str(tmp_path))


def run(env, *args):
    r = subprocess.run(["/bin/sh", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
    return r, [json.loads(x) for x in r.stdout.splitlines()]


def test_one_json_line_per_sport_through_the_light_guard(env, tmp_path):
    r, lines = run(env)
    assert r.returncode == 0
    assert [x["sport"] for x in lines] == ["nba", "nhl", "ncaab", "soccer", "nfl"]
    assert all(x["rc"] == 0 and x["created"] == 3 and x["eligible_games"] == 2 and "wall_s" in x for x in lines)
    calls = (tmp_path / "calls").read_text().splitlines()
    assert len(calls) == 5 and all("--light" in c and "--max-minutes 2" in c and "--max-rss-gb 1.5" in c and "--nice 10" in c for c in calls)
    assert "--name challengers-soccer" in calls[3]


def test_a_failing_sport_does_not_stop_the_next_and_the_exit_is_nonzero(env):
    env["LC_FAIL"] = "nhl"
    r, lines = run(env)
    assert r.returncode == 1
    assert [x["rc"] for x in lines] == [0, 1, 0, 0, 0] and lines[1]["status"] == "failed"
    assert "boom nhl" in r.stderr


def test_an_existing_store_is_backed_up_once_and_dry_runs_and_missing_stores_are_not(env, tmp_path):
    research = tmp_path / "data" / "research"
    run(env)
    assert not list(research.glob("challengers.pre-cycle-*"))                       # no store yet: nothing to back up
    import sqlite3
    c = sqlite3.connect(research / "challengers.sqlite")
    c.execute("CREATE TABLE t (x)")
    c.execute("INSERT INTO t VALUES (1)")
    c.commit()
    c.close()
    run(env, "--dry-run")
    assert not list(research.glob("challengers.pre-cycle-*"))
    run(env)
    run(env)
    backups = list(research.glob("challengers.pre-cycle-*.sqlite"))
    assert len(backups) == 1
    assert sqlite3.connect(backups[0]).execute("SELECT x FROM t").fetchall() == [(1,)]


def test_extra_arguments_reach_the_recorder_and_sports_can_be_limited(env, tmp_path):
    env["LC_SPORTS"] = "nfl"
    r, lines = run(env, "--dry-run")
    assert [x["sport"] for x in lines] == ["nfl"]
    assert "--dry-run" in (tmp_path / "calls").read_text()
