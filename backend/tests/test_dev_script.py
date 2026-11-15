"""scripts/dev.sh data-directory guard. Runs the real script from a disposable synthetic checkout with logging stubs for
every process-management and file-changing command, and proves that refused configurations exit before any of them.
Accepted configurations run the whole startup flow on fake directories (real mkdir/rm/cp/backup; servers stubbed)."""
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dev.sh"
# Anything here running would mean the script went past validation: stopping servers, copying, deleting, migrating.
DESTRUCTIVE = ("lsof", "kill", "ps", "mkdir", "rm", "cp", "curl", "npm", "date", "sleep", "mv", "ln")
SERVICES = ("lsof", "curl", "npm", "sleep")          # stubbed in full-flow runs; file commands run for real there
MARKER = ".nba-dev-data"
MARKER_TEXT = "nba-predictor development data copy; created by scripts/dev.sh, which may overwrite it\n"


def _store(path, value):
    c = sqlite3.connect(path)
    c.execute("create table t(x)")
    c.execute("insert into t values (?)", (value,))
    c.commit()
    c.close()


def _rows(path):
    c = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    try:
        return [r[0] for r in c.execute("select x from t")]
    finally:
        c.close()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "dev.sh")
    (root / "backend" / "app").mkdir(parents=True)          # the path check dev.sh runs (shared with the publisher)
    shutil.copy(SCRIPT.parents[1] / "backend" / "app" / "devpaths.py", root / "backend" / "app" / "devpaths.py")
    (root / "frontend" / "node_modules").mkdir(parents=True)
    log = tmp_path / "calls.log"
    venv = root / "backend" / ".venv" / "bin"
    venv.mkdir(parents=True)
    # The backend Python logs its arguments; only the validator runs for real (anything later is just recorded).
    (venv / "python").write_text(f'#!/bin/sh\necho "python $*" >> "{log}"\n'
                                 f'[ "${{2:-}}" = check-paths ] && exec "{sys.executable}" "$@"\nexit 0\n')
    (venv / "python").chmod(0o755)
    stubs, svc = tmp_path / "stubs", tmp_path / "svc"
    stubs.mkdir()
    svc.mkdir()
    for d, names in ((stubs, DESTRUCTIVE), (svc, SERVICES)):
        for name in names:
            (d / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
            (d / name).chmod(0o755)
    live = root / "data"
    (live / "research").mkdir(parents=True)
    _store(live / "nba.sqlite", "LIVE")
    (live / "research" / "keep.txt").write_text("live research")
    return {"root": root, "live": live, "log": log, "stubs": stubs, "svc": svc, "tmp": tmp_path,
            "live_bytes": (live / "nba.sqlite").read_bytes()}


def marked(path):
    """A development directory as the launcher itself creates it."""
    path.mkdir(parents=True, exist_ok=True)
    (path / MARKER).write_text(MARKER_TEXT)
    return path


def run(repo, dev, *args, live=None, cwd=None, extra_env=None, timeout=60):
    env = {"PATH": f"{repo['stubs']}:/usr/bin:/bin", "HOME": str(repo["tmp"]), "NBA_DEV_DATA_DIR": str(dev)}
    if live is not None:
        env["NBA_LIVE_DATA_DIR"] = str(live)
    env.update(extra_env or {})
    repo["log"].unlink(missing_ok=True)       # each run is checked on its own calls
    return subprocess.run(["/bin/sh", str(repo["root"] / "scripts" / "dev.sh"), *args], env=env,
                          cwd=cwd or repo["root"], capture_output=True, text=True, timeout=timeout)


def calls(repo):
    return repo["log"].read_text().splitlines() if repo["log"].exists() else []


def assert_live_unchanged(repo):
    assert (repo["live"] / "nba.sqlite").read_bytes() == repo["live_bytes"]
    assert (repo["live"] / "research" / "keep.txt").read_text() == "live research"
    # Exactly the live entries the test created (repo["live_extra"]: stores a test deliberately added), nothing written there.
    assert sorted(p.name for p in repo["live"].iterdir()) == sorted(["nba.sqlite", "research", *repo.get("live_extra", [])])


def assert_refused_before_anything(repo, proc, reason="refusing to continue"):
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert reason in proc.stderr, proc.stderr
    made = calls(repo)
    assert [c for c in made if c.split()[0] in DESTRUCTIVE] == []
    assert len(made) == 1 and made[0].startswith("python - check-paths "), made   # only the validation itself ran
    assert_live_unchanged(repo)


@pytest.mark.parametrize("dev", [
    "{live}",                 # same absolute path
    "./data",                 # relative alias (cwd = checkout root)
    "data/",
    "{live}/.",
    "{live}/../data",
    "{root}/scripts/../data",
    "{root}/DATA",            # case variant (same directory on case-insensitive volumes; refused everywhere)
])
def test_aliases_of_the_live_directory_are_refused(repo, dev):
    proc = run(repo, dev.format(live=repo["live"], root=repo["root"]))
    assert_refused_before_anything(repo, proc)


def test_symlink_to_the_live_directory_is_refused(repo):
    link = repo["tmp"] / "dev-link"
    link.symlink_to(repo["live"])
    assert_refused_before_anything(repo, run(repo, link))
    assert_refused_before_anything(repo, run(repo, link / "new-child"))


def test_symlinked_parent_resolving_into_live_is_refused(repo):
    link = repo["tmp"] / "parent-link"
    link.symlink_to(repo["root"])
    assert_refused_before_anything(repo, run(repo, link / "data"))


@pytest.mark.parametrize("sub", ["research", "research/new", "not-yet/created"])
def test_development_directory_inside_live_is_refused(repo, sub):
    assert_refused_before_anything(repo, run(repo, repo["live"] / sub))


def test_live_directory_inside_development_directory_is_refused(repo):
    assert_refused_before_anything(repo, run(repo, repo["root"]))       # <repo> contains <repo>/data
    assert_refused_before_anything(repo, run(repo, "/"))
    outer = repo["tmp"] / "outer"
    (outer / "live").mkdir(parents=True)
    _store(outer / "live" / "nba.sqlite", "OUTER")
    marked(outer)                                                       # even a marked directory may not contain live
    assert_refused_before_anything(repo, run(repo, outer, live=outer / "live"))


def test_uncertain_paths_are_refused(repo):
    dangling = repo["tmp"] / "dangling"
    dangling.symlink_to(repo["tmp"] / "missing-target")
    assert_refused_before_anything(repo, run(repo, dangling))
    assert_refused_before_anything(repo, run(repo, repo["tmp"] / "missing" / ".." / "repo" / "data"))
    loop = repo["tmp"] / "loop"
    loop.symlink_to(loop)
    assert_refused_before_anything(repo, run(repo, loop / "x"))
    afile = repo["tmp"] / "a-file"
    afile.write_text("")
    assert_refused_before_anything(repo, run(repo, afile))


@pytest.mark.parametrize("dev", ["{tmp}/dev-data", "{tmp}/new/nested/dev-data", "../dev-sibling", "{tmp}/with space/dev data"])
def test_new_separate_directory_is_accepted_and_check_only_touches_nothing(repo, dev):
    target = dev.format(tmp=repo["tmp"])
    proc = run(repo, target, "--check-paths")
    assert proc.returncode == 0, proc.stderr
    assert "data directories are separate" in proc.stdout
    made = calls(repo)
    assert len(made) == 1 and made[0].startswith("python - check-paths ")
    assert not (repo["root"] / target).exists()                         # nothing created (relative to the checkout)
    assert not (repo["tmp"] / "new").exists() and not (repo["tmp"] / "with space").exists()


def test_marked_existing_directory_is_accepted_by_check_only(repo):
    dev = marked(repo["tmp"] / "existing-dev")
    proc = run(repo, dev, "--check-paths")
    assert proc.returncode == 0, proc.stderr
    assert sorted(os.listdir(dev)) == [MARKER]


# --- Ownership: only a directory the launcher created (and marked) is ever reused --------------------------------------
def test_existing_unmarked_directories_are_refused(repo):
    empty = repo["tmp"] / "empty-dir"
    empty.mkdir()
    assert_refused_before_anything(repo, run(repo, empty), "not created by this launcher")
    assert_refused_before_anything(repo, run(repo, empty, "--check-paths"), "not created by this launcher")
    assert os.listdir(empty) == []                                      # never adopted or marked


def test_unrelated_valuable_directories_are_refused_and_untouched(repo):
    home = repo["tmp"] / "fake home"                                    # a home folder with same-named content
    (home / "research" / "thesis").mkdir(parents=True)
    (home / "research" / "thesis" / "ch1.md").write_text("precious")
    (home / "raw").mkdir()
    (home / "raw" / "photos.txt").write_text("precious")
    (home / "api.log").write_text("mine")
    _store(home / "nba.sqlite", "HOME")
    other = repo["tmp"] / "other-checkout" / "data"                     # another project's live data
    (other / "research").mkdir(parents=True)
    (other / "research" / "model.json").write_text("other")
    _store(other / "nba.sqlite", "OTHER")
    backup = repo["tmp"] / "backup-2026-10-01"                          # a backup folder
    backup.mkdir()
    _store(backup / "nba.sqlite", "BACKUP")
    snapshot = {p: p.read_bytes() for d in (home, other, backup) for p in d.rglob("*") if p.is_file()}
    for d in (home, other, backup):
        assert_refused_before_anything(repo, run(repo, d), "not created by this launcher")
    elsewhere = repo["tmp"] / "live-elsewhere"                          # the checkout root, with live data elsewhere
    shutil.copytree(repo["live"], elsewhere)
    proc = run(repo, repo["root"], live=elsewhere)
    assert proc.returncode != 0 and "not created by this launcher" in proc.stderr
    assert {p: p.read_bytes() for p in snapshot} == snapshot
    assert not (home / MARKER).exists() and not (other / MARKER).exists() and not (backup / MARKER).exists()
    assert not (repo["root"] / MARKER).exists()


@pytest.mark.parametrize("bad", ["wrong text", "extra", "symlink", "directory", "empty"])
def test_invalid_markers_are_refused(repo, bad):
    dev = repo["tmp"] / "dev"
    dev.mkdir()
    m = dev / MARKER
    if bad == "wrong text":
        m.write_text("something else\n")
    elif bad == "extra":
        m.write_text(MARKER_TEXT + "more\n")
    elif bad == "symlink":                                              # a valid marker elsewhere does not count
        good = marked(repo["tmp"] / "real-dev") / MARKER
        m.symlink_to(good)
    elif bad == "directory":
        m.mkdir()
    else:
        m.write_text("")
    assert_refused_before_anything(repo, run(repo, dev), "launcher marker")


def test_symlinked_development_directory_is_refused(repo):
    real = marked(repo["tmp"] / "real-dev")
    link = repo["tmp"] / "dev-alias"
    link.symlink_to(real)
    assert_refused_before_anything(repo, run(repo, link), "symlink")


# --- Write targets: nothing the launcher or the development app writes may lead elsewhere ------------------------------
WRITE_TARGETS = ["SNAPSHOT_AT", "api.log", "web.log", "publish.log", "PUBLISH_STATUS.json", ".publish-staging",
                 "soccer.sqlite", "soccer.sqlite-wal", ".soccer.sqlite.tmp", ".soccer-jobs.lock",
                 "nfl.sqlite", "nfl.sqlite-wal", ".nfl.sqlite.tmp", "nba.sqlite", "nba.sqlite-wal", "nba.sqlite-shm",
                 "nba.sqlite-journal", ".nba.sqlite.tmp", "nhl.sqlite", "nhl.sqlite-wal", "nhl.sqlite-shm",
                 ".nhl.sqlite.tmp", ".nba-jobs.lock", ".nhl-jobs.lock", "research", "raw", "eval", "reports",
                 "reports/diagnostics-nba", "research/tables", "research/adaptive/nba", "eval/test_access_nhl.jsonl",
                 "some-future-file"]
DIR_TARGETS = {".publish-staging", "research", "raw", "eval", "reports", "reports/diagnostics-nba", "research/tables", "research/adaptive/nba"}


@pytest.mark.parametrize("name", WRITE_TARGETS)
def test_symlinked_write_targets_in_development_directory_are_refused(repo, name):
    dev = marked(repo["tmp"] / "dev")
    link = dev / name
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(repo["live"] / ("research" if name in DIR_TARGETS else "nba.sqlite"))
    assert_refused_before_anything(repo, run(repo, dev), "symlink")
    assert link.is_symlink()                                            # reported, not removed


@pytest.mark.parametrize("name", ["SNAPSHOT_AT", "api.log", "web.log", "nba.sqlite", "nba.sqlite-wal", ".nba-jobs.lock"])
def test_non_regular_file_targets_are_refused(repo, name):
    dev = marked(repo["tmp"] / "dev")
    (dev / name).mkdir()
    assert_refused_before_anything(repo, run(repo, dev), "not a regular file")


@pytest.mark.parametrize("name", ["research", "raw", "eval", "reports"])
def test_non_directory_targets_are_refused(repo, name):
    dev = marked(repo["tmp"] / "dev")
    (dev / name).write_text("x")
    assert_refused_before_anything(repo, run(repo, dev), "not a directory")


@pytest.mark.parametrize("name", ["SNAPSHOT_AT", "api.log", "web.log", "nba.sqlite", ".nba-jobs.lock"])
def test_hard_linked_write_targets_are_refused(repo, name):
    # Written in place (shell redirects, SQLite): a second link would carry the write to a file outside the directory.
    outside = repo["tmp"] / "outside.txt"
    outside.write_text("precious")
    dev = marked(repo["tmp"] / "dev")
    os.link(outside, dev / name)
    assert_refused_before_anything(repo, run(repo, dev), "hard link")
    assert outside.read_text() == "precious"


@pytest.mark.parametrize("suffix", ["/.", "/./", "/.."])
def test_trailing_dot_alias_of_development_directory_is_refused(repo, suffix):
    # islink("<link>/.") is false, so a trailing '.' would hide that the directory is reached through a symlink.
    real = marked(repo["tmp"] / "real-dev")
    link = repo["tmp"] / "dev-alias"
    link.symlink_to(real)
    assert_refused_before_anything(repo, run(repo, f"{link}{suffix}"), "'.' or '..'")
    assert_refused_before_anything(repo, run(repo, f"{real}{suffix}", "--check-paths"), "'.' or '..'")
    assert sorted(os.listdir(real)) == [MARKER]


def test_marker_replaced_by_fifo_after_type_check_is_refused_without_hanging(repo):
    # Deterministic stand-in for a swap between the marker's lstat and its open: lstat sees the regular marker, then the
    # file becomes a FIFO. A blocking open would wait forever for a writer; the opened file's own type must be checked.
    site = repo["tmp"] / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        "import os\n_lstat = os.lstat\n"
        "def lstat(p, *a, **k):\n"
        "    st = _lstat(p, *a, **k)\n"
        "    if str(p).endswith('.nba-dev-data') and not os.path.exists(str(p) + '.swapped'):\n"
        "        os.unlink(p); os.mkfifo(p); open(str(p) + '.swapped', 'w').close()\n"
        "    return st\n"
        "os.lstat = lstat\n")
    dev = marked(repo["tmp"] / "dev")
    try:
        proc = run(repo, dev, extra_env={"PYTHONPATH": str(site)}, timeout=20)
    except subprocess.TimeoutExpired:
        os.close(os.open(dev / MARKER, os.O_WRONLY | os.O_NONBLOCK))      # release the blocked validator
        pytest.fail("validator blocked opening a FIFO marker")
    assert_refused_before_anything(repo, proc, "not a regular file")                 # the opened file's own type


