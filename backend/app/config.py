"""Single configuration path for local storage. Override the data directory with NBA_DATA_DIR."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return Path(os.environ.get("NBA_DATA_DIR", REPO_ROOT / "data"))


def db_path() -> Path:
    return data_dir() / "nba.sqlite"


def raw_dir() -> Path:
    return data_dir() / "raw"



def shadow_enabled() -> bool:
    """Whether `daily` also refreshes possession stats and records shadow forecasts. Off unless NBA_SHADOW_RECORDING=1."""
    return os.environ.get("NBA_SHADOW_RECORDING") == "1"


def injury_collection_enabled() -> bool:
    """Whether `app.cli injuries` may make requests. Off unless NBA_INJURY_COLLECTION=1 (set only in its own agent)."""
    return os.environ.get("NBA_INJURY_COLLECTION") == "1"
