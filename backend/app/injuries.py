"""Raw collection of NBA injury-report PDFs (pilot; docs/INJURY_COLLECTOR_PLAN.md).

Saves each report unchanged in the raw cache and logs every HTTP attempt in injury_report_attempts. Nothing here reads
a report's content: no parsing, no player data, no model input. A stored report's availability is the time its
response finished arriving (`completed_at`), never the time in its name and never the request's start time.

Budgets count every attempt, including retries, 403s and failures: MAX_ATTEMPTS_PER_RUN, MAX_ATTEMPTS_PER_SLOT (ever)
and MAX_ATTEMPTS_PER_DAY (rolling 24 hours), all read from the attempt log.
"""
import hashlib
import random
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import raw_cache
from .db import claim_store, transaction, utc_now
from .live import ELIGIBLE_TYPES
from .sources import nba_injury

SLOT_MINUTES = 30                                 # the :00 and :30 reports
QUARTER_MINUTES = 15                              # the :15 and :45 reports (owner, Nov 11: every published report before games)
LOOKBACK = timedelta(hours=24)                    # unfinished slots this old are still collected (late)
WINDOW_BEFORE_TIPOFF = timedelta(hours=36)        # a :00/:30 slot is due if an eligible game tips off within this time after it
WINDOW_AFTER_TIPOFF = timedelta(hours=6)          # ... or tipped off within this time before it
QUARTER_WINDOW_BEFORE_TIPOFF = timedelta(hours=6) # a :15/:45 slot only if a game tips off within this time after it
PUBLISH_MARGIN = timedelta(seconds=60)            # a slot is not requested until this long after its time
LATE_AFTER = timedelta(minutes=30)
MAX_ATTEMPTS_PER_RUN = 8
MAX_ATTEMPTS_PER_SLOT = 4
MAX_ATTEMPTS_PER_QUARTER_SLOT = 1                 # a missing :15/:45 file costs one request (review MEDIUM, Nov 11): the next
                                                  # report is 15 minutes away, and retries must never starve the evening ones
MAX_ATTEMPTS_PER_DAY = 160                        # ~67 reports on a full game day (Nov 11: :15/:45 added) + retries
MAX_ATTEMPTS_PER_SLOT_PER_RUN = 2
OVERWRITE_CHECKS_PER_DAY = 2
OVERWRITE_CHECK_AGE = (timedelta(hours=24), timedelta(hours=48))
BACKOFF_SECONDS = (2, 8)
FMT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class Slot:
    at: str      # the slot's time, UTC
    name: str    # the report's file name (Eastern wall time)