def test_symlink_inside_live_copied_trees_is_refused(repo):
    # cp -R copies links as links: the development app could then write through them into live.
    (repo["live"] / "research" / "latest").symlink_to(repo["live"] / "research" / "keep.txt")
    dev = marked(repo["tmp"] / "dev")
    proc = run(repo, dev)
    assert proc.returncode != 0 and "symlink" in proc.stderr
    assert [c for c in calls(repo) if c.split()[0] in DESTRUCTIVE] == []
    assert len(calls(repo)) == 1
    assert sorted(os.listdir(dev)) == [MARKER]


def test_refused_configuration_without_check_flag_still_never_reaches_port_or_copy_steps(repo):
    # Full (non --check-paths) invocation: the stubs would record lsof/kill/mkdir/cp/rm/python copy or migrate calls.
    proc = run(repo, "./data")
    assert_refused_before_anything(repo, proc)


# --- Full accepted startup flow on fake directories: every later step must use exactly the validated directories ----
def _full_run(repo, dev, live=None, cwd=None, before_create="", extra_env=None, api_seconds=0):
    """Runs the whole script. Validation, directory creation, the read-only backup and the research copy really run
    (on fake directories); migrate and uvicorn are recorded with their working directory and data-directory variables
    and exit; lsof/curl/npm/sleep are stubs. `before_create` is shell run just before the create step's Python starts.
    Returns the process and one row per backend Python call."""
    trace = repo["tmp"] / "trace.log"
    trace.unlink(missing_ok=True)
    repo["log"].unlink(missing_ok=True)
    py = repo["root"] / "backend" / ".venv" / "bin" / "python"
    py.write_text("#!/bin/sh\n"
                  f'printf "%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n" "$1 $2" "$(pwd -P)" "${{NBA_DATA_DIR:-}}" '
                  f'"${{NBA_LIVE_DATA_DIR:-}}" "${{2:-}}" "${{3:-}}" "$*" >> "{trace}"\n'
                  f'[ "${{2:-}}" = create-dev ] && {{ :; {before_create}\n}}\n'
                  'case "${2:-}" in check-paths|create-dev|/*) exec ' f'"{sys.executable}" "$@" ;; esac\n'
                  f'[ "${{2:-}}" = uvicorn ] && /bin/sleep {api_seconds}\n'
                  "exit 0\n")
    env = {"PATH": f"{repo['svc']}:/usr/bin:/bin", "HOME": str(repo["tmp"]), "NBA_DEV_DATA_DIR": str(dev),
           **(extra_env or {})}
    if live is not None:
        env["NBA_LIVE_DATA_DIR"] = str(live)
    proc = subprocess.run(["/bin/sh", str(repo["root"] / "scripts" / "dev.sh")], env=env, cwd=cwd or repo["root"],
                          capture_output=True, text=True, timeout=60)
    rows = [line.split("\t") for line in trace.read_text().splitlines()] if trace.exists() else []
    return proc, rows


