"""Live-store guard (audit 2026-11-07, H1 and C4/I9).

H1: the data folder of a main checkout (REPO/data: the live NBA, NHL and NFL stores) is never opened for writing, migrated
or created by a development command. Only the verified release environment (release.check problems other than the schema
line are empty: NBA_RELEASE_GUARD=1, clean release checkout named by NBA_RELEASE_ROOT, NBA_DATA_DIR set) may write there,
and only the explicit `migrate` command may migrate. A refusal is a JSON line with status "refused" and exit 1, before any
lock, connection, directory or file. Copies (scripts/dev.sh), tests and in-memory databases are unaffected.

C4/I9: `replay` / `nfl replay` / `soccer replay` check, read-only, that every cached response they will read exists before
the CLI migrates the store or replays anything; `release-check` lists the missing files; release.check() itself does not
scan them. Temporary directories only: a fake main checkout is `tmp/repo/.git/` (a directory) + `tmp/repo/data`."""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from app import cli, config, db, ingest, release, soccer
from test_nfl import nfl_event, nfl_payload
from test_soccer import event as soccer_event, payload as soccer_payload
from test_storage import event, payload


# --- helpers ----------------------------------------------------------------------------------------------------------
def git_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README").write_text("release\n")
    subprocess.run(["git", "-C", str(path), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "r"], check=True)
    return path


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A fake main checkout whose data folder is the default data directory (NBA_DATA_DIR unset), outside any release."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "data").mkdir()
    monkeypatch.setattr(config, "REPO_ROOT", repo)
    for key in ("NBA_DATA_DIR", release.GUARD_ENV, release.ROOT_ENV):
        monkeypatch.delenv(key, raising=False)
    assert config.data_dir() == repo / "data"
    return repo / "data"


def build_store(tmp_path, dest, sport, upto=None):
    """A migrated, claimed store built outside the protected folder, then copied into `dest` (closed, no WAL left)."""
    build = tmp_path / "build" / f"{sport}-{upto}.sqlite"
    build.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(build)
    db.migrate(conn, upto=upto)
    db.claim_store(conn, sport)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.close()
    shutil.copy(build, dest)
    return dest


def schema(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    finally:
        conn.close()


def tree(path):
    """Every file under `path` with its content hash: nothing created, changed or removed."""
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(path.rglob("*")) if p.is_file()}