def _parse(value: str) -> datetime:
    return datetime.strptime(value, FMT).replace(tzinfo=timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(FMT)


def slots_between(start: datetime, end: datetime, step: int = SLOT_MINUTES) -> list[Slot]:
    """Slots every `step` minutes with start < time <= end, oldest first. When clocks go back, the repeated Eastern hour
    maps two instants to one file name; the first instant is kept. When clocks go forward, the skipped hour has no slot."""
    first = start.astimezone(timezone.utc).replace(second=0, microsecond=0)
    first = first.replace(minute=first.minute - first.minute % step) + timedelta(minutes=step)
    out, seen, t = [], set(), first
    while t <= end:
        name = nba_injury.file_name(t)
        if name not in seen:
            seen.add(name)
            out.append(Slot(_fmt(t), name))
        t += timedelta(minutes=step)
    return out


def due_slots(conn: sqlite3.Connection, start: datetime, end: datetime) -> list[Slot]:
    """Slots inside a game window: a :00/:30 slot when an eligible game (not postponed or canceled) tips off within
    WINDOW_BEFORE_TIPOFF after it or tipped off within WINDOW_AFTER_TIPOFF before it; a :15/:45 slot only when a game
    tips off within QUARTER_WINDOW_BEFORE_TIPOFF after it. No game nearby means no request."""
    tipoffs = [_parse(r[0]) for r in conn.execute(
        f"SELECT tipoff_at FROM games WHERE status NOT IN ('postponed', 'canceled') AND tipoff_at > ? AND tipoff_at <= ? "
        f"AND season_type IN ({', '.join('?' * len(ELIGIBLE_TYPES))})",
        (_fmt(start - WINDOW_AFTER_TIPOFF), _fmt(end + WINDOW_BEFORE_TIPOFF), *ELIGIBLE_TYPES))]
    out = []
    for s in slots_between(start, end, QUARTER_MINUTES):
        t = _parse(s.at)
        if t.minute % SLOT_MINUTES == 0:
            if any(t - WINDOW_AFTER_TIPOFF < tip <= t + WINDOW_BEFORE_TIPOFF for tip in tipoffs):
                out.append(s)
        elif any(t < tip <= t + QUARTER_WINDOW_BEFORE_TIPOFF for tip in tipoffs):
            out.append(s)
    return out


def slot_limit(slot: Slot) -> int:
    """Lifetime attempts a slot may use: four for :00/:30, one for :15/:45."""
    return MAX_ATTEMPTS_PER_SLOT if _parse(slot.at).minute % SLOT_MINUTES == 0 else MAX_ATTEMPTS_PER_QUARTER_SLOT


def _slot_state(conn: sqlite3.Connection, name: str) -> tuple[bool, int]:
    """(already stored, collect attempts ever made) for one report."""
    row = conn.execute("SELECT COALESCE(SUM(outcome = 'stored'), 0), COUNT(*) FROM injury_report_attempts "
                       "WHERE file_name = ? AND purpose = 'collect'", (name,)).fetchone()
    return bool(row[0]), row[1]


def plan(conn: sqlite3.Connection, now: datetime) -> dict:
    """Due slots of the last LOOKBACK, split into stored, pending (oldest first) and given up."""
    due = due_slots(conn, now - LOOKBACK, now - PUBLISH_MARGIN)
    out = {"due": len(due), "stored": 0, "pending": [], "given_up": []}
    for s in due:
        stored, attempts = _slot_state(conn, s.name)
        if stored:
            out["stored"] += 1
        elif attempts >= slot_limit(s):
            out["given_up"].append(s)
        else:
            out["pending"].append(s)
    return out


def _newest_first(pending: list) -> list:
    """Collection order: the newest due report first. A missing (404) older slot costs one attempt per run until it is
    given up; with several of them ahead in the queue the latest report, the one the injury-aware forecast needs within
    60 minutes, could wait for runs. Older slots are still collected with the rest of the run's budget."""
    return sorted(pending, key=lambda slot: slot.at, reverse=True)


def attempts_since(conn: sqlite3.Connection, since: datetime) -> int:
    return conn.execute("SELECT COUNT(*) FROM injury_report_attempts WHERE started_at > ?", (_fmt(since),)).fetchone()[0]


def _record(conn: sqlite3.Connection, slot: Slot, method: str, purpose: str, started: str, completed: str, outcome: str,
            resp: Optional[nba_injury.Response] = None, error: Optional[str] = None, fetch_id: Optional[int] = None,
            sha256: Optional[str] = None, late: bool = False) -> None:
    conn.execute(
        "INSERT INTO injury_report_attempts (file_name, slot_at, method, purpose, started_at, completed_at, outcome, "
        "http_status, last_modified, etag, content_length, sha256, byte_size, fetch_id, late, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (slot.name, slot.at, method, purpose, started, completed, outcome, resp.status if resp else None,
         resp.last_modified if resp else None, resp.etag if resp else None, resp.content_length if resp else None,
         sha256, len(resp.body) if sha256 and resp else None, fetch_id, int(late), error))


def _download(conn: sqlite3.Connection, slot: Slot, purpose: str, fetch: Callable[[str], nba_injury.Response],
              clock: Callable[[], str]) -> str:
    """One GET attempt, logged whatever happens. Returns the outcome."""
    started = clock()
    try:
        resp = fetch(slot.name)
    except Exception as exc:  # network error: counted and logged
        _record(conn, slot, "GET", purpose, started, clock(), "failed",
                error=f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}")
        return "failed"
    completed = clock()   # the retrieval time: read only after the whole response has arrived
    if resp.status in (403, 404):   # the server gives 403 for a file that is not there
        _record(conn, slot, "GET", purpose, started, completed, "not_available", resp)
        return "not_available"
    error = f"HTTP {resp.status}" if resp.status != 200 else nba_injury.problem(resp)
    if error:
        _record(conn, slot, "GET", purpose, started, completed, "failed", resp, error=error)
        return "failed"
    try:
        with transaction(conn):
            claim_store(conn, "nba")
            _, fetch_id = raw_cache.store_fetch(
                conn, source=nba_injury.SOURCE, endpoint=nba_injury.BASE, request_key=nba_injury.request_key(slot.name),
                body=resp.body, retrieved_at=completed, importer_version=nba_injury.IMPORTER_VERSION, suffix=".pdf")
            _record(conn, slot, "GET", purpose, started, completed, "stored", resp, fetch_id=fetch_id,
                    sha256=hashlib.sha256(resp.body).hexdigest(),
                    late=_parse(completed) - _parse(slot.at) > LATE_AFTER)
    except Exception as exc:  # storage failure: the request was made and still counts
        _record(conn, slot, "GET", purpose, started, clock(), "failed", resp, error=f"store {type(exc).__name__}: {exc}")
        return "failed"
    return "stored"


