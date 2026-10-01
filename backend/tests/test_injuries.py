"""Injury-report collector pilot (docs/INJURY_COLLECTOR_PLAN.md): raw files, attempt log, budgets (no network)."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import coverage, db, ingest, injuries, jobs, raw_cache
from app.sources import nba_injury
from app.sources.nba_injury import Response
from test_live import event, ingest_events

UTC = timezone.utc
TIPOFF = "2026-10-20T23:00:00Z"            # game window: 2026-10-19T11:00Z (36 h before) to 2026-10-21T05:00Z (6 h after)
EARLY = datetime(2026, 10, 19, 12, 1, tzinfo=UTC)   # three slots due: 11:00Z, 11:30Z, 12:00Z
NAMES = ["Injury-Report_2026-10-19_07_00AM.pdf", "Injury-Report_2026-10-19_07_30AM.pdf",
         "Injury-Report_2026-10-19_08_00AM.pdf"]


def pdf(tag="x") -> bytes:
    return b"%PDF-1.7 " + tag.encode()


def ok(body: bytes, etag='"e1"', modified="Mon, 19 Oct 2026 11:00:05 GMT") -> Response:
    return Response(200, modified, etag, len(body), body)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(tmp_path / "inj.sqlite")
    db.migrate(c)
    ingest_events(c, [event("9001", TIPOFF, "1", "2")], "2026-10-18T12:00:00Z")
    yield c
    c.close()


def attempts(c, cols="file_name, purpose, outcome"):
    return [tuple(r) for r in c.execute(f"SELECT {cols} FROM injury_report_attempts ORDER BY attempt_id")]


def collect(c, fetch, now=EARLY, **kw):
    return injuries.run(c, now=now, fetch=fetch, sleep=kw.pop("sleep", lambda _s: None), **kw)


# ---- 1. slots and file names ---------------------------------------------------------------------------------------

def test_file_names_use_eastern_wall_time():
    at = lambda *a: datetime(*a, tzinfo=UTC)  # noqa: E731
    assert nba_injury.file_name(at(2026, 10, 20, 21, 30)) == "Injury-Report_2026-10-20_05_30PM.pdf"   # EDT
    assert nba_injury.file_name(at(2026, 12, 1, 22, 0)) == "Injury-Report_2026-12-01_05_00PM.pdf"     # EST
    assert nba_injury.file_name(at(2026, 10, 21, 4, 0)) == "Injury-Report_2026-10-21_12_00AM.pdf"     # midnight
    assert nba_injury.file_name(at(2026, 10, 20, 16, 0)) == "Injury-Report_2026-10-20_12_00PM.pdf"    # noon
    assert nba_injury.url("a.pdf") == "https://ak-static.cms.nba.com/referee/injury/a.pdf" and "?" not in nba_injury.url("a")


def test_slots_across_both_daylight_saving_changes():
    day = lambda slots, d: [s.name for s in slots if f"_{d}_" in s.name]  # noqa: E731
    back = injuries.slots_between(datetime(2026, 11, 1, 3, 59, tzinfo=UTC), datetime(2026, 11, 2, 4, 59, tzinfo=UTC))
    names = day(back, "2026-11-01")
    assert len(names) == 48 == len(set(names)) and len(back) == 48      # 50 half-hours, the repeated hour kept once
    assert [s.at for s in back if "_01_00AM" in s.name] == ["2026-11-01T05:00:00Z"]   # the first of the two 1:00 AMs
    forward = injuries.slots_between(datetime(2027, 3, 14, 4, 59, tzinfo=UTC), datetime(2027, 3, 15, 3, 59, tzinfo=UTC))
    names = day(forward, "2027-03-14")
    assert len(names) == 46 and not any("_02_00AM" in n or "_02_30AM" in n for n in names)


def test_only_slots_inside_a_game_window_are_due(conn):
    due = injuries.due_slots(conn, datetime(2026, 10, 19, 0, 0, tzinfo=UTC), datetime(2026, 10, 22, 0, 0, tzinfo=UTC))
    assert (due[0].at, due[-1].at) == ("2026-10-19T11:00:00Z", "2026-10-21T04:30:00Z")
    calls = []
    out = collect(conn, lambda n: calls.append(n) or ok(pdf()), now=datetime(2026, 10, 10, 12, 0, tzinfo=UTC))
    assert out["status"] == "skipped" and out["due"] == 0 and calls == [] and attempts(conn) == []


# ---- 2. timestamps -------------------------------------------------------------------------------------------------

def test_retrieval_time_is_taken_after_the_response_arrives(conn):
    # The 11:00Z report: request starts 11:29:58Z, response arrives 11:30:07Z (after the half-hour boundary).
    ticks = iter(["2026-10-19T11:29:58Z", "2026-10-19T11:30:07Z"])
    body = pdf("slow")
    injuries.run(conn, now=datetime(2026, 10, 19, 11, 29, 58, tzinfo=UTC), fetch=lambda n: ok(body),
                 sleep=lambda _s: None, clock=lambda: next(ticks))
    row = conn.execute("SELECT a.*, f.retrieved_at, r.first_retrieved_at, r.path, r.byte_size AS raw_bytes "
                       "FROM injury_report_attempts a JOIN raw_fetches f USING (fetch_id) "
                       "JOIN raw_responses r USING (raw_id)").fetchone()
    assert (row["started_at"], row["completed_at"]) == ("2026-10-19T11:29:58Z", "2026-10-19T11:30:07Z")
    assert row["retrieved_at"] == row["first_retrieved_at"] == "2026-10-19T11:30:07Z"
    assert row["late"] == 1    # 30 min 7 s after the slot: late by arrival time, not by request time
    assert row["path"].endswith(".pdf.gz") and row["raw_bytes"] == len(body) and row["byte_size"] == len(body)
    assert (row["http_status"], row["etag"], row["last_modified"], row["content_length"]) == \
        (200, '"e1"', "Mon, 19 Oct 2026 11:00:05 GMT", len(body))
    raw_id = conn.execute("SELECT raw_id FROM raw_responses WHERE source = 'nba_injury_report'").fetchone()[0]
    assert raw_cache.load(conn, raw_id) == body    # stored unchanged


# ---- 3. budgets ----------------------------------------------------------------------------------------------------

def down(calls):
    def fetch(name):
        calls.append(name)
        raise ConnectionError("down")
    return fetch


def test_outage_never_exceeds_the_run_slot_or_day_budgets(conn, monkeypatch):
    now = datetime(2026, 10, 20, 21, 31, tzinfo=UTC)     # 48 slots due
    calls = []
    first = collect(conn, down(calls), now=now)
    assert first["due"] == 48 and len(calls) == 8 == first["attempts"] and first["status"] == "failed"
    assert len(first["failed"]) == 4 and first["deferred"] == 44      # 2 attempts on each of the 4 oldest slots
    second = collect(conn, down(calls), now=now)
    assert len(calls) == 16 and len(second["given_up"]) == 4          # those 4 slots have used their 4 attempts
    third = collect(conn, down(calls), now=now)
    assert len(calls) == 24 and set(third["failed"]).isdisjoint(second["failed"])   # moves on, never retries given-up slots
    assert max(r[0] for r in conn.execute("SELECT COUNT(*) FROM injury_report_attempts GROUP BY file_name")) == 4
    monkeypatch.setattr(injuries, "MAX_ATTEMPTS_PER_DAY", 30)
    fourth = collect(conn, down(calls), now=now)
    assert fourth["attempts"] == 6 and len(calls) == 30               # the rolling 24-hour cap stops the run mid-way
    assert collect(conn, down(calls), now=now)["attempts"] == 0 and len(calls) == 30


def test_run_budget_exhausted_mid_retry(conn, monkeypatch):
    monkeypatch.setattr(injuries, "MAX_ATTEMPTS_PER_RUN", 3)
    calls = []
    out = collect(conn, down(calls))
    assert calls == [NAMES[0], NAMES[0], NAMES[1]] and out["attempts"] == 3 and out["deferred"] == 1


def test_requests_are_spaced_by_the_crawl_delay_or_backoff(conn):
    sleeps, calls = [], []

    def flaky(name):
        calls.append(name)
        if len(calls) == 1:
            raise ConnectionError("temporary")
        return ok(pdf(name))
    out = collect(conn, flaky, sleep=sleeps.append)
    assert out["attempts"] == 4 and len(out["stored"]) == 3
    assert sleeps == [2, 1.0, 1.0] and all(s >= nba_injury.CRAWL_DELAY_SECONDS for s in sleeps)


# ---- 4. not available, then available; giving up ----------------------------------------------------------------------

def test_not_available_is_retried_on_later_runs_then_given_up(conn):
    calls = []

    def fetch(name):
        calls.append(name)
        return ok(pdf(name)) if name != NAMES[2] or len(calls) > 9 else Response(403, None, None, None)
    first = collect(conn, fetch)
    assert first["stored"] == NAMES[:2] and first["not_available"] == [NAMES[2]] and first["status"] == "success"
    assert calls.count(NAMES[2]) == 1          # a 403 is not retried within the same run
    for _ in range(3):
        out = collect(conn, lambda n: Response(403, None, None, None))
    assert out["given_up"] == [NAMES[2]] and attempts(conn, "outcome").count(("not_available",)) == 4
    final = collect(conn, fetch)               # given up: no further request even though it would now succeed
    assert final["attempts"] == 0 and final["status"] == "skipped"


# ---- 5. bad bodies -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("resp, error", [
    (Response(200, None, None, 9, b"<html>no</html>"), "body is not a PDF"),
    (Response(200, None, None, 500, pdf("short")), "Content-Length says 500"),
    (Response(500, None, None, None, b""), "HTTP 500"),
])
def test_incomplete_or_wrong_content_is_not_stored(conn, resp, error):
    out = collect(conn, lambda n: resp)
    assert out["status"] == "failed" and out["stored"] == []
    assert all(error in r[0] for r in conn.execute("SELECT error FROM injury_report_attempts"))
    assert conn.execute("SELECT COUNT(*) FROM raw_responses WHERE source = 'nba_injury_report'").fetchone()[0] == 0


# ---- 6. reruns and late catch-up -----------------------------------------------------------------------------------

def test_rerun_stores_nothing_twice_and_late_catch_up_keeps_its_true_time(conn):
    collect(conn, lambda n: ok(pdf(n)))
    again = collect(conn, lambda n: ok(pdf(n)))
    assert again["attempts"] == 0 and again["already_stored"] == 3 and len(attempts(conn)) == 3
    assert [r[0] for r in conn.execute("SELECT late FROM injury_report_attempts")] == [1, 1, 0]   # 61, 31 and 1 min after
    # The machine was asleep: the 12:30Z to 14:00Z reports are fetched at 14:01Z, each stamped 14:01Z.
    later = collect(conn, lambda n: ok(pdf(n)), now=datetime(2026, 10, 19, 14, 1, tzinfo=UTC))
    rows = conn.execute("SELECT slot_at, completed_at, late FROM injury_report_attempts ORDER BY attempt_id").fetchall()[3:]
    assert len(later["stored"]) == 4 and all(r["completed_at"] == "2026-10-19T14:01:00Z" for r in rows)
    assert [(r["slot_at"][11:16], r["late"]) for r in rows] == [("12:30", 1), ("13:00", 1), ("13:30", 1), ("14:00", 0)]


# ---- 7. overwrite check --------------------------------------------------------------------------------------------

NEXT_DAY = datetime(2026, 10, 20, 12, 1, tzinfo=UTC)     # the three slots stored at EARLY are now 24 to 25 hours old


def catch_up(conn, now, fetch):
    """Test setup: stores every pending slot directly, so the next run has its whole budget free for the overwrite check."""
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for slot in injuries.plan(conn, now)["pending"]:
        assert injuries._download(conn, slot, "collect", fetch, lambda: stamp) == "stored"


def test_overwrite_check_runs_once_a_day_and_passes_unchanged_files(conn):
    fetch = lambda n: ok(pdf(n))  # noqa: E731
    collect(conn, fetch)
    catch_up(conn, NEXT_DAY, fetch)
    heads = []

    def head(name):
        heads.append(name)
        return Response(200, "Mon, 19 Oct 2026 11:00:05 GMT", '"e1"', len(pdf(name)))
    out = injuries.run(conn, now=NEXT_DAY, fetch=fetch, head=head, sleep=lambda _s: None)
    assert len(heads) == 2 and set(heads) <= set(NAMES) and out["attempts"] == 2
    assert out["overwrite_check"] == {"checked": 2, "unchanged": 2, "changed": [], "failed": 0, "redownload_failed": []}
    assert [r for r in attempts(conn, "method, purpose, outcome") if r[0] == "HEAD"] == \
        [("HEAD", "overwrite_check", "unchanged")] * 2
    again = injuries.run(conn, now=NEXT_DAY + timedelta(minutes=30), fetch=fetch, head=head, sleep=lambda _s: None)
    assert len(heads) == 2 and again["overwrite_check"]["checked"] == 0      # not repeated within 24 hours
    assert conn.execute("SELECT COUNT(*) FROM raw_responses WHERE source = 'nba_injury_report'").fetchone()[0] == \
        conn.execute("SELECT COUNT(DISTINCT file_name) FROM injury_report_attempts WHERE outcome = 'stored'").fetchone()[0]


def test_overwrite_check_flags_a_changed_file_and_keeps_the_first(conn):
    collect(conn, lambda n: ok(pdf(n)))
    catch_up(conn, NEXT_DAY, lambda n: ok(pdf(n)))
    new_body = pdf("rewritten upstream")
    out = injuries.run(conn, now=NEXT_DAY, sleep=lambda _s: None,
                       fetch=lambda n: ok(new_body, etag='"e2"', modified="Tue, 20 Oct 2026 09:00:00 GMT"),
                       head=lambda n: Response(200, "Tue, 20 Oct 2026 09:00:00 GMT", '"e2"', len(new_body)))
    changed = out["overwrite_check"]["changed"]
    assert len(changed) == 2 and out["overwrite_check"]["unchanged"] == 0 and out["attempts"] == 4   # 2 HEAD + 2 GET
    for name in changed:
        hashes = [r[0] for r in conn.execute("SELECT content_hash FROM raw_responses WHERE request_key = ? ORDER BY raw_id",
                                             (nba_injury.request_key(name),))]
        assert len(hashes) == 2 and hashes[0] != hashes[1]      # the first file is kept next to the new one
        log = [r[1:] for r in attempts(conn) if r[0] == name]
        assert log == [("collect", "stored"), ("overwrite_check", "changed_upstream"), ("redownload", "stored")]
        first = conn.execute("SELECT raw_id FROM raw_responses WHERE request_key = ? ORDER BY raw_id LIMIT 1",
                             (nba_injury.request_key(name),)).fetchone()[0]
        assert raw_cache.load(conn, first) == pdf(name)


def test_overwrite_check_never_starves_collection_and_reports_head_failures(conn):
    collect(conn, lambda n: ok(pdf(n)))
    heads = []
    busy = injuries.run(conn, now=NEXT_DAY, fetch=lambda n: ok(pdf(n)), head=heads.append, sleep=lambda _s: None)
    assert busy["attempts"] == 8 and len(busy["stored"]) == 8 and heads == []   # collection used the whole run budget
    catch_up(conn, NEXT_DAY, lambda n: ok(pdf(n)))

    def broken(name):
        raise ConnectionError("down")
    out = injuries.run(conn, now=NEXT_DAY, fetch=lambda n: ok(pdf(n)), head=broken, sleep=lambda _s: None)
    assert out["overwrite_check"]["failed"] == 2 and out["status"] == "failed"
    assert attempts(conn, "method, outcome")[-2:] == [("HEAD", "failed")] * 2


def stored_then_caught_up(conn):
    collect(conn, lambda n: ok(pdf(n)))
    catch_up(conn, NEXT_DAY, lambda n: ok(pdf(n)))


@pytest.mark.parametrize("status", [500, 403, 404])
def test_codex_head_error_is_a_failed_check_not_a_change(conn, status):
    # Codex review: two HTTP 500 HEAD responses used to give status=success, changed=2, failed=0.
    stored_then_caught_up(conn)
    gets = []
    out = injuries.run(conn, now=NEXT_DAY, sleep=lambda _s: None, fetch=lambda n: gets.append(n) or ok(pdf(n)),
                       head=lambda n: Response(status, None, None, None))
    assert out["status"] == "failed" and gets == []
    assert out["overwrite_check"] == {"checked": 0, "unchanged": 0, "changed": [], "failed": 2, "redownload_failed": []}
    rows = [r for r in attempts(conn, "method, outcome, http_status, error") if r[0] == "HEAD"]
    assert [r[1:3] for r in rows] == [("failed", status)] * 2 and all("state is unknown" in r[3] for r in rows)


def test_codex_changed_file_whose_download_fails_makes_the_job_fail(conn):
    stored_then_caught_up(conn)

    def broken(name):
        raise ConnectionError("down")
    out = injuries.run(conn, now=NEXT_DAY, sleep=lambda _s: None, fetch=broken,
                       head=lambda n: Response(200, "Tue, 20 Oct 2026 09:00:00 GMT", '"e2"', 99))
    check = out["overwrite_check"]
    assert out["status"] == "failed" and len(check["changed"]) == 2 and check["redownload_failed"] == check["changed"]
    for name in check["changed"]:
        assert [r[1:] for r in attempts(conn) if r[0] == name] == \
            [("collect", "stored"), ("overwrite_check", "changed_upstream"), ("redownload", "failed")]
        assert conn.execute("SELECT COUNT(*) FROM raw_responses WHERE request_key = ?",
                            (nba_injury.request_key(name),)).fetchone()[0] == 1          # the original is kept
    served = injuries.run(conn, now=NEXT_DAY, sleep=lambda _s: None, fetch=lambda n: Response(403, None, None, None),
                          head=lambda n: 1 / 0)
    assert served["overwrite_check"]["checked"] == 0      # already checked today: no second round of HEADs


def test_missing_etag_is_settled_by_a_hash_comparison_not_by_matching_metadata(conn):
    # Stored without an ETag; the HEAD also has none, and its date and length equal the stored ones.
    no_etag = lambda body: Response(200, "Mon, 19 Oct 2026 11:00:05 GMT", None, len(body), body)  # noqa: E731
    collect(conn, lambda n: no_etag(pdf(n)))
    catch_up(conn, NEXT_DAY, lambda n: no_etag(pdf(n)))
    same_len = {n: (b"%PDF-1.7 " + bytes(reversed(n.encode()))) for n in NAMES}     # different bytes, same length
    out = injuries.run(conn, now=NEXT_DAY, sleep=lambda _s: None,
                       head=lambda n: Response(200, "Mon, 19 Oct 2026 11:00:05 GMT", None, len(pdf(n))),
                       fetch=lambda n: no_etag(same_len[n] if n == NAMES[0] else pdf(n)))
    check = out["overwrite_check"]
    assert check["checked"] == 2 and check["failed"] == 0 and out["attempts"] == 4        # 2 HEAD + 2 GET, all budgeted
    picked = sorted({r[0] for r in attempts(conn) if r[1] == "overwrite_check"})
    expect_changed = [n for n in picked if n == NAMES[0]]
    assert check["changed"] == expect_changed and check["unchanged"] == 2 - len(expect_changed)
    for name in picked:
        raws = conn.execute("SELECT COUNT(*) FROM raw_responses WHERE request_key = ?",
                            (nba_injury.request_key(name),)).fetchone()[0]
        assert raws == (2 if name == NAMES[0] else 1)       # identical bytes reuse the raw file; changed bytes add one


def test_missing_etag_without_budget_or_with_a_failed_download_is_a_failed_check(conn, monkeypatch):
    no_etag = lambda body: Response(200, None, None, len(body), body)  # noqa: E731
    collect(conn, lambda n: no_etag(pdf(n)))
    catch_up(conn, NEXT_DAY, lambda n: no_etag(pdf(n)))
    monkeypatch.setattr(injuries, "MAX_ATTEMPTS_PER_RUN", 2)

    def broken(name):
        raise ConnectionError("down")
    out = injuries.run(conn, now=NEXT_DAY, sleep=lambda _s: None, fetch=broken,
                       head=lambda n: Response(200, None, None, 12))
    assert out["attempts"] == 2 and out["status"] == "failed"      # HEAD, then the failed GET; budget then exhausted
    assert out["overwrite_check"]["failed"] == 1 and out["overwrite_check"]["checked"] == 0
    assert [r for r in attempts(conn, "method, purpose, outcome")][-2:] == \
        [("GET", "redownload", "failed"), ("HEAD", "overwrite_check", "failed")]


# ---- 8. switch, job log and dry run --------------------------------------------------------------------------------

def test_nothing_is_collected_while_the_switch_is_off(conn, monkeypatch):
    monkeypatch.delenv("NBA_INJURY_COLLECTION", raising=False)
    calls = []
    out = jobs.injury_reports(conn, now=EARLY, fetch=lambda n: calls.append(n) or ok(pdf()), sleep=lambda _s: None)
    assert out["status"] == "disabled" and calls == [] and attempts(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0] == 0
    dry = jobs.injury_reports(conn, dry_run=True, now=EARLY, fetch=lambda n: calls.append(n) or ok(pdf()))
    assert dry["dry_run"] and dry["would_request"] == NAMES and calls == [] and attempts(conn) == []
    monkeypatch.setenv("NBA_INJURY_COLLECTION", "1")
    on = jobs.injury_reports(conn, now=EARLY, fetch=lambda n: ok(pdf(n)), sleep=lambda _s: None)
    assert on["status"] == "success" and on["attempts"] == 3 and on["stored"] == NAMES
    assert [tuple(r) for r in conn.execute("SELECT job, status, attempts FROM job_runs")] == [("injuries", "success", 3)]
    bad = jobs.injury_reports(conn, enabled=True, now=datetime(2026, 10, 19, 12, 31, tzinfo=UTC), fetch=down([]),
                              sleep=lambda _s: None)
    assert bad["status"] == "failed" and len(bad["failed"]) == 1


# ---- 9. append-only ------------------------------------------------------------------------------------------------

def test_attempt_log_is_append_only_even_from_a_plain_connection(conn, tmp_path):
    collect(conn, lambda n: ok(pdf(n)))
    external = sqlite3.connect(tmp_path / "inj.sqlite")   # recursive_triggers off (SQLite default)
    try:
        row = external.execute("SELECT attempt_id, file_name, slot_at, method, purpose, started_at, completed_at, outcome, "
                               "fetch_id FROM injury_report_attempts LIMIT 1").fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="conflicting insert rejected"):
            external.execute("INSERT OR REPLACE INTO injury_report_attempts (attempt_id, file_name, slot_at, method, purpose, "
                             "started_at, completed_at, outcome, fetch_id) VALUES (?,?,?,?,?,?,?,?,?)", row)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            external.execute("UPDATE injury_report_attempts SET outcome = 'failed'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            external.execute("DELETE FROM injury_report_attempts")
    finally:
        external.close()


# ---- 10. migration, coverage, replay -------------------------------------------------------------------------------

def test_migration_keeps_job_rows_and_allows_the_new_job(tmp_path):
    c = db.connect(tmp_path / "m.sqlite")
    db.migrate(c, upto=9)
    c.execute("INSERT INTO job_runs (job, started_at, status) VALUES ('shadow', '2026-10-01T00:00:00Z', 'success')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO job_runs (job, started_at, status) VALUES ('injuries', '2026-10-01T00:00:00Z', 'success')")
    assert db.migrate(c, upto=11) == ["0010_injury_report_attempts.sql", "0011_near_tipoff.sql"]
    c.execute("INSERT INTO job_runs (job, started_at, status) VALUES ('injuries', '2026-10-01T00:01:00Z', 'success')")
    assert [r[0] for r in c.execute("SELECT job FROM job_runs ORDER BY run_id")] == ["shadow", "injuries"]
    c.close()


def test_coverage_summary_and_replay_leave_the_collection_alone(conn, monkeypatch):
    def fetch(name):
        return ok(pdf(name)) if name != NAMES[1] else Response(403, None, None, None)
    collect(conn, fetch)
    summary = injuries.summary(conn, now=EARLY)
    day = summary["days"]["2026-10-19"]
    assert (day["expected"], day["stored"], day["stored_late"], day["not_stored_yet"], day["given_up"]) == (3, 2, 1, 1, 0)
    assert day["median_minutes_slot_to_retrieval"] == 61.0 and summary["reports_stored"] == 2
    assert summary["attempts_by_purpose_and_outcome"] == {"collect:not_available": 1, "collect:stored": 2}
    assert "injury_reports" in coverage.report(conn)
    before = attempts(conn, "*")
    ingest.replay(conn)                                   # nothing to parse for this source
    assert attempts(conn, "*") == before