def _resolve(cwd, p):
    return os.path.realpath(os.path.join(cwd, p))


def _assert_targets(repo, proc, rows, dev_dir, cwd=None):
    assert "dev.sh: ready" in proc.stdout, (proc.stdout, proc.stderr, rows)
    dev_real, live_real = os.path.realpath(dev_dir), os.path.realpath(repo["live"])
    kinds = {}
    for call, run_cwd, data_dir, live_env, a2, a3, *_ in rows:
        kinds.setdefault(call, []).append((run_cwd, data_dir, live_env, a2, a3))
    assert "-m app.cli" in kinds and "-m uvicorn" in kinds, rows
    backups = [r for c, rs in kinds.items() if c.startswith("- /") for r in rs]
    assert len(backups) == 1, rows
    for run_cwd, _, _, src, dst in backups:                       # read-only backup: live → development
        assert _resolve(run_cwd, src) == live_real and _resolve(run_cwd, dst) == dev_real
    for run_cwd, data_dir, _, _, _ in kinds["-m app.cli"]:        # migrate runs from backend/
        assert _resolve(run_cwd, data_dir) == dev_real != live_real
    for run_cwd, data_dir, live_env, _, _ in kinds["-m uvicorn"]:  # API serves the copy, reads training status from live
        assert _resolve(run_cwd, data_dir) == dev_real != live_real
        assert _resolve(run_cwd, live_env) == live_real
    # What actually landed where: the copy, the snapshot time and both logs are regular files in the development dir.
    dev = Path(dev_real)
    assert _rows(dev / "nba.sqlite") == ["LIVE"]
    assert (dev / "research" / "keep.txt").read_text() == "live research"
    for name in ("SNAPSHOT_AT", "api.log", "web.log"):
        assert (dev / name).is_file() and not (dev / name).is_symlink(), name
    assert (dev / "SNAPSHOT_AT").read_text().strip().endswith("Z")
    assert (dev / MARKER).read_text() == MARKER_TEXT
    for elsewhere in {repo["root"], repo["root"] / "backend", repo["root"] / "frontend", Path(cwd or repo["root"])}:
        for name in ("SNAPSHOT_AT", "api.log", "web.log", "nba.sqlite", MARKER):
            assert not (elsewhere / name).exists(), elsewhere / name
    assert_live_unchanged(repo)