def dump(path):
    """Every row of every table, read-only."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {t: sorted(map(repr, conn.execute(f'SELECT * FROM "{t}"'))) for t in tables}
    finally:
        conn.close()


def run(argv, capsys):
    """cli.main's exit code (or the exception it raised) and its last JSON line."""
    try:
        rc = cli.main(argv)
    except (Exception, SystemExit) as exc:          # the unguarded code raises instead of refusing
        rc = f"{type(exc).__name__}: {exc}"
    out = [line for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    try:
        last = json.loads(out[-1]) if out else {}
    except json.JSONDecodeError:
        last = {}
    return rc, last


def no_run(monkeypatch):
    """The command's own work is not under test: only what happens to the store before it."""
    monkeypatch.setattr(cli, "_run", lambda *a, **k: 0)


# --- H1: protected data folder ----------------------------------------------------------------------------------------
def test_tests_never_default_to_the_checkout_data_dir():
    """conftest.py: a test that sets no NBA_DATA_DIR gets a temporary one, never REPO/data."""
    assert os.environ.get("NBA_DATA_DIR")
    assert config.data_dir().resolve() != (config.REPO_ROOT / "data").resolve()
    assert not config.protected_data_dir(config.data_dir())


def test_protected_data_dir_is_a_main_checkout_data_folder_only(tmp_path, monkeypatch):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text("gitdir: elsewhere\n")          # a linked worktree has a .git FILE
    monkeypatch.setattr(config, "REPO_ROOT", linked)
    assert config.protected_data_dir(main / "data")              # any main checkout's data folder
    assert config.protected_data_dir(linked / "data")            # this checkout's own default data folder
    assert not config.protected_data_dir(tmp_path / "copy")      # dev copies, scratch, tests
    assert not config.protected_data_dir(tmp_path / "other" / "data")


def test_implicit_command_never_migrates_the_checkout_store(tmp_path, checkout, monkeypatch, capsys):
    store = build_store(tmp_path, checkout / "nhl.sqlite", "nhl", upto=release.shipped_schema() - 1)
    before = tree(checkout)
    no_run(monkeypatch)
    rc, out = run(["--sport", "nhl", "score"], capsys)
    assert schema(store) == release.shipped_schema() - 1
    assert tree(checkout) == before                               # no lock file, no WAL, no change
    assert (rc, out.get("status")) == (1, "refused") and "protected" in out["reason"]


def test_explicit_nba_data_dir_naming_the_checkout_store_is_refused(tmp_path, checkout, monkeypatch, capsys):
    store = build_store(tmp_path, checkout / "nhl.sqlite", "nhl", upto=release.shipped_schema() - 1)
    monkeypatch.setenv("NBA_DATA_DIR", str(checkout))
    no_run(monkeypatch)
    rc, out = run(["--sport", "nhl", "score"], capsys)
    assert schema(store) == release.shipped_schema() - 1
    assert (rc, out.get("status")) == (1, "refused")


def test_explicit_migrate_outside_a_release_checkout_is_refused(tmp_path, checkout, capsys):
    nhl = build_store(tmp_path, checkout / "nhl.sqlite", "nhl", upto=release.shipped_schema() - 1)
    nfl = build_store(tmp_path, checkout / "nfl.sqlite", "nfl", upto=release.shipped_schema() - 1)
    before = tree(checkout)
    for argv, store in ((["--sport", "nhl", "migrate"], nhl), (["nfl", "migrate"], nfl)):
        rc, out = run(argv, capsys)
        assert schema(store) == release.shipped_schema() - 1, argv
        assert (rc, out.get("status")) == (1, "refused"), argv
    assert tree(checkout) == before


def test_no_store_file_is_created_in_a_protected_dir(checkout, monkeypatch, capsys):
    def no_network(*a, **k):
        raise AssertionError("a refused command must not make a request")
    monkeypatch.setattr(soccer, "ingest_range", no_network)
    for argv in (["soccer", "ingest", "--from", "20261101", "--to", "20261102"], ["soccer", "migrate"], ["migrate"],
                 ["nfl", "ingest", "--from", "202410", "--to", "202410"]):
        rc, out = run(argv, capsys)
        assert (rc, out.get("status")) == (1, "refused"), argv
    assert list(checkout.iterdir()) == []                         # no store, no lock file, no raw folder


def test_dev_checkout_write_on_protected_store_at_shipped_schema_is_refused(tmp_path, checkout, monkeypatch, capsys):
    """After a release the live stores are at this checkout's schema: a dev write must still be refused (CHALLENGE M1)."""
    for sport in ("nba", "nhl", "nfl", "soccer"):
        build_store(tmp_path, checkout / f"{sport}.sqlite", sport)
    before = tree(checkout)
    no_run(monkeypatch)
    monkeypatch.setattr(ingest, "replay", lambda *a, **k: pytest.fail("replay ran on a protected store"))
    monkeypatch.setattr(soccer, "replay", lambda *a, **k: pytest.fail("replay ran on a protected store"))
    for argv in (["--sport", "nhl", "score"], ["predict"], ["--sport", "nhl", "odds", "--force"], ["replay"],
                 ["nfl", "replay"], ["nfl", "refresh"], ["soccer", "replay"], ["tuning-study"], ["deep-study"],
                 ["--sport", "nhl", "adaptive-train"]):
        rc, out = run(argv, capsys)
        assert (rc, out.get("status")) == (1, "refused"), (argv, rc, out)
    assert tree(checkout) == before


def test_read_only_commands_still_read_a_protected_store(tmp_path, checkout, capsys):
    store = build_store(tmp_path, checkout / "nhl.sqlite", "nhl")
    rc, out = run(["--sport", "nhl", "coverage"], capsys)
    assert rc == 0 and out.get("status") != "refused"
    assert schema(store) == release.shipped_schema()


def test_release_migration_context_may_migrate(tmp_path, checkout, monkeypatch, capsys):
    """The documented release migration: release checkout + NBA_RELEASE_GUARD/ROOT + NBA_DATA_DIR=the live folder."""
    store = build_store(tmp_path, checkout / "nhl.sqlite", "nhl", upto=release.shipped_schema() - 1)
    root = git_repo(tmp_path / "release")
    monkeypatch.setattr(release, "code_root", lambda: root)
    monkeypatch.setenv(release.GUARD_ENV, "1")
    monkeypatch.setenv(release.ROOT_ENV, str(root))
    monkeypatch.setenv("NBA_DATA_DIR", str(checkout))
    real_run = cli._run
    no_run(monkeypatch)
    rc, out = run(["--sport", "nhl", "adaptive-record"], capsys)  # an unguarded implicit command never migrates
    assert (rc, out.get("status")) == (1, "refused") and schema(store) == release.shipped_schema() - 1
    monkeypatch.setattr(cli, "_run", real_run)
    assert cli.main(["--sport", "nhl", "migrate"]) == 0
    assert schema(store) == release.shipped_schema()
    no_run(monkeypatch)
    assert cli.main(["--sport", "nhl", "score"]) == 0            # the guarded jobs after a release: not blocked


def test_release_context_without_the_guard_is_not_a_release(tmp_path, checkout, monkeypatch, capsys):
    store = build_store(tmp_path, checkout / "nhl.sqlite", "nhl")
    root = git_repo(tmp_path / "release")
    monkeypatch.setattr(release, "code_root", lambda: root)
    monkeypatch.setenv(release.ROOT_ENV, str(root))
    monkeypatch.setenv("NBA_DATA_DIR", str(checkout))
    no_run(monkeypatch)
    rc, out = run(["--sport", "nhl", "score"], capsys)
    assert (rc, out.get("status")) == (1, "refused") and "NBA_RELEASE_GUARD" in out["reason"]
    assert schema(store) == release.shipped_schema()


def test_copies_still_migrate_implicitly(tmp_path, monkeypatch, capsys):
    """scripts/dev.sh's copy (and any scratch folder) keeps today's behaviour: commands upgrade it as before."""
    copy = tmp_path / "copy"
    copy.mkdir()
    monkeypatch.setenv("NBA_DATA_DIR", str(copy))
    store = build_store(tmp_path, copy / "nhl.sqlite", "nhl", upto=release.shipped_schema() - 1)
    no_run(monkeypatch)
    assert cli.main(["--sport", "nhl", "score"]) == 0
    assert schema(store) == release.shipped_schema()
    assert cli.main(["soccer", "migrate"]) == 0 and schema(copy / "soccer.sqlite") == release.shipped_schema()


def test_db_migrate_refuses_protected_pending_without_context(tmp_path, checkout):
    store = build_store(tmp_path, checkout / "nhl.sqlite", "nhl", upto=release.shipped_schema() - 1)
    for explicit in (False, True):                               # explicit, but not in the release environment
        conn = db.connect(store)
        try:
            raised = None
            try:
                db.migrate(conn, explicit=explicit) if explicit else db.migrate(conn)
            except Exception as exc:
                raised = exc
        finally:
            conn.close()
        assert schema(store) == release.shipped_schema() - 1
        assert type(raised).__name__ == "ProtectedStoreError", raised
    conn = db.connect(store)                                     # nothing pending through `upto`: a no-op, not refused
    try:
        assert db.migrate(conn, upto=release.shipped_schema() - 1) == []
    finally:
        conn.close()


def test_in_memory_and_temporary_databases_are_never_protected(tmp_path, checkout, monkeypatch):
    """PRAGMA database_list reports '' for them; '' must not resolve to the working directory (a checkout's backend/)."""
    backend = checkout.parent / "backend"
    backend.mkdir()
    monkeypatch.chdir(backend)                                   # Path('').resolve() would be here, beside .git/
    for target in (":memory:", ""):
        conn = sqlite3.connect(target, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            assert db.migrate(conn)[-1].startswith(f"{release.shipped_schema():04d}_")
        finally:
            conn.close()
    conn = db.connect(tmp_path / "scratch.sqlite")
    try:
        assert db.migrate(conn)
    finally:
        conn.close()


# --- C4/I9: replay preflight --------------------------------------------------------------------------------------------
def _rebuild_target(data, src, target, upto):
    """`target`: an empty store (migrated through `upto`, unclaimed) holding `src`'s cached-response log, as when a store
    is rebuilt from its cache."""
    conn = db.connect(target)
    db.migrate(conn, upto=upto)
    for table in ("raw_responses", "raw_fetches"):
        have = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        for r in src.execute(f"SELECT * FROM {table}"):
            cols = [c for c in r.keys() if c in have]
            conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         tuple(r[c] for c in cols))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    later = src.execute("SELECT r.raw_id, r.path FROM raw_responses r JOIN raw_fetches f ON f.raw_id = r.raw_id "
                        "ORDER BY f.retrieved_at DESC LIMIT 1").fetchone()
    (data / "raw" / later["path"]).unlink()                       # the LATER response's bytes are gone; the earlier is present
    return later["raw_id"]


UPTO = {"schema31": 31, "schema33": 33, "shipped-1": release.shipped_schema() - 1, "shipped": release.shipped_schema()}


@pytest.mark.parametrize("at", sorted(UPTO))
@pytest.mark.parametrize("sport", ["nba", "nfl", "soccer"])
def test_replay_refuses_before_writing_when_a_later_raw_file_is_missing(tmp_path, monkeypatch, capsys, sport, at):
    """Older schemas (the live NBA/NHL 31, NFL 33, shipped-1): the CLI must not migrate before refusing; shipped: replay
    must not import the earlier, present response and then stop half-way at the missing one."""
    data = tmp_path / "data"
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    src = db.connect(data / "source.sqlite")
    db.migrate(src)
    if sport == "nba":
        ingest.ingest_month(src, "202401", fetch=lambda _m: payload(event(eid="1001")), retrieved_at="2024-01-20T12:00:00Z")
        ingest.ingest_month(src, "202402", fetch=lambda _m: payload(event(eid="1002", date="2024-02-15T18:00Z")),
                            retrieved_at="2024-02-20T12:00:00Z")
        argv = ["replay"]
    elif sport == "nfl":
        ingest.ingest_nfl_month(src, "202409", fetch=lambda _m: nfl_payload(nfl_event(eid="401671000", date="2024-09-29T17:00Z",
                                                                                      week=4)),
                                retrieved_at="2024-09-30T12:00:00Z")
        ingest.ingest_nfl_month(src, "202410", fetch=lambda _m: nfl_payload(nfl_event()), retrieved_at="2024-10-07T12:00:00Z")
        argv = ["nfl", "replay"]
    else:
        soccer.ingest_day(src, "eng.1", "20261107", fetch=lambda *_: soccer_payload("eng.1", soccer_event()),
                          retrieved_at="2026-11-07T20:00:00Z")
        soccer.ingest_day(src, "eng.1", "20261108", fetch=lambda *_: soccer_payload("eng.1", soccer_event(
            eid="401878759", date="2026-11-08T15:00Z")), retrieved_at="2026-11-08T20:00:00Z")
        argv = ["soccer", "replay"]
    store_file = config.db_path(sport)
    missing = _rebuild_target(data, src, store_file, upto=UPTO[at])
    src.close()
    before = dump(store_file)
    rc, out = run(argv, capsys)
    assert dump(store_file) == before                             # schema, games/matches, revisions, fetches, identity
    assert (rc, out.get("status")) == (1, "refused"), (rc, out)
    assert out["missing_raw"]["count"] == 1 and out["missing_raw"]["raw_ids"] == [missing]
    if sport != "soccer":                                         # where the complete list comes from
        assert "release-check" in out["reason"]


def test_replay_with_every_file_present_still_runs(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    src = db.connect(data / "source.sqlite")
    db.migrate(src)
    ingest.ingest_nfl_month(src, "202410", fetch=lambda _m: nfl_payload(nfl_event()), retrieved_at="2024-10-07T12:00:00Z")
    ingest.ingest_nfl_month(src, "202411", fetch=lambda _m: nfl_payload(nfl_event(eid="401671009", date="2024-11-03T18:00Z",
                                                                                  week=9)),
                            retrieved_at="2024-11-04T12:00:00Z")
    target = db.connect(config.db_path("nfl"))
    db.migrate(target)
    for table in ("raw_responses", "raw_fetches"):
        for r in src.execute(f"SELECT * FROM {table}"):
            target.execute(f"INSERT INTO {table} ({', '.join(r.keys())}) VALUES ({', '.join('?' * len(r))})", tuple(r))
    target.close()
    src.close()
    rc, out = run(["nfl", "replay"], capsys)
    assert rc == 0 and out["games_inserted"] == 2


def test_release_check_lists_missing_raw_files_but_release_check_function_does_not_scan(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    conn = db.connect(sport="nba")
    db.migrate(conn)
    db.claim_store(conn, "nba")
    ingest.ingest_month(conn, "202401", fetch=lambda _m: payload(event(eid="1001")), retrieved_at="2024-01-20T12:00:00Z")
    ingest.ingest_month(conn, "202402", fetch=lambda _m: payload(event(eid="1002", date="2024-02-15T18:00Z")),
                        retrieved_at="2024-02-20T12:00:00Z")
    gone = conn.execute("SELECT raw_id, path FROM raw_responses ORDER BY raw_id DESC LIMIT 1").fetchone()
    conn.close()
    (data / "raw" / gone["path"]).unlink()
    assert "raw_missing" not in release.check("nba")              # every guarded tick: no file scan
    from app import research_live, selector
    monkeypatch.setattr(research_live, "definition", lambda _c: "synthetic")   # needs sealed research files
    monkeypatch.setattr(selector, "version", lambda: "synthetic")
    cli.main(["release-check"])
    out = json.loads(capsys.readouterr().out)
    assert out["raw_missing"]["count"] == 1 and out["raw_missing"]["raw_ids"] == [gone["raw_id"]]


def test_nfl_release_check_lists_missing_raw_files(tmp_path, monkeypatch, capsys):
    """NFL is the store with byte-less responses (audit C4): its release check reports them, read-only, without failing."""
    data = tmp_path / "data"
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    conn = db.connect(sport="nfl")
    db.migrate(conn)
    ingest.ingest_nfl_month(conn, "202410", fetch=lambda _m: nfl_payload(nfl_event()), retrieved_at="2024-10-07T12:00:00Z")
    gone = conn.execute("SELECT raw_id, path FROM raw_responses").fetchone()
    conn.close()
    (data / "raw" / gone["path"]).unlink()
    before = dump(config.db_path("nfl"))
    cli.main(["nfl", "release-check"])
    out = json.loads(capsys.readouterr().out)
    assert out["raw_missing"] == {"count": 1, "raw_ids": [gone["raw_id"]], "truncated": False}
    assert "raw_missing" not in release.check("nfl") and dump(config.db_path("nfl")) == before


# --- Review round 2 (2026-11-07) ---------------------------------------------------------------------------------------
def _files(tmp_path, *names):
    for name in names:
        (tmp_path / name).write_text("{}")
    return [str(tmp_path / n) for n in names]


def test_nfl_activate_on_a_protected_folder_is_refused(tmp_path, checkout, monkeypatch, capsys):
    """A dev-checkout activation would replace the live activation file (another definition hash: NFL goes inactive)."""
    from app import nfl_live
    build_store(tmp_path, checkout / "nfl.sqlite", "nfl")
    before = tree(checkout)
    monkeypatch.setattr(nfl_live, "activate", lambda *a, **k: pytest.fail("activation ran on a protected folder"))
    sealed, report, log = _files(tmp_path, "sealed.json", "report.json", "access.jsonl")
    rc, out = run(["nfl", "activate", "--sealed", sealed, "--report", report, "--access-log", log], capsys)
    assert (rc, out.get("status")) == (1, "refused"), (rc, out)
    assert tree(checkout) == before


def test_nfl_test_run_and_reserved_line_collection_on_a_protected_folder_are_refused(tmp_path, checkout, monkeypatch,
                                                                                       capsys):
    """Both append to the live one-shot access log (data/research/nfl_eval/nfl_test_access.jsonl)."""
    from app import nfl_eval
    build_store(tmp_path, checkout / "nfl.sqlite", "nfl")
    before = tree(checkout)
    monkeypatch.setattr(nfl_eval, "run_test", lambda *a, **k: pytest.fail("test run on a protected folder"))
    monkeypatch.setattr(nfl_eval, "line_events", lambda *a, **k: pytest.fail("reserved lines on a protected folder"))
    (sealed,) = _files(tmp_path, "sealed.json")
    for argv in (["nfl", "test-run", "--sealed", sealed, "--out", str(tmp_path / "out")],
                 ["nfl", "collect-lines", "--test", "--sealed", sealed]):
        rc, out = run(argv, capsys)
        assert (rc, out.get("status")) == (1, "refused"), (argv, rc, out)
    assert tree(checkout) == before


@pytest.mark.parametrize("link", ["store", "raw"])
def test_a_symlink_into_a_protected_folder_is_refused(tmp_path, checkout, monkeypatch, capsys, link):
    """NBA_DATA_DIR names an unprotected folder whose store file (or raw/ folder) is a link into the live folder."""
    build_store(tmp_path, checkout / "nhl.sqlite", "nhl")
    (checkout / "raw").mkdir()
    copy = tmp_path / "copy"
    copy.mkdir()
    if link == "store":
        (copy / "nhl.sqlite").symlink_to(checkout / "nhl.sqlite")
    else:
        build_store(tmp_path, copy / "nhl.sqlite", "nhl")
        (copy / "raw").symlink_to(checkout / "raw")
    monkeypatch.setenv("NBA_DATA_DIR", str(copy))
    before = tree(checkout)
    no_run(monkeypatch)
    rc, out = run(["--sport", "nhl", "score"], capsys)
    assert (rc, out.get("status")) == (1, "refused"), (rc, out)
    assert tree(checkout) == before


def test_db_migrate_backstop_follows_a_store_symlink(tmp_path, checkout, monkeypatch):
    store = build_store(tmp_path, checkout / "nhl.sqlite", "nhl", upto=release.shipped_schema() - 1)
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / "nhl.sqlite").symlink_to(store)
    conn = db.connect(copy / "nhl.sqlite")
    try:
        with pytest.raises(db.ProtectedStoreError):
            db.migrate(conn)
    finally:
        conn.close()
    assert schema(store) == release.shipped_schema() - 1


def _other_spelling(path, how):
    """The same existing folder under another path spelling (case-swapped last component on a case-insensitive file
    system, or macOS's /System/Volumes/Data firmlink), or None when this file system has no such spelling."""
    other = (path.parent / path.name.upper() if how == "case" else
             Path("/System/Volumes/Data" + str(path.resolve())))
    try:
        return other if str(other) != str(path) and os.path.samefile(other, path) else None
    except OSError:
        return None


@pytest.mark.parametrize("spelling", ["exact", "data-case", "data-firmlink", "root-case"])
def test_the_checkout_holding_the_data_folder_is_never_a_release_environment(tmp_path, monkeypatch, capsys, spelling):
    """Setting NBA_RELEASE_GUARD/ROOT in the development main checkout itself (clean, data/ ignored) does not make it a
    release: the release is a separate checkout. Decided by file identity, so another spelling of the same data folder
    or code root (case variant, firmlink) is caught too."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repo(repo)
    (repo / ".gitignore").write_text("data/\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "ignore"], check=True)
    (repo / "data").mkdir()
    store = build_store(tmp_path, repo / "data" / "nhl.sqlite", "nhl")
    root, data = repo, repo / "data"
    if spelling != "exact":
        other = _other_spelling(repo, spelling.split("-")[1])
        if other is None:
            pytest.skip(f"no {spelling} spelling of {repo} on this file system")
        root, data = (other, data) if spelling == "root-case" else (repo, other / "data")
    monkeypatch.setattr(config, "REPO_ROOT", root)
    monkeypatch.setattr(release, "code_root", lambda: root)
    for key, value in ((release.GUARD_ENV, "1"), (release.ROOT_ENV, str(root)), ("NBA_DATA_DIR", str(data))):
        monkeypatch.setenv(key, value)
    assert release.check("nhl")["problems"] == []                 # release.check itself is unchanged
    assert any("holds this data folder" in p for p in release.migration_context("nhl"))
    before = tree(repo / "data")
    no_run(monkeypatch)
    rc, out = run(["--sport", "nhl", "score"], capsys)
    assert (rc, out.get("status")) == (1, "refused"), (rc, out)
    assert tree(repo / "data") == before and schema(store) == release.shipped_schema()


def test_the_own_data_folder_is_protected_under_another_spelling(tmp_path, monkeypatch):
    """A linked worktree's own data/ folder (no .git directory beside it) is matched by identity, not by path string."""
    linked = tmp_path / "linked"
    (linked / "data").mkdir(parents=True)
    (linked / ".git").write_text("gitdir: elsewhere\n")
    monkeypatch.setattr(config, "REPO_ROOT", linked)
    others = [o / "data" for o in (_other_spelling(linked, "case"), _other_spelling(linked, "firmlink")) if o is not None]
    if not others:
        pytest.skip("no other spelling of the folder on this file system")
    for other in others:
        assert config.protected_data_dir(other) and config.protected_path(other / "nhl.sqlite"), other
    assert not config.protected_data_dir(tmp_path / "copy")


def _nfl_store_with_one_response(data):
    conn = db.connect(data / "nfl.sqlite")
    db.migrate(conn)
    ingest.ingest_nfl_month(conn, "202410", fetch=lambda _m: nfl_payload(nfl_event()), retrieved_at="2024-10-07T12:00:00Z")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return conn


def test_replay_preflight_fails_closed_when_it_cannot_read_the_store(tmp_path, monkeypatch, capsys):
    """Only an absent raw_responses table means "nothing to check"; any other read error is a refusal, never a pass."""
    data = tmp_path / "data"
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    conn = _nfl_store_with_one_response(data)
    conn.close()
    plain = sqlite3.connect(data / "nfl.sqlite", isolation_level=None)
    plain.execute("PRAGMA foreign_keys = OFF")
    plain.execute("DROP TABLE raw_fetches")                       # the log replay reads is unreadable
    plain.close()
    before = dump(data / "nfl.sqlite")
    rc, out = run(["nfl", "replay"], capsys)
    assert dump(data / "nfl.sqlite") == before
    assert (rc, out.get("status")) == (1, "refused") and "could not read" in out["reason"], (rc, out)
    (data / "nfl.sqlite").write_bytes(b"not a database" * 100)    # unreadable file
    rc, out = run(["nfl", "replay"], capsys)
    assert (rc, out.get("status")) == (1, "refused") and "could not read" in out["reason"], (rc, out)


def test_nfl_release_check_without_a_cache_log_reports_it_instead_of_crashing(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    (data / "nfl.sqlite").write_bytes(b"")                       # a 0-byte store
    try:
        rc = cli.main(["nfl", "release-check"])
    except Exception as exc:
        rc = f"{type(exc).__name__}: {exc}"
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["raw_missing"] is None and out["raw_missing_reason"], (rc, out)
    assert (data / "nfl.sqlite").read_bytes() == b""


def test_an_unlogged_nhl_response_replay_skips_is_not_a_missing_input(tmp_path, monkeypatch, capsys):
    """False-positive guard: replay never reads a cached NHL response without a logged retrieval."""
    from test_replay_same_store import _nhl
    data = tmp_path / "data"
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    conn = db.connect(config.db_path("nhl"))
    db.migrate(conn)
    ingest.ingest_nhl_season(conn, 2027, fetch=lambda _s: _nhl(4, 1, 3), retrieved_at="2026-10-13T03:00:00Z")
    r = conn.execute("SELECT * FROM raw_responses").fetchone()
    conn.execute("INSERT INTO raw_responses (source, endpoint, request_key, content_hash, byte_size, path, importer_version, "
                 "first_retrieved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (r["source"], r["endpoint"], r["request_key"] + "&unlogged", "0" * 64, 1, "nhl_stats/00/missing.json.gz",
                  r["importer_version"], "2026-10-13T04:00:00Z"))
    conn.close()
    rc, out = run(["--sport", "nhl", "replay"], capsys)
    assert rc == 0 and out.get("status") != "refused", (rc, out)


@pytest.mark.parametrize("source", ["sportsdataverse", "espn_summary"])
def test_a_missing_team_box_file_refuses_replay(tmp_path, monkeypatch, capsys, source):
    """Possession files (rebuild_team_box) are read by replay too: a missing one refuses before the scoreboards import."""
    data = tmp_path / "data"
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    src = db.connect(data / "source.sqlite")
    db.migrate(src)
    ingest.ingest_month(src, "202401", fetch=lambda _m: payload(event(eid="1001")), retrieved_at="2024-01-20T12:00:00Z")
    target = config.db_path("nba")
    conn = db.connect(target)
    db.migrate(conn)
    for table in ("raw_responses", "raw_fetches"):
        for r in src.execute(f"SELECT * FROM {table}"):
            conn.execute(f"INSERT INTO {table} ({', '.join(r.keys())}) VALUES ({', '.join('?' * len(r))})", tuple(r))
    src.close()
    raw_id = conn.execute("INSERT INTO raw_responses (source, endpoint, request_key, content_hash, byte_size, path, "
                          "importer_version, first_retrieved_at) VALUES (?, 'box', 'box?1', ?, 1, ?, 'test', ?)",
                          (source, "1" * 64, f"{source}/11/{'1' * 64}.json.gz", "2024-01-21T12:00:00Z")).lastrowid
    conn.execute("INSERT INTO raw_fetches (raw_id, retrieved_at) VALUES (?, ?)", (raw_id, "2024-01-21T12:00:00Z"))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    before = dump(target)
    rc, out = run(["replay"], capsys)
    assert dump(target) == before
    assert (rc, out.get("status")) == (1, "refused") and out["missing_raw"]["raw_ids"] == [raw_id], (rc, out)


def test_a_hard_link_to_a_protected_store_is_refused(tmp_path, checkout, monkeypatch, capsys):
    """A store file hard-linked to the live one (same inode, no path into the live folder) is refused, fail-closed: a
    store file with more than one link may be the live store under another name."""
    build_store(tmp_path, checkout / "nhl.sqlite", "nhl")
    copy = tmp_path / "copy"
    copy.mkdir()
    os.link(checkout / "nhl.sqlite", copy / "nhl.sqlite")
    monkeypatch.setenv("NBA_DATA_DIR", str(copy))
    before = tree(checkout)
    no_run(monkeypatch)
    rc, out = run(["--sport", "nhl", "score"], capsys)
    assert (rc, out.get("status")) == (1, "refused") and "hard link" in out["reason"], (rc, out)
    assert tree(checkout) == before


@pytest.mark.parametrize("action", ["tick", "refresh"])
def test_a_research_folder_linked_into_a_protected_folder_is_refused(tmp_path, checkout, monkeypatch, capsys, action):
    """A copy with its own store but research/ linked into the live folder: the scheduled NFL commands may append to the
    live one-shot access log (nfl_live.record -> nfl_eval.load_rows) or read the live activation, so they are refused."""
    from app import jobs, nfl_eval
    build_store(tmp_path, checkout / "nfl.sqlite", "nfl")
    (checkout / "research" / "nfl_eval").mkdir(parents=True)
    (checkout / "research" / "nfl_eval" / "nfl_test_access.jsonl").write_text('{"live": "entry"}\n')
    copy = tmp_path / "copy"
    copy.mkdir()
    build_store(tmp_path, copy / "nfl.sqlite", "nfl")
    (copy / "research").symlink_to(checkout / "research")
    monkeypatch.setenv("NBA_DATA_DIR", str(copy))

    def appends(conn, **kw):              # the access-log write NFL live makes during a tick (nfl_live.record)
        nfl_eval.access_log_path().parent.mkdir(parents=True, exist_ok=True)
        with open(nfl_eval.access_log_path(), "a") as f:
            f.write('{"from": "a copy"}\n')
        return {"status": "ok"}
    monkeypatch.setattr(jobs, "tick", appends)
    monkeypatch.setattr(jobs, "refresh", appends)
    before = tree(checkout)
    rc, out = run(["nfl", action], capsys)
    assert (rc, out.get("status")) == (1, "refused"), (rc, out)
    assert tree(checkout) == before
    (copy / "research").unlink()                                  # the same copy without the link still runs
    (copy / "research").mkdir()
    rc, _ = run(["nfl", action], capsys)
    assert rc == 0 and tree(checkout) == before
    assert (copy / "research" / "nfl_eval" / "nfl_test_access.jsonl").read_text() == '{"from": "a copy"}\n'


@pytest.mark.parametrize("argv", [["replay"], ["nfl", "replay"], ["soccer", "replay"]], ids=lambda a: a[0])
def test_replay_on_an_empty_store_file_is_refused(tmp_path, monkeypatch, capsys, argv):
    """A 0-byte store file has no cached-response log to replay from: refused before any lock, migration or side file (a
    store file that does not exist at all is still created and migrated as before)."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    store = config.db_path({"replay": "nba"}.get(argv[0], argv[0]))
    store.write_bytes(b"")
    rc, out = run(argv, capsys)
    assert (rc, out.get("status")) == (1, "refused") and "empty" in out["reason"], (rc, out)
    assert store.read_bytes() == b"" and sorted(p.name for p in data.iterdir()) == [store.name]