def _overwrite_candidates(conn: sqlite3.Connection, now: datetime) -> list[sqlite3.Row]:
    """Up to OVERWRITE_CHECKS_PER_DAY stored reports from one to two days ago, chosen by a date-seeded shuffle."""
    newest, oldest = _fmt(now - OVERWRITE_CHECK_AGE[0]), _fmt(now - OVERWRITE_CHECK_AGE[1])
    rows = conn.execute(
        "SELECT a.* FROM injury_report_attempts a WHERE a.outcome = 'stored' AND a.slot_at > ? AND a.slot_at <= ? "
        "AND a.attempt_id = (SELECT MAX(b.attempt_id) FROM injury_report_attempts b WHERE b.file_name = a.file_name "
        "AND b.outcome = 'stored') ORDER BY a.file_name", (oldest, newest)).fetchall()
    random.Random(int(now.strftime("%Y%m%d"))).shuffle(rows)
    return rows[:OVERWRITE_CHECKS_PER_DAY]


def run(conn: sqlite3.Connection, *, now: Optional[datetime] = None, dry_run: bool = False,
        fetch: Callable[[str], nba_injury.Response] = nba_injury.fetch,
        head: Callable[[str], nba_injury.Response] = nba_injury.head,
        sleep: Callable[[float], None] = time.sleep, clock: Optional[Callable[[], str]] = None) -> dict:
    """One collection run. `now`, `clock`, `fetch`, `head` and `sleep` are replaceable for tests; real runs use the
    system clock and stamp each attempt separately."""
    if clock is None:
        fixed = _fmt(now) if now else None
        clock = (lambda: fixed) if fixed else utc_now
    now = now or datetime.now(timezone.utc)
    p = plan(conn, now)
    out = {"as_of": _fmt(now), "due": p["due"], "already_stored": p["stored"], "pending": len(p["pending"]),
           "given_up": [s.name for s in p["given_up"]], "stored": [], "not_available": [], "failed": [],
           "deferred": 0, "attempts": 0, "overwrite_check": {"checked": 0, "unchanged": 0, "changed": [], "failed": 0, "redownload_failed": []},
           "budgets": {"per_run": MAX_ATTEMPTS_PER_RUN, "per_slot": MAX_ATTEMPTS_PER_SLOT, "per_day": MAX_ATTEMPTS_PER_DAY}}
    if dry_run:
        return {**out, "dry_run": True, "would_request": [s.name for s in _newest_first(p["pending"])][:MAX_ATTEMPTS_PER_RUN]}
    budget = max(0, min(MAX_ATTEMPTS_PER_RUN, MAX_ATTEMPTS_PER_DAY - attempts_since(conn, now - timedelta(hours=24))))

    def spend(wait: float) -> None:
        """Accounts for one attempt and keeps the crawl delay (or a longer backoff) between requests."""
        nonlocal budget
        if out["attempts"]:
            sleep(max(wait, nba_injury.CRAWL_DELAY_SECONDS))
        budget -= 1
        out["attempts"] += 1

    for slot in _newest_first(p["pending"]):
        if budget <= 0:
            out["deferred"] += 1
            continue
        _, lifetime = _slot_state(conn, slot.name)
        tries, outcome = 0, None
        while outcome not in ("stored", "not_available") and tries < MAX_ATTEMPTS_PER_SLOT_PER_RUN \
                and lifetime + tries < slot_limit(slot) and budget > 0:
            spend(BACKOFF_SECONDS[min(tries - 1, len(BACKOFF_SECONDS) - 1)] if tries else 0)
            outcome = _download(conn, slot, "collect", fetch, clock)
            tries += 1
        if tries:
            out[outcome].append(slot.name)

    checked_today = conn.execute("SELECT COUNT(*) FROM injury_report_attempts WHERE purpose = 'overwrite_check' "
                                 "AND started_at > ?", (_fmt(now - timedelta(hours=24)),)).fetchone()[0]
    check = out["overwrite_check"]
    if not checked_today:
        for stored in _overwrite_candidates(conn, now):
            if budget <= 0:
                break
            slot = Slot(stored["slot_at"], stored["file_name"])
            spend(0)
            started = clock()
            try:
                resp = head(slot.name)
            except Exception as exc:
                _record(conn, slot, "HEAD", "overwrite_check", started, clock(), "failed",
                        error=f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}")
                check["failed"] += 1
                continue
            head_done = clock()
            if resp.status != 200:   # an error or a refusal tells us nothing about the file's content
                _record(conn, slot, "HEAD", "overwrite_check", started, head_done, "failed", resp,
                        error=f"HEAD returned HTTP {resp.status}; the file's state is unknown")
                check["failed"] += 1
                continue
            if resp.etag and stored["etag"]:   # the only validator trusted on its own
                changed = resp.etag != stored["etag"]
                _record(conn, slot, "HEAD", "overwrite_check", started, head_done,
                        "changed_upstream" if changed else "unchanged", resp)
                check["checked"] += 1
                if not changed:
                    check["unchanged"] += 1
                    continue
                check["changed"].append(slot.name)
                if budget <= 0:
                    check["redownload_failed"].append(slot.name)   # known to have changed, but not saved this run
                    continue
                spend(0)
                if _download(conn, slot, "redownload", fetch, clock) != "stored":
                    check["redownload_failed"].append(slot.name)
                continue
            # No ETag on one side: equal dates or lengths prove nothing. Compare content hashes with one GET.
            if budget <= 0:
                _record(conn, slot, "HEAD", "overwrite_check", started, head_done, "failed", resp,
                        error="no ETag to compare and no request budget left for a hash comparison")
                check["failed"] += 1
                continue
            spend(0)
            if _download(conn, slot, "redownload", fetch, clock) != "stored":
                _record(conn, slot, "HEAD", "overwrite_check", started, head_done, "failed", resp,
                        error="no ETag to compare and the download for a hash comparison failed")
                check["failed"] += 1
                continue
            new_hash = conn.execute("SELECT sha256 FROM injury_report_attempts WHERE file_name = ? AND purpose = 'redownload' "
                                    "ORDER BY attempt_id DESC LIMIT 1", (slot.name,)).fetchone()[0]
            changed = new_hash != stored["sha256"]
            _record(conn, slot, "HEAD", "overwrite_check", started, head_done,
                    "changed_upstream" if changed else "unchanged", resp)
            check["checked"] += 1
            if changed:
                check["changed"].append(slot.name)   # the new content is already saved as a second raw file
            else:
                check["unchanged"] += 1

    after = plan(conn, now)
    out["given_up"] = [s.name for s in after["given_up"]]
    out["status"] = "failed" if out["failed"] or check["failed"] or check["redownload_failed"] else \
        "success" if out["attempts"] else "skipped"
    return out