def test_new_development_directory_is_created_marked_and_used(repo):
    work = repo["tmp"] / "work dir"
    work.mkdir()
    dev = repo["tmp"] / "new parent" / "dev data"                       # spaces; neither component exists yet
    proc, rows = _full_run(repo, "../new parent/dev data", cwd=work)
    assert [r[0] for r in rows].count("- create-dev") == 1, rows
    _assert_targets(repo, proc, rows, dev, cwd=work)


def test_marked_directory_is_reused_on_repeated_startup(repo):
    dev = repo["tmp"] / "dev data"
    proc, rows = _full_run(repo, dev)
    _assert_targets(repo, proc, rows, dev)
    marker_id = os.stat(dev / MARKER).st_ino
    (dev / "research" / "keep.txt").write_text("edited in the copy")   # the next start must refresh it from live
    proc, rows = _full_run(repo, dev)
    assert "- create-dev" not in [r[0] for r in rows], rows             # reused, not re-created or re-marked
    _assert_targets(repo, proc, rows, dev)
    assert os.stat(dev / MARKER).st_ino == marker_id


def test_directory_created_by_someone_else_after_the_check_is_not_adopted(repo):
    # Between the first check (absent) and creation, an unrelated directory appears: it must be refused, not marked.
    dev = repo["tmp"] / "racy"
    lsof = repo["svc"] / "lsof"
    lsof.write_text(f'#!/bin/sh\nmkdir -p "{dev}"; echo precious > "{dev}/keep"\n')
    proc, rows = _full_run(repo, dev)
    assert proc.returncode != 0
    assert "dev.sh: ready" not in proc.stdout
    assert sorted(os.listdir(dev)) == ["keep"] and (dev / "keep").read_text() == "precious\n"
    assert "-m app.cli" not in [r[0] for r in rows] and not any(r[0].startswith("- /") for r in rows)
    assert_live_unchanged(repo)


