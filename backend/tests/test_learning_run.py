"""The learning-run guard (app/learning_run.py): one heavy job at a time, bounded time and memory, never in a game window."""
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
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
    games_store(data, "nhl", 6.0, -5.0)                       # later than 2.5 h, and one that started over 4 h ago
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


# ---- review fixes -------------------------------------------------------------------------------------------------------
def test_a_game_in_progress_defers_but_a_final_or_long_past_one_does_not(tmp_path):
    data = tmp_path / "data"
    games_store(data, "nba", -1.0)                                  # tipped off 1 h ago, not final: in progress
    assert go(tmp_path, [PY, "-c", "pass"]) == lr.EXIT_REFUSED
    assert "nba0" in events(tmp_path)[-1]["reason"]
    (data / "nba.sqlite").unlink()
    games_store(data, "nba", -1.0, status="final")
    assert go(tmp_path, [PY, "-c", "pass"]) == 0
    (data / "nba.sqlite").unlink()
    games_store(data, "nba", -5.0)                                  # past the in-progress horizon
    assert go(tmp_path, [PY, "-c", "pass"]) == 0


def test_guard_exit_codes_avoid_the_shell_126_127_and_the_timeout_124_125_conventions():
    codes = {lr.EXIT_USAGE, lr.EXIT_REFUSED, lr.EXIT_TIMEOUT, lr.EXIT_MEMORY, lr.EXIT_UNMEASURABLE, lr.EXIT_NOT_STARTED}
    assert len(codes) == 6 and not codes & {124, 125, 126, 127}
    assert all(120 <= c <= 123 for c in (lr.EXIT_TIMEOUT, lr.EXIT_MEMORY, lr.EXIT_UNMEASURABLE, lr.EXIT_NOT_STARTED))
    for c in ("120", "121", "122", "123"):
        assert c in lr.__doc__


def test_wall_deadline_also_uses_the_wall_clock(tmp_path, monkeypatch):
    real = time.time
    base = real()
    calls = {"n": 0}

    def jump():                                          # the machine "slept": wall clock jumps an hour, monotonic does not
        calls["n"] += 1
        return base + (3600 if calls["n"] > 3 else 0)
    monkeypatch.setattr(lr.time, "time", jump)
    assert go(tmp_path, [PY, "-c", "import time; time.sleep(30)"], max_minutes=5) == lr.EXIT_TIMEOUT


def test_the_lock_outlives_a_sigkilled_guard_while_the_child_lives(tmp_path):
    data = tmp_path / "data"
    code = (f"import sys; sys.path.insert(0, {str(Path(lr.__file__).parents[1])!r}); from app import learning_run as lr; "
            f"from pathlib import Path; lr.run_guarded([{PY!r}, '-c', 'import time; time.sleep(30)'], name='k', max_minutes=1, max_rss_gb=4, "
            f"data_dir=Path({str(data)!r}), poll_s=0.05)")
    guard = subprocess.Popen([PY, "-c", code], start_new_session=True)
    try:
        log = data / "research" / "learning-runs.jsonl"
        for _ in range(100):
            if log.exists() and "start" in log.read_text():
                break
            time.sleep(0.1)
        child = json.loads(log.read_text().splitlines()[0])["pid"]
        os.kill(guard.pid, 9)
        guard.wait()
        fd = os.open(data / ".learning-heavy.lock", os.O_RDWR)
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)      # a surviving child still holds it
        os.close(fd)
        os.killpg(child, 9)
        time.sleep(0.5)
        fd = os.open(data / ".learning-heavy.lock", os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)          # freed once the child is gone
        os.close(fd)
    finally:
        try:
            os.killpg(child, 9)
        except Exception:
            pass


# ---- light mode (prospective recorders) -------------------------------------------------------------------------------
def test_light_runs_inside_a_game_window_and_while_the_heavy_lock_is_held(tmp_path):
    games_store(tmp_path / "data", "nba", 1.0)                 # tips off in 1 h: a heavy job is refused
    assert go(tmp_path, [PY, "-c", "pass"]) == lr.EXIT_REFUSED
    lock = tmp_path / "data" / ".learning-heavy.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert go(tmp_path, [PY, "-c", "pass"], light=True) == 0
    finally:
        os.close(fd)
    assert events(tmp_path)[-1]["event"] == "end"


def test_light_does_not_take_the_heavy_lock_and_refuses_only_the_same_name(tmp_path):
    marker = tmp_path / "heavy_rc"
    code = (f"import sys; sys.path.insert(0, {str(Path(lr.__file__).parent.parent)!r}); from app import learning_run as lr; "
            f"rc = lr.run_guarded([sys.executable, '-c', 'pass'], name='h', max_minutes=1, max_rss_gb=4, data_dir={str(tmp_path / 'data')!r}); "
            f"open({str(marker)!r}, 'w').write(str(rc))")
    # a light child that, while it lives, runs a heavy job (must be admitted) and a same-name light job (must be refused)
    inner = f"import subprocess, sys; subprocess.run([sys.executable, '-c', {code!r}])"
    assert go(tmp_path, [PY, "-c", inner], light=True) == 0
    assert marker.read_text() == "0"
    lockp = tmp_path / "data" / ".learning-light-t.lock"
    lockp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lockp, os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert go(tmp_path, [PY, "-c", "pass"], light=True) == lr.EXIT_REFUSED
        assert lr.run_guarded([PY, "-c", "pass"], name="other", max_minutes=1, max_rss_gb=4, data_dir=tmp_path / "data", light=True) == 0
    finally:
        os.close(fd)


def test_light_keeps_the_wall_clock_and_memory_caps(tmp_path):
    assert go(tmp_path, [PY, "-c", "import time; time.sleep(30)"], light=True, max_minutes=0.01) == lr.EXIT_TIMEOUT
    assert go(tmp_path, [PY, "-c", "import time; time.sleep(30)"], light=True, rss_reader=lambda pid: 10 * 1024 ** 3) == lr.EXIT_MEMORY


def test_cli_light_flag(tmp_path):
    games_store(tmp_path / "data", "nba", 1.0)
    env = dict(os.environ, NBA_DATA_DIR=str(tmp_path / "data"), PYTHONDONTWRITEBYTECODE="1")
    base = [PY, "-m", "app.learning_run", "--name", "x", "--max-minutes", "1", "--max-rss-gb", "2"]
    cwd = str(Path(lr.__file__).parent.parent)
    # nba tips off within the default window only if the clock is near it; the fixture is fixed to 2026-11-18, so check the flag parses
    r = subprocess.run(base + ["--light", "--", PY, "-c", "import sys; sys.exit(5)"], env=env, capture_output=True, text=True, cwd=cwd)
    assert r.returncode == 5
