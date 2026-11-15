"""Single configuration path for local storage. Override the data directory with NBA_DATA_DIR."""
import os
from pathlib import Path


def telegram_settings() -> dict:
    """Private local bot settings; environment overrides the ignored, owner-only credentials file."""
    import stat
    settings = {}
    path = telegram_credentials_path()
    if path.exists():
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError('Telegram credentials must be a private regular file (chmod 600 .env.telegram).')
        for line in path.read_text().splitlines():
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                settings[key.strip()] = value.strip()
    for key in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_PAIR_CODE'):
        if key in os.environ:
            settings[key] = os.environ[key]
    return settings


def save_telegram_setting(key: str, value: str) -> None:
    """Atomic private-file update; never prints secrets or places them in a tracked file."""
    import tempfile
    if key not in ('TELEGRAM_CHAT_ID', 'TELEGRAM_PAIR_CODE') or '\n' in value:
        raise ValueError('Invalid Telegram setting.')
    settings = telegram_settings()
    settings[key] = value
    path = telegram_credentials_path()
    fd, name = tempfile.mkstemp(prefix='.env.telegram.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(''.join(f'{k}={v}\n' for k, v in settings.items()))
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return Path(os.environ.get("NBA_DATA_DIR", REPO_ROOT / "data"))


def protected_data_dir(path: Path) -> bool:
    """A data folder holding live stores: this checkout's own `data/` folder (the default data directory) or the folder
    directly inside the root of a main git checkout (its parent has a `.git` DIRECTORY; linked worktrees have a `.git`
    file; copies, scratch and test folders have none). Development commands never write, migrate or create a store there
    (cli._write_refusal, db.migrate); only the verified release environment may (release.migration_context).
    Path checks only: no subprocess, nothing at import time."""
    p, own = Path(path).expanduser().resolve(), (REPO_ROOT / "data").resolve()
    if p == own:
        return True
    try:            # by file identity when both exist: another spelling (case variant, firmlink) of the same folder
        if os.path.samefile(p, own):
            return True
    except (FileNotFoundError, NotADirectoryError):
        pass
    except OSError:
        return True     # cannot tell: fail closed
    return (p.parent / ".git").is_dir()


def protected_path(path: Path) -> bool:
    """True when `path`, with links followed, is a protected data folder or lies inside one (a store file, its raw/ or
    research/ folder, a lock file), so a link from an unprotected folder into the live one is caught too."""
    p = Path(path).expanduser().resolve()
    return any(protected_data_dir(a) for a in (p, *p.parents))


def lock_path(sport: str) -> Path:
    """The store's job-lock file (db.store_lock)."""
    return data_dir() / f".{sport}-jobs.lock"


# A release checkout imports code from its own folder, so REPO_ROOT is not where the production receipts and credentials
# live: the release environment names them explicitly (scripts/launchd, app/release.delivery_check).
TELEGRAM_LEDGER_ENV = 'NBA_TELEGRAM_LEDGER'
TELEGRAM_CREDENTIALS_ENV = 'NBA_TELEGRAM_CREDENTIALS'


def telegram_credentials_path() -> Path:
    """The private bot settings file: named explicitly in the release environment, else this checkout's own.
    A development copy (NBA_DATA_DIR elsewhere) still reads these same credentials: tests must mock Telegram."""
    explicit = os.environ.get(TELEGRAM_CREDENTIALS_ENV)
    return Path(explicit) if explicit else REPO_ROOT / '.env.telegram'


def telegram_ledger_path() -> Path:
    """Preserve the production ledger; isolate development copies with their data directory."""
    explicit = os.environ.get(TELEGRAM_LEDGER_ENV)
    if explicit:
        return Path(explicit)
    directory = data_dir()
    if directory.resolve() == (REPO_ROOT / 'data').resolve():
        return REPO_ROOT / '.telegram-delivery.sqlite'
    if (directory.resolve().parent / '.git').is_dir():   # another main checkout's live data folder: its ledger, never a new one
        return directory.resolve().parent / '.telegram-delivery.sqlite'
    return directory / '.telegram-delivery.sqlite'


def db_path(sport: str = "nba") -> Path:
    """Each sport has its own store (docs/MULTISPORT_PLAN.md); the NBA store keeps its original file name.
    Data-only sports (NFL) have a store path too; serving them is refused elsewhere (sports.get, live.engine)."""
    from . import sports
    return data_dir() / sports.store(sport).db_file


def raw_dir() -> Path:
    return data_dir() / "raw"


def live_data_dir() -> Path:
    """Where the scheduled jobs and the trainer write. Training-run status reads it (read-only). Defaults to the data
    directory; the development launcher (scripts/dev.sh) serves a snapshot copy and points NBA_LIVE_DATA_DIR at the live one."""
    return Path(os.environ.get("NBA_LIVE_DATA_DIR", data_dir()))



def shadow_enabled() -> bool:
    """Whether `daily` also refreshes possession stats and records shadow forecasts. Off unless NBA_SHADOW_RECORDING=1."""
    return os.environ.get("NBA_SHADOW_RECORDING") == "1"


def injury_collection_enabled() -> bool:
    """Whether `app.cli injuries` may make requests. Off unless NBA_INJURY_COLLECTION=1 (set only in its own agent). Not the
    only collector: the NBA two-hour cycle (jobs.predict_now) also fetches the official report whenever the sealed
    availability facts (research/forward-facts.json) exist, because that variant needs a report from the last 60 minutes."""
    return os.environ.get("NBA_INJURY_COLLECTION") == "1"


def player_box_refresh_enabled() -> bool:
    """Whether the nightly job may re-download the current season's NBA player box (jobs.player_box_refresh). Off unless
    NBA_PLAYER_BOX_REFRESH=1, activated only with an approved service configuration change."""
    return os.environ.get("NBA_PLAYER_BOX_REFRESH", "0") == "1"


def nhl_player_box_refresh_enabled() -> bool:
    """Whether the pre-training step (training_inputs.collect) downloads the NHL player boxes of new finals that the NHL
    injury member reads. Off unless NHL_PLAYER_BOX_REFRESH=1, activated only with an approved service configuration change."""
    return os.environ.get("NHL_PLAYER_BOX_REFRESH", "0") == "1"


def daily_agreement_enabled() -> bool:
    """Opt-in recording, activated only with an approved service configuration change."""
    return os.environ.get("NBA_DAILY_AGREEMENT", "0") == "1"


def nhl_injury_capture_enabled() -> bool:
    """Whether the NHL two-hour cycle captures the live ESPN NHL injuries page (sources.nhl_injuries.capture_live) into the
    research cache the NHL injury member reads. Off unless NHL_INJURY_CAPTURE=1, activated only with an approved service
    configuration change."""
    return os.environ.get("NHL_INJURY_CAPTURE", "0") == "1"