def test_directory_appearing_just_before_creation_is_not_adopted(repo):
    # Narrower race: the directory appears after the shell's existence test, as the create step starts. os.mkdir must
    # fail rather than reuse it, and no marker may be written into it.
    dev = repo["tmp"] / "racy dir"
    proc, rows = _full_run(repo, dev, before_create=f'mkdir -p "{dev}"; echo precious > "{dev}/keep"')
    assert proc.returncode != 0 and "could not create a new development data directory" in proc.stderr, proc.stderr
    assert sorted(os.listdir(dev)) == ["keep"] and (dev / "keep").read_text() == "precious\n"
    assert "-m app.cli" not in [r[0] for r in rows] and not any(r[0].startswith("- /") for r in rows)
    assert_live_unchanged(repo)


@pytest.fixture
def dev_copy(repo):
    def make(path):
        marked(path)
        _store(path / "nba.sqlite", "OLD DEV COPY")
        return path
    return make


def test_relative_development_dir_parent_of_checkout_is_used_as_validated(repo, dev_copy):
    # From the checkout root, ../data is a sibling of the checkout (separate) — but from backend/ it would be <repo>/data.
    dev = dev_copy(repo["tmp"] / "data")
    proc, rows = _full_run(repo, "../data")
    _assert_targets(repo, proc, rows, dev)