def summary(conn: sqlite3.Connection, now: Optional[datetime] = None, days: int = 7) -> dict:
    """Collection coverage for the coverage report: per Eastern day over the last `days`, plus all-time totals."""
    now = now or datetime.now(timezone.utc)
    by_day: dict[str, dict] = {}
    for s in due_slots(conn, now - timedelta(days=days), now - PUBLISH_MARGIN):
        day = by_day.setdefault(_parse(s.at).astimezone(nba_injury.EASTERN).strftime("%Y-%m-%d"),
                                {"expected": 0, "stored": 0, "stored_late": 0, "not_stored_yet": 0, "given_up": 0, "lags": []})
        day["expected"] += 1
        row = conn.execute("SELECT completed_at, late FROM injury_report_attempts WHERE file_name = ? AND purpose = 'collect' "
                           "AND outcome = 'stored' ORDER BY attempt_id LIMIT 1", (s.name,)).fetchone()
        if row:
            day["stored"] += 1
            day["stored_late"] += row["late"]
            day["lags"].append((_parse(row["completed_at"]) - _parse(s.at)).total_seconds() / 60)
        elif _slot_state(conn, s.name)[1] >= slot_limit(s):
            day["given_up"] += 1
        else:
            day["not_stored_yet"] += 1
    for day in by_day.values():
        lags = sorted(day.pop("lags"))
        day["median_minutes_slot_to_retrieval"] = round(lags[len(lags) // 2], 1) if lags else None
    outcomes = {f"{r[0]}:{r[1]}": r[2] for r in conn.execute(
        "SELECT purpose, outcome, COUNT(*) FROM injury_report_attempts GROUP BY 1, 2")}
    stored = conn.execute("SELECT COUNT(DISTINCT file_name), COALESCE(SUM(byte_size), 0) FROM injury_report_attempts "
                          "WHERE outcome = 'stored'").fetchone()
    return {"days": dict(sorted(by_day.items())), "attempts_by_purpose_and_outcome": outcomes,
            "reports_stored": stored[0], "bytes_stored": stored[1],
            "note": "Raw PDFs only; their content is not read. Availability of a stored report is its retrieval time."}
