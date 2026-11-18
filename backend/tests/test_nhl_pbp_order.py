"""jobs.nhl_pbp works the newest finals first, so old games whose play-by-play keeps failing cannot crowd the current
season's recent finals out of the bounded per-run slice (I1: candidate recorder refused on shots staleness)."""
from app import jobs, nhl_shots
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)
from test_nhl_xg import fake_fetch


def newest_pending(conn, n):
    seasons = [r[0] for r in conn.execute("SELECT DISTINCT season FROM games WHERE status='final' ORDER BY season DESC LIMIT 2")]
    p = sorted(nhl_shots.pending(conn, seasons), key=lambda g: (g["tipoff_at"], g["game_id"]), reverse=True)
    ids = [g["game_id"] for g in p]
    assert len(ids) > 3 * n
    return ids[:n], ids[n:]            # the newest n, and every older pending game


def test_a_failing_old_backlog_does_not_starve_the_newest_finals(nhl_store):
    newest, oldest = newest_pending(nhl_store, 5)
    ok = fake_fetch(nhl_store)

    def fetch(game_id):
        if game_id in oldest:
            raise RuntimeError("HTTP 404 (old game)")
        return ok(game_id)
    out = jobs.nhl_pbp(nhl_store, fetch=fetch, limit=5)
    imported = [r[0] for r in nhl_store.execute("SELECT game_id FROM nhl_pbp_imports")]
    assert sorted(imported) == sorted(newest), (imported, newest)
    assert out["status"] == "success" and out["imported"] == 5 and not out["errors"]


def test_pending_and_error_reporting_are_unchanged(nhl_store):
    def fetch(game_id):
        raise RuntimeError("down")
    out = jobs.nhl_pbp(nhl_store, fetch=fetch, limit=3)
    assert out["status"] == "failed" and out["pending"] == 3 and out["imported"] == 0 and len(out["errors"]) == 3
    assert set(out) >= {"status", "pending", "imported", "incomplete", "errors", "seasons"}