def test_relative_development_dir_from_another_cwd_is_used_as_validated(repo, dev_copy):
    dev = dev_copy(repo["tmp"] / "dev-rel")
    proc, rows = _full_run(repo, "dev-rel", cwd=repo["tmp"])
    _assert_targets(repo, proc, rows, dev, cwd=repo["tmp"])


def test_relative_live_dir_is_used_as_validated(repo, dev_copy):
    dev = dev_copy(repo["tmp"] / "dev-abs")
    proc, rows = _full_run(repo, dev, live="data")              # relative to the checkout root, not to backend/
    _assert_targets(repo, proc, rows, dev)
    proc, rows = _full_run(repo, dev, live="repo/data", cwd=repo["tmp"])
    _assert_targets(repo, proc, rows, dev, cwd=repo["tmp"])


def test_separate_absolute_development_dir_is_used_as_validated(repo, dev_copy):
    dev = dev_copy(repo["tmp"] / "elsewhere" / "dev-data")
    proc, rows = _full_run(repo, dev)
    _assert_targets(repo, proc, rows, dev)


# --- After the copy: the re-check must stop the run before migrate, SNAPSHOT_AT and the servers ---------------------
def _cp_wrapper(repo, script):
    """Replaces cp in the full run (service stubs come first on PATH) with `script`; "$@" are cp's arguments."""
    w = repo["svc"] / "cp"
    w.write_text("#!/bin/sh\n" + script + "\n")
    w.chmod(0o755)


def _stopped_before_migrate(repo, proc, rows, dev):
    assert proc.returncode != 0 and "refusing to continue" in proc.stderr, (proc.stdout, proc.stderr)
    assert "-m app.cli" not in [r[0] for r in rows] and "-m uvicorn" not in [r[0] for r in rows], rows
    for name in ("SNAPSHOT_AT", "api.log", "web.log"):
        assert not (Path(dev) / name).exists(), name
    assert_live_unchanged(repo)


def test_symlink_planted_in_copy_is_caught_after_the_copy(repo):
    dev = repo["tmp"] / "dev"
    _cp_wrapper(repo, f'/bin/cp "$@" || exit $?\nln -s "{repo["live"]}/nba.sqlite" "{dev}/research/to-live"')
    _stopped_before_migrate(repo, *_full_run(repo, dev), dev)


def test_symlink_reaching_live_after_the_check_is_carried_in_and_caught(repo):
    # The live tree gains a link after the pre-copy check; cp -R copies it as a link into the development directory.
    dev = repo["tmp"] / "dev"
    late = repo["live"] / "research" / "late-link"
    _cp_wrapper(repo, f'[ -L "{late}" ] || ln -s keep.txt "{late}"\n/bin/cp "$@"')
    proc, rows = _full_run(repo, dev)
    late.unlink()
    assert (dev / "research" / "late-link").is_symlink()
    _stopped_before_migrate(repo, proc, rows, dev)


