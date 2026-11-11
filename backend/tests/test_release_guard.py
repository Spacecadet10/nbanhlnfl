"""Pinned release guard (app/release.py): scheduled commands run only from a clean release checkout that uses the shared
data folder at the schema the release ships; a refusal happens before any request, lock or migration and is a failed job
run; every guarded job run records its release commit. Temporary git repositories and stores only."""
import json
import subprocess

import pytest

from app import cli, db, jobs, release


def git_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README").write_text("release\n")
    subprocess.run(["git", "-C", str(path), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "r"], check=True)
    release.write_pin(path)      # a release pins its commit (app.release pin), as the release procedure does
    return path


@pytest.fixture
def store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    conn = db.connect(sport="nhl")
    db.migrate(conn)
    db.claim_store(conn, "nhl")
    conn.close()
    return data


def test_a_clean_release_checkout_with_the_shared_store_passes(tmp_path, store, monkeypatch):
    root = git_repo(tmp_path / "release")
    monkeypatch.setenv(release.ROOT_ENV, str(root))
    info = release.check("nhl", root=root)
    assert info["problems"] == [] and len(info["commit"]) == 40
    assert info["store_schema"] == info["release_schema"] == release.shipped_schema()


def test_every_unsafe_state_is_refused_with_its_reason(tmp_path, store, monkeypatch):
    root = git_repo(tmp_path / "release")
    monkeypatch.setenv(release.ROOT_ENV, str(tmp_path / "elsewhere"))
    (root / "README").write_text("edited\n")
    monkeypatch.delenv("NBA_DATA_DIR")
    problems = " | ".join(release.check("nhl", root=root)["problems"])
    assert "not the release checkout" in problems and "uncommitted changes: README" in problems
    assert "NBA_DATA_DIR is not set" in problems


def test_a_store_at_another_schema_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path / "data"))
    conn = db.connect(sport="nhl")
    db.migrate(conn, upto=release.shipped_schema() - 1)
    conn.close()
    root = git_repo(tmp_path / "release")
    monkeypatch.setenv(release.ROOT_ENV, str(root))
    problems = release.check("nhl", root=root)["problems"]
    assert any("differs from this release's schema" in p for p in problems)


def test_the_cli_refuses_before_any_request_lock_or_migration_and_records_it(tmp_path, store, monkeypatch, capsys):
    monkeypatch.setenv(release.GUARD_ENV, "1")
    monkeypatch.setenv(release.ROOT_ENV, str(tmp_path / "not-this-checkout"))
    def no_network(*a, **k):
        raise AssertionError("a refused command must not make a request")
    monkeypatch.setattr(jobs, "odds_snapshot", no_network)
    monkeypatch.setattr(db, "store_lock", no_network)
    monkeypatch.setattr(db, "migrate", no_network)
    assert cli.main(["--sport", "nhl", "odds"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "failed" and any("not the release checkout" in p for p in out["problems"])
    conn = db.connect(sport="nhl")
    row = conn.execute("SELECT job, status, detail FROM job_runs WHERE run_id=?", (out["run_id"],)).fetchone()
    assert (row["job"], row["status"]) == ("odds", "failed")
    assert json.loads(row["detail"])["reason"] == "release guard refused the scheduled command"


def test_commands_outside_the_guard_and_dev_runs_are_unaffected(tmp_path, store, monkeypatch, capsys):
    monkeypatch.delenv(release.GUARD_ENV, raising=False)              # the development checkout: no guard
    assert not release.enabled()
    monkeypatch.setenv(release.GUARD_ENV, "1")
    assert "coverage" not in release.GUARDED and "odds" in release.GUARDED and "daily" in release.GUARDED


def test_job_runs_record_the_release_commit(store, monkeypatch):
    conn = db.connect(sport="nhl")
    monkeypatch.delenv(release.COMMIT_ENV, raising=False)
    plain = jobs._finish(conn, jobs._start(conn, "odds"), "skipped", 0, {"reason": "x"})
    assert "release" not in plain
    monkeypatch.setenv(release.COMMIT_ENV, "abc123")
    stamped = jobs._finish(conn, jobs._start(conn, "odds"), "skipped", 0, {"reason": "x"})
    assert stamped["release"] == "abc123"
    assert json.loads(conn.execute("SELECT detail FROM job_runs WHERE run_id=?", (stamped["run_id"],)).fetchone()[0])["release"] == "abc123"


def test_a_checkout_not_at_its_pinned_commit_is_refused(tmp_path, store, monkeypatch):
    # Owner, Nov 10: the guard never compared the checkout with a pinned commit, so a moved checkout still ran.
    root = git_repo(tmp_path / "release")
    monkeypatch.setenv(release.ROOT_ENV, str(root))
    pinned = release.pinned_commit(root)
    (root / "NEXT").write_text("later\n")
    subprocess.run(["git", "-C", str(root), "add", "NEXT"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "moved"], check=True)
    problems = release.check("nhl", root=root)["problems"]
    assert problems == [f"the release checkout is at {release._git(root, 'rev-parse', 'HEAD')[:12]}, not the pinned commit {pinned[:12]}"]
    monkeypatch.setenv(release.COMMIT_ENV, release._git(root, "rev-parse", "HEAD").strip())   # only records; never pins
    assert release.check("nhl", root=root)["problems"] != []
    monkeypatch.delenv(release.COMMIT_ENV)
    release.write_pin(root)
    assert release.check("nhl", root=root)["problems"] == [] and not release._git(root, "status", "--porcelain").strip()


def test_a_checkout_without_any_pin_is_refused(tmp_path, store, monkeypatch):
    root = git_repo(tmp_path / "release")
    release.pin_path(root).unlink()
    monkeypatch.setenv(release.ROOT_ENV, str(root))
    assert any(p.startswith("no pinned release commit") for p in release.check("nhl", root=root)["problems"])
