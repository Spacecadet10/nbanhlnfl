"""Proof that a development data directory is separate from the live one and safe to overwrite.

Single source of truth for scripts/dev.sh (which feeds this file to the backend Python as `python - check-paths LIVE DEV
MARK`, before any server is stopped or any file is touched) and for the preview publisher (app/preview_publish.py), which
calls `check()` before every publication so that nothing it writes can lead into the live directory: relative paths, '..'
and symlinks are resolved, then identity and nesting in either direction are refused by resolved path (case-insensitively)
and by device/inode of every ancestor; an existing development directory must carry the launcher's marker, must not be a
symlink and must contain no symlink anywhere; files written in place must not be hard links. Standard library only.
"""
import os
import stat
import sys

MARK_TEXT = "nba-predictor development data copy; created by scripts/dev.sh, which may overwrite it"


class UnsafePaths(ValueError):
    """The development directory is not provably separate from the live one, or not safe to overwrite."""


MARKER = ".nba-dev-data"
FILES = ["SNAPSHOT_AT", "api.log", "web.log", "publish.log", "PUBLISH_STATUS.json", ".PUBLISH_STATUS.json.tmp",
         ".SNAPSHOT_AT.tmp"] + [n for sport in ("nba", "nhl", "nfl") for n in (
    f"{sport}.sqlite", f"{sport}.sqlite-wal", f"{sport}.sqlite-shm", f"{sport}.sqlite-journal", f".{sport}.sqlite.tmp",
    f".{sport}-jobs.lock")]
DIRS = ["research", "raw", "eval", "reports", ".publish-staging"]
COPIED = ["research", "raw", "eval"]


def ancestors(p):
    while True:
        yield p
        parent = os.path.dirname(p)
        if parent == p:
            return
        p = parent


def ident(p):
    s = os.stat(p)
    return s.st_dev, s.st_ino


def raise_error(exc):
    raise exc


def check(live: str, dev: str, mark: str = MARK_TEXT) -> str:
    """Raise UnsafePaths unless `dev` is provably separate from `live` and safe to write; return a one-line summary."""
    live, dev, mark = str(live), str(dev), mark + "\n"

    def refuse(msg):
        raise UnsafePaths(f"{msg} (live: {live!r}, development: {dev!r})")

    def no_links(top, what):
        for base, dirs, files in os.walk(top, onerror=raise_error):   # never follows links; unreadable dirs are errors
            for n in dirs + files:
                if os.path.islink(os.path.join(base, n)):
                    refuse(f"{what} contains a symlink: {os.path.join(base, n)}")

    try:
        if not live or not dev:
            refuse("empty data directory path")
        if os.path.basename(dev.rstrip("/")) in (".", ".."):     # islink("<link>/.") is false: would hide a symlink
            refuse("development path ends in '.' or '..'; give the directory's own path")
        L = os.path.realpath(live, strict=True)
        if not os.path.isdir(L):
            refuse("live data path is not a directory")
        # Longest existing prefix of the development path, resolved by the OS; the rest must be plain new names.
        raw = dev if os.path.isabs(dev) else os.path.join(os.getcwd(), dev)
        head, tail = raw, []
        while not os.path.lexists(head):
            head, name = os.path.split(head)
            if name:
                tail.insert(0, name)
            elif not head or head == os.path.dirname(head):
                refuse("development path has no existing ancestor")
        if any(n in (".", "..") for n in tail):
            refuse("development path uses '.' or '..' below a directory that does not exist yet")
        E = os.path.realpath(head, strict=True)          # raises for a dangling or looping symlink
        if not os.path.isdir(E):
            refuse("development path (or its existing part) is not a directory")
        D = os.path.join(E, *tail)
        Lc, Dc = os.path.normcase(L).casefold(), os.path.normcase(D).casefold()
        if os.path.commonpath([Lc, Dc]) in (Lc, Dc):
            refuse("development and live data directories are the same or nested")
        live_id = ident(L)
        if any(ident(a) == live_id for a in ancestors(E)):
            refuse("development data directory is the live directory or inside it")
        if not tail:
            dev_id = ident(E)
            if any(ident(a) == dev_id for a in ancestors(L)):
                refuse("live data directory is inside the development directory")
        for sub in COPIED:                                 # copied into the development directory as they are
            p = os.path.join(L, sub)
            if os.path.islink(p):
                refuse(f"live {sub} is a symlink")
            if os.path.isdir(p):
                no_links(p, f"live {sub}")
        if not tail:                                       # existing: reused only if this launcher created it
            if os.path.islink(raw.rstrip("/") or "/"):
                refuse("development data directory is itself a symlink; give its real path")
            m = os.path.join(E, MARKER)
            try:
                st = os.lstat(m)
            except FileNotFoundError:
                refuse(f"existing development data directory {E} was not created by this launcher (no launcher marker "
                       f"{MARKER}); it is never adopted. Set NBA_DEV_DATA_DIR to a path that does not exist yet")
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                refuse(f"launcher marker {m} is a symlink or not a regular file")
            fd = os.open(m, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)   # never waits on a FIFO swapped in meanwhile
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                refuse(f"launcher marker {m} is a symlink or not a regular file")
            with os.fdopen(fd, "rb") as f:
                if f.read(len(mark.encode()) + 1) != mark.encode():
                    refuse(f"launcher marker {m} has unexpected contents")
            for n in FILES + DIRS:
                p = os.path.join(E, n)
                if not os.path.lexists(p):
                    continue
                st = os.lstat(p)
                mode = st.st_mode
                if stat.S_ISLNK(mode):
                    refuse(f"write target is a symlink: {p}")
                if n in FILES and not stat.S_ISREG(mode):
                    refuse(f"write target is not a regular file: {p}")
                if n in FILES and st.st_nlink != 1:          # written in place: another link would receive the write
                    refuse(f"write target is a hard link shared with another file: {p}")
                if n in DIRS and not stat.S_ISDIR(mode):
                    refuse(f"write target is not a directory: {p}")
            no_links(E, "development data directory")
    except UnsafePaths:
        raise
    except Exception as exc:
        refuse(f"could not validate the data directories ({type(exc).__name__}: {exc})")
    return (f"data directories are separate: live {L}, development {D}"
            + (" (launcher-owned)" if not tail else " (will be created)"))


if __name__ == "__main__" and len(sys.argv) >= 5 and sys.argv[1] == "check-paths":
    try:
        print("dev.sh: " + check(sys.argv[2], sys.argv[3], sys.argv[4]))
    except UnsafePaths as exc:
        print(f"dev.sh: {exc}", file=sys.stderr)
        sys.exit(1)