def test_marker_removed_during_copy_is_caught_after_the_copy(repo):
    dev = repo["tmp"] / "dev"
    _cp_wrapper(repo, f'/bin/cp "$@" || exit $?\n/bin/rm -f "{dev}/{MARKER}"')
    _stopped_before_migrate(repo, *_full_run(repo, dev), dev)


# --- Copy correctness ---------------------------------------------------------------------------------------------
def test_failed_clone_leaving_a_partial_copy_falls_back_without_nesting(repo):
    # The clone (cp -cR) fails after creating part of the destination; the plain copy must start from a clean target,
    # not copy into it (which would give <dev>/research/research and keep the partial files).
    dev = repo["tmp"] / "dev"
    _cp_wrapper(repo, 'if [ "$1" = -cR ]; then mkdir -p "$3"; echo partial > "$3/partial"; exit 1; fi\nexec /bin/cp "$@"')
    proc, rows = _full_run(repo, dev)
    _assert_targets(repo, proc, rows, dev)
    assert sorted(os.listdir(dev / "research")) == ["keep.txt"]


def test_missing_live_sources_remove_stale_development_counterparts(repo):
    # Live has nba.sqlite and research only. Earlier copies of nhl.sqlite (+ its journal files), raw/ and eval/ must not
    # be served under a fresh SNAPSHOT_AT; files the launcher does not manage are left alone.
    dev = marked(repo["tmp"] / "dev")
    _store(dev / "nhl.sqlite", "STALE NHL")
    _store(dev / "soccer.sqlite", "STALE SOCCER")
    for name in ("nhl.sqlite-wal", "nhl.sqlite-shm", "nhl.sqlite-journal", ".nhl.sqlite.tmp",
                 "soccer.sqlite-wal", "soccer.sqlite-shm", "soccer.sqlite-journal", ".soccer.sqlite.tmp"):
        (dev / name).write_text("stale")
    for sub in ("raw", "eval", "research"):
        (dev / sub).mkdir()
        (dev / sub / "stale.txt").write_text("stale")
    (dev / "reports").mkdir()
    (dev / "reports" / "r.json").write_text("{}")
    (dev / "notes.txt").write_text("mine")
    proc, rows = _full_run(repo, dev)
    _assert_targets(repo, proc, rows, dev)
    left = sorted(os.listdir(dev))
    assert not [n for n in left if "nhl" in n or "soccer" in n] and "raw" not in left and "eval" not in left, left
    assert sorted(os.listdir(dev / "research")) == ["keep.txt"]
    assert (dev / "notes.txt").read_text() == "mine" and (dev / "reports" / "r.json").read_text() == "{}"
    migrations = [r for r in rows if r[0] == "-m app.cli" and r[5] != "preview-publish"]
    assert len(migrations) == 1, rows                                        # nba only; no stale nhl copy migrated


def test_live_soccer_store_is_copied_and_migrated_with_the_soccer_command(repo):
    # The data-only soccer store (app/soccer.py) is copied like the others and migrated through `app.cli soccer migrate`
    # (never `--sport soccer`, which the served-sport CLI refuses), against the copy only.
    _store(repo["live"] / "soccer.sqlite", "LIVE SOCCER")
    live_soccer = (repo["live"] / "soccer.sqlite").read_bytes()
    repo["live_extra"] = ["soccer.sqlite"]
    dev = repo["tmp"] / "dev"
    proc, rows = _full_run(repo, dev)
    _assert_targets(repo, proc, rows, dev)
    assert _rows(dev / "soccer.sqlite") == ["LIVE SOCCER"]
    soccer = [r for r in rows if r[0] == "-m app.cli" and r[5] == "soccer"]
    assert len(soccer) == 1 and r_args(soccer[0]) == "-m app.cli soccer migrate", rows
    assert os.path.realpath(soccer[0][2]) == os.path.realpath(dev)
    assert (repo["live"] / "soccer.sqlite").read_bytes() == live_soccer


def r_args(row):
    return row[6]


