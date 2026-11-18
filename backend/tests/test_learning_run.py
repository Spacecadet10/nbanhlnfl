"""The learning-run guard (app/learning_run.py): one heavy job at a time, bounded time and memory, never in a game window."""
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import learning_run as lr

PY = sys.executable
NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)


def ts(hours):
    return (NOW + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def games_store(data, sport, *offsets, table="games", col="tipoff_at", status="scheduled"):
    data.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(data / f"{sport}.sqlite")
    c.execute(f"CREATE TABLE IF NOT EXISTS {table} (game_id TEXT PRIMARY KEY, {col} TEXT, status TEXT)")
    for i, h in enumerate(offsets):
        c.execute(f"INSERT INTO {table} VALUES (?,?,?)", (f"{sport}{i}", ts(h), status))
    c.commit()
    c.close()


def go(tmp_path, argv, **kw):
    kw.setdefault("max_minutes", 1)
    kw.setdefault("max_rss_gb", 4)
    kw.setdefault("poll_s", 0.05)
    kw.setdefault("now", NOW)
    return lr.run_guarded(argv, name="t", data_dir=tmp_path / "data", **kw)


def events(tmp_path):
    p = tmp_path / "data" / "research" / "learning-runs.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()]


def test_child_rc_is_returned_and_the_log_has_start_and_end(tmp_path):
    assert go(tmp_path, [PY, "-c", "import sys; sys.exit(3)"]) == 3
    ev = events(tmp_path)
    assert [e["event"] for e in ev] == ["start", "end"]
    assert ev[0]["argv"][0] == PY and ev[0]["name"] == "t" and ev[1]["rc"] == 3
    assert ev[1]["wall_s"] >= 0 and "peak_rss_mb" in ev[1] and ev[1]["outcome"] == "exited"


def test_a_held_lock_refuses_and_does_not_queue(tmp_path):
    lock = tmp_path / "data" / ".learning-heavy.lock"
    lock.parent.mkdir(parents=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    marker = tmp_path / "ran"
    t0 = time.monotonic()
    rc = go(tmp_path, [PY, "-c", f"open({str(marker)!r},'w')"])
    assert rc == lr.EXIT_REFUSED and not marker.exists() and time.monotonic() - t0 < 5
    e = events(tmp_path)
    assert [x["event"] for x in e] == ["refused"] and "lock" in e[0]["reason"]
    os.close(fd)
    assert go(tmp_path, [PY, "-c", "pass"]) == 0           # released: runs


def test_game_in_window_refuses_and_games_outside_do_not(tmp_path):
    data = tmp_path / "data"
    games_store(data, "nba", 1.0)
    games_store(data, "nhl", 6.0, -1.0)                       # later than 2.5 h, and one already started
    marker = tmp_path / "ran"
    assert go(tmp_path, [PY, "-c", f"open({str(marker)!r},'w')"]) == lr.EXIT_REFUSED and not marker.exists()
    e = events(tmp_path)[0]
    assert e["event"] == "refused" and "nba" in e["reason"] and "nba0" in e["reason"]
    assert go(tmp_path, [PY, "-c", "pass"], defer_hours=0.5) == 0        # 1 h away is outside a 0.5 h window


def test_other_sports_are_only_checked_when_asked(tmp_path):
    data = tmp_path / "data"
    games_store(data, "ncaab", 2.0)
    games_store(data, "soccer", 1.0, table="soccer_matches", col="kickoff_at")
    assert go(tmp_path, [PY, "-c", "pass"], defer_sports=("nba", "nhl")) == 0
    assert go(tmp_path, [PY, "-c", "pass"]) == lr.EXIT_REFUSED                  # ncaab is in the default set
    assert go(tmp_path, [PY, "-c", "pass"], defer_sports=("soccer",)) == lr.EXIT_REFUSED


def test_unreadable_store_fails_closed(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "nba.sqlite").write_bytes(b"not a database at all" * 50)
    assert go(tmp_path, [PY, "-c", "pass"]) == lr.EXIT_REFUSED
    assert "nba" in events(tmp_path)[0]["reason"]


def test_missing_stores_are_not_a_window(tmp_path):
    assert go(tmp_path, [PY, "-c", "pass"]) == 0


def test_wall_clock_limit_kills_the_whole_group(tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    code = ("import subprocess,sys,time; p=subprocess.Popen(['sleep','60']); "
            f"open({str(pidfile)!r},'w').write(str(p.pid)); time.sleep(60)")
    t0 = time.monotonic()
    rc = go(tmp_path, [PY, "-c", code], max_minutes=0.03)
    assert rc == lr.EXIT_TIMEOUT and time.monotonic() - t0 < 30
    pid = int(pidfile.read_text())
    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert events(tmp_path)[-1]["outcome"] == "killed: wall-clock limit"


def test_rss_cap_kills_the_group(tmp_path):
    rc = go(tmp_path, [PY, "-c", "import time; time.sleep(60)"], max_rss_gb=1.0, rss_reader=lambda pgid: 2 * 1024 ** 3)
    assert rc == lr.EXIT_MEMORY
    e = events(tmp_path)[-1]
    assert e["outcome"].startswith("killed: memory") and e["peak_rss_mb"] >= 2048


def test_unreadable_memory_fails_closed(tmp_path):
    rc = go(tmp_path, [PY, "-c", "import time; time.sleep(60)"], rss_reader=lambda pgid: None)
    assert rc == lr.EXIT_UNMEASURABLE
    assert "cannot be read" in events(tmp_path)[-1]["outcome"]


def test_real_rss_reader_sees_a_running_group():
    p = subprocess.Popen([PY, "-c", "import time; time.sleep(5)"], start_new_session=True)
    try:
        time.sleep(0.3)
        assert lr.group_rss_bytes(os.getpgid(p.pid)) > 1_000_000
    finally:
        os.killpg(p.pid, 9)
        p.wait()


def test_nice_and_thread_caps_reach_the_child_and_explicit_values_win(tmp_path, monkeypatch):
    out = tmp_path / "env.json"
    code = f"import os,json; json.dump([os.nice(0), os.environ.get('OMP_NUM_THREADS'), os.environ.get('MKL_NUM_THREADS'), os.environ.get('OPENBLAS_NUM_THREADS')], open({str(out)!r},'w'))"
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    monkeypatch.setenv("MKL_NUM_THREADS", "7")
    assert go(tmp_path, [PY, "-c", code], nice=12) == 0
    n, omp, mkl, ob = json.loads(out.read_text())
    assert n >= 12 and omp == "2" and mkl == "7" and ob == "2"


def test_command_not_found_is_logged(tmp_path):
    assert go(tmp_path, ["/nonexistent/binary"]) == lr.EXIT_NOT_STARTED
    assert events(tmp_path)[-1]["event"] == "end"


def test_cli_requires_limits_and_a_command(tmp_path):
    env = dict(os.environ, NBA_DATA_DIR=str(tmp_path / "data"), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([PY, "-m", "app.learning_run", "--name", "x", "--max-minutes", "1", "--", PY, "-c", "pass"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == lr.EXIT_USAGE                       # --max-rss-gb missing
    r = subprocess.run([PY, "-m", "app.learning_run", "--name", "x", "--max-minutes", "1", "--max-rss-gb", "2", "--",
                        PY, "-c", "import sys; sys.exit(5)"], env=env, capture_output=True, text=True)
    assert r.returncode == 5
