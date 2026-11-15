"""The approved NFL activation (data/research/nfl_live/activation.json, activated 2026-11-02, definition b6074877a7e7ccfe)
binds the exact bytes of seven files (nfl_live.definition: nfl_live, nfl_eval, coverage, markets, models/base, elo,
points). Any byte change there silently turns NFL off: no forecasts recorded, no Telegram cards, no ticket legs
(nfl_live.activation() -> None). On Nov 8 an uncommitted coverage.py edit did exactly that in the live sender/helper.
This test uses a byte copy of the production artifact; changing a sealed file requires a new reviewed activation."""
import json
from pathlib import Path

from app import nfl_live

ARTIFACT = Path(__file__).parent / "fixtures" / "nfl_activation_2026-11-02.json"


def test_the_approved_nfl_activation_stays_valid_for_this_code(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    target = nfl_live.activation_path()
    target.parent.mkdir(parents=True)
    target.write_bytes(ARTIFACT.read_bytes())
    approved = json.loads(ARTIFACT.read_text())["definition"]
    assert approved == "b6074877a7e7ccfe"
    assert nfl_live.definition() == approved, "a sealed NFL dependency changed; NFL would be inactive"
    active = nfl_live.activation()
    assert active is not None and active["definition"] == approved