def test_wal_mode_live_store_without_side_files_is_copied_and_left_unchanged(repo):
    """The live stores are WAL-mode and their -wal/-shm files vanish when the last job closes. The read-only backup must
    still copy them (checked 2026-11-01 with the backend's SQLite 3.50: a mode=ro reader creates the usual empty -wal and
    -shm side files, which the next job reuses) and must never change the live database file itself."""
    live_db = repo["live"] / "nba.sqlite"
    live_db.unlink()
    c = sqlite3.connect(live_db)
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("create table t(x)")
    c.execute("insert into t values ('LIVE')")
    c.commit()
    c.close()                                   # last connection: checkpoint, -wal and -shm removed
    assert sorted(p.name for p in repo["live"].iterdir()) == ["nba.sqlite", "research"]
    before = live_db.read_bytes()
    dev = repo["tmp"] / "dev wal"
    proc, rows = _full_run(repo, str(dev))
    assert "dev.sh: ready" in proc.stdout, (proc.stdout, proc.stderr)
    copy = sqlite3.connect(dev / "nba.sqlite")
    assert [r[0] for r in copy.execute("select x from t")] == ["LIVE"]
    copy.close()
    assert live_db.read_bytes() == before
    extra = sorted(p.name for p in repo["live"].iterdir() if p.name not in ("nba.sqlite", "research"))
    assert set(extra) <= {"nba.sqlite-wal", "nba.sqlite-shm"}, extra
    wal = repo["live"] / "nba.sqlite-wal"
    assert not wal.exists() or wal.stat().st_size == 0


# --- Republishing (docs/REFRESH_PLAN.md): the publisher runs on the validated directories only, and can be turned off ---
def _publisher_rows(repo, dev, extra_env=None, wait=5.0):
    import time
    (repo["svc"] / "npm").write_text("#!/bin/sh\n/bin/sleep 1\n")          # the stub servers stay up for a moment
    proc, rows = _full_run(repo, dev, extra_env=extra_env, api_seconds=1)
    trace = repo["tmp"] / "trace.log"
    deadline = time.time() + wait            # the publisher starts in the background; give it a moment to be recorded
    found = []
    while time.time() < deadline:
        found = [line.split("\t") for line in trace.read_text().splitlines() if "preview-publish" in line]
        if found or extra_env:
            break
        time.sleep(0.1)
    return proc, rows, found


def test_publisher_republishes_into_the_validated_development_directory_only(repo):
    dev = marked(repo["tmp"] / "dev")
    proc, rows, found = _publisher_rows(repo, dev)
    _assert_targets(repo, proc, rows, dev)
    assert len(found) == 1, (found, proc.stdout, proc.stderr, (repo["tmp"] / "trace.log").read_text())
    call, run_cwd, data_dir, _, _, _, args = found[0]
    dev_real, live_real = os.path.realpath(dev), os.path.realpath(repo["live"])
    assert call == "-m app.cli" and _resolve(run_cwd, data_dir) == dev_real
    argv = args.split()
    assert _resolve(run_cwd, argv[argv.index("--dev") + 1]) == dev_real
    assert _resolve(run_cwd, argv[argv.index("--live") + 1]) == live_real != dev_real
    assert argv[argv.index("--interval") + 1] == "60"
    assert not (dev / "PUBLISH_STATUS.json").exists()        # a previous run's status is removed at startup


def test_publisher_can_be_turned_off_for_a_frozen_copy(repo):
    dev = marked(repo["tmp"] / "dev")
    (dev / "PUBLISH_STATUS.json").write_text('{"interval_seconds": 60}')
    proc, rows, found = _publisher_rows(repo, dev, extra_env={"NBA_DEV_PUBLISH": "0"})
    _assert_targets(repo, proc, rows, dev)
    assert found == [] and not (dev / "PUBLISH_STATUS.json").exists()


def test_the_publisher_checks_the_same_marker_text_dev_sh_writes():
    from app import devpaths
    assert f'DEV_MARK="{devpaths.MARK_TEXT}"' in SCRIPT.read_text()
    assert MARKER_TEXT == devpaths.MARK_TEXT + "\n"


@pytest.mark.parametrize("interval", ["10", "29", "abc", "60s", "-60", "3.5"])
def test_invalid_publish_interval_is_refused_before_anything(repo, interval):
    # The publisher refuses intervals under 30 s and exits; the launcher used to start anyway and still announce that copies
    # are republished. An invalid value must stop the launch with a clear message before any server or file is touched.
    dev = marked(repo["tmp"] / "dev")
    proc = run(repo, dev, extra_env={"PUBLISH_INTERVAL": interval})
    assert_refused_before_anything(repo, proc, "PUBLISH_INTERVAL")


def test_publish_interval_is_not_checked_when_publishing_is_off(repo):
    dev = repo["tmp"] / "dev"
    proc, rows = _full_run(repo, dev, extra_env={"PUBLISH_INTERVAL": "10", "NBA_DEV_PUBLISH": "0"})
    _assert_targets(repo, proc, rows, dev)
    assert "republished" not in proc.stdout and not [r for r in rows if r[5] == "preview-publish"]
