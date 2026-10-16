"""Content-addressed cache of original source responses. Files and rows are never overwritten."""
import gzip
import hashlib
import sqlite3
from pathlib import Path
from typing import Optional

from . import config


class CacheIntegrityError(RuntimeError):
    pass


def store(conn: sqlite3.Connection, **kw) -> int:
    """Saves a response and logs the fetch; returns the raw_id (see store_fetch)."""
    return store_fetch(conn, **kw)[0]


def store_fetch(conn: sqlite3.Connection, *, source: str, endpoint: str, request_key: str, body: bytes,
                retrieved_at: str, importer_version: str, cache_dir: Optional[Path] = None,
                suffix: str = ".json") -> tuple[int, int]:
    """Saves a response and logs the fetch; returns (raw_id, fetch_id). Identical bytes for the same request reuse the
    existing raw row, but every retrieval gets its own raw_fetches row, so a repeat of earlier content stays observable.
    `suffix` names the cached file's type (".json" by default; ".pdf" for injury reports); the bytes are stored as given."""
    cache_dir = cache_dir or config.raw_dir()
    digest = hashlib.sha256(body).hexdigest()
    rel = f"{source}/{digest[:2]}/{digest}{suffix}.gz"
    path = cache_dir / rel
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(gzip.compress(body, mtime=0))
        tmp.rename(path)
    row = conn.execute("SELECT raw_id FROM raw_responses WHERE source=? AND request_key=? AND content_hash=?",
                       (source, request_key, digest)).fetchone()
    if row:
        raw_id = row["raw_id"]
    else:
        raw_id = conn.execute(
            "INSERT INTO raw_responses (source, endpoint, request_key, content_hash, byte_size, path, importer_version,"
            " first_retrieved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (source, endpoint, request_key, digest, len(body), rel, importer_version, retrieved_at)).lastrowid
    fetch_id = conn.execute("INSERT INTO raw_fetches (raw_id, retrieved_at) VALUES (?, ?)",
                            (raw_id, retrieved_at)).lastrowid
    return raw_id, fetch_id


def missing_files(conn: sqlite3.Connection, cache_dir: Optional[Path] = None) -> list[str]:
    """Cache paths the store references whose file is absent (a data directory copied without its `raw/` folder). Checks
    existence only; `load` still verifies each file's hash when it is read."""
    cache_dir = cache_dir or config.raw_dir()
    return [r[0] for r in conn.execute("SELECT path FROM raw_responses ORDER BY raw_id") if not (cache_dir / r[0]).is_file()]


def load(conn: sqlite3.Connection, raw_id: int, cache_dir: Optional[Path] = None) -> bytes:
    """Reads cached bytes and verifies them against the recorded hash."""
    cache_dir = cache_dir or config.raw_dir()
    row = conn.execute("SELECT path, content_hash FROM raw_responses WHERE raw_id=?", (raw_id,)).fetchone()
    if row is None:
        raise KeyError(raw_id)
    body = gzip.decompress((cache_dir / row["path"]).read_bytes())
    if hashlib.sha256(body).hexdigest() != row["content_hash"]:
        raise CacheIntegrityError(f"Cached file for raw_id {raw_id} does not match its recorded hash")
    return body
