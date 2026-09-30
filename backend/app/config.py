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

