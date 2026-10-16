"""Single configuration path for local storage. Override the data directory with NBA_DATA_DIR."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return Path(os.environ.get("NBA_DATA_DIR", REPO_ROOT / "data"))


def db_path(sport: str = "nba") -> Path:
    """Each sport has its own store (docs/MULTISPORT_PLAN.md); the NBA store keeps its original file name."""
    from . import sports
    return data_dir() / sports.get(sport).db_file


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
