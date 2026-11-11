"""SQLite connection and forward-only migrations (app/migrations/NNNN_name.sql)."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
# Migrations that rebuild a table other tables reference: run with foreign keys off, every reference checked before commit.
PARENT_REBUILDS = frozenset({"0033_nfl_market_quotes.sql", "0035_result_integrity.sql"})


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StoreMismatch(RuntimeError):
    pass


def connect(path: Optional[Path] = None, *, check_same_thread: bool = True, sport: Optional[str] = None) -> sqlite3.Connection:
    """check_same_thread=False is for the API only: one connection per request, never shared concurrently, but
    FastAPI may run the dependency and the endpoint on different thread-pool threads.
    With `sport` and no path, opens that sport's store; call `claim_store` after migrating to bind and check its identity."""
    path = Path(path) if path else config.db_path(sport or "nba")
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Defence in depth: REPLACE conflict deletions fire DELETE triggers. Schema triggers (0002) protect other connections.
    conn.execute("PRAGMA recursive_triggers = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def connect_readonly(sport: str) -> sqlite3.Connection:
    """A connection that cannot write (`query_only`): no migration, no journal change, no job lock. For commands that only
    read, so they never block or delay a scheduled cycle. The store must already exist."""
    path = config.db_path(sport)
    if not path.is_file():
        raise FileNotFoundError(f"No {sport} store at {path}")
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


class transaction:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, *_):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


class store_lock:
    """One writing job per store at a time (advisory lock on a file next to the store, released when the job ends or its
    process dies). Entering returns True when the lock is held, False when another job still holds it after `wait_seconds`
    (None waits without limit; 0 does not wait). Overlapping scheduled jobs therefore never migrate, refresh or forecast
    on the same store together; the two sports' stores are independent."""

    def __init__(self, sport: str, wait_seconds: Optional[float] = None, poll_seconds: float = 2.0):
        self.path = config.data_dir() / f".{sport}-jobs.lock"
        self.wait, self.poll, self.fd = wait_seconds, poll_seconds, None

    def __enter__(self) -> bool:
        import fcntl
        import os
        import time
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = None if self.wait is None else time.monotonic() + self.wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.fd = fd
                return True
            except BlockingIOError:
                if deadline is not None and time.monotonic() >= deadline:
                    os.close(fd)
                    return False
                time.sleep(self.poll if deadline is None else max(0.0, min(self.poll, deadline - time.monotonic())))

    def __exit__(self, *_):
        import os
        if self.fd is not None:
            os.close(self.fd)          # closing the descriptor releases the lock
            self.fd = None
        return False


def migrate(conn: sqlite3.Connection, upto: Optional[int] = None) -> list[str]:
    """Applies pending migrations in order (optionally only through version `upto`); returns the names applied."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
    done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for f in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        version = int(f.name[:4])
        if version in done or (upto is not None and version > upto):
            continue
        parent_rebuild = f.name in PARENT_REBUILDS
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        if parent_rebuild:
            conn.execute("PRAGMA foreign_keys = OFF")
        try:
            with transaction(conn):
                for stmt in _split(f.read_text()):
                    conn.execute(stmt)
                if parent_rebuild and conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise sqlite3.IntegrityError(f"foreign key violation in the {f.name} table rebuild")
                conn.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)", (version, f.name, utc_now()))
        finally:
            if parent_rebuild:
                conn.execute(f"PRAGMA foreign_keys = {foreign_keys}")
                conn.execute("PRAGMA legacy_alter_table = OFF")
        applied.append(f.name)
    return applied


def _split(sql: str) -> list[str]:
    """Splits a migration into statements, keeping trigger bodies (BEGIN ... END;) intact."""
    out, buf = [], ""
    for line in sql.splitlines(keepends=True):
        if line.lstrip().startswith("--"):
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        raise ValueError(f"Incomplete SQL statement in migration: {buf[:80]}")
    return out


def store_sport(conn: sqlite3.Connection) -> Optional[str]:
    """The sport this store belongs to, or None for a store that predates identities (or is not migrated)."""
    if not has_table(conn, "store_identity"):
        return None
    row = conn.execute("SELECT sport FROM store_identity").fetchone()
    return row["sport"] if row else None


def has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def claim_store(conn: sqlite3.Connection, sport: str) -> Optional[str]:
    """Binds an unclaimed store to `sport` (once, append-only), or checks that it already belongs to it.
    A store holding another sport's data is never used: that would mix leagues in one model or ledger."""
    if not has_table(conn, "store_identity"):
        return None   # a schema before 0012 (only migration rehearsals); every entry point migrates first
    current = store_sport(conn)
    if current is None:
        conn.execute("INSERT INTO store_identity (sport, claimed_at) VALUES (?, ?)", (sport, utc_now()))
        return sport
    if current != sport:
        raise StoreMismatch(f"This store holds {current!r} data, not {sport!r}")
    return current
