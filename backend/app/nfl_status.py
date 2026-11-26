"""Why NFL is, or is not, being served: the activation (data/research/nfl_live/activation.json) against a committed pin.

nfl_live.activation() returns None, silently, when the activation file is missing or unreadable, its seal or report no
longer match, `nfl_live.definition()` (the bytes of seven source files) differs from the activation's, or it holds no
activation time; the Telegram run then used to skip NFL without a trace. This module only READS: it explains that None
(`inactive_reason`), compares the file with `docs/nfl_activation_pin.json` (`pin_problems`, used by `release.check("nfl")`)
and builds the status the Telegram run and `/api/nfl/activation` show (`status`). It refuses nothing, sends nothing,
and none of the seven hashed files is touched.

The pin lives in docs/ because app/ is hashed as a whole by autoresearch.app_identity (every .py/.json/.sql/.csv below
app/), so a pin file in app/reference/ would be part of that identity. Re-pin only deliberately, after a reviewed
`nfl activate`:  python -m app.nfl_status pin     (prints the current status without arguments).
"""
import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

from . import nfl_eval, nfl_live

PIN_PATH = Path(__file__).resolve().parents[2] / "docs" / "nfl_activation_pin.json"
FILE = "research/nfl_live/activation.json"      # in the data folder; named without its absolute path so a run's result is the same anywhere


def load_pin(pin_path: Optional[Path] = None) -> Optional[dict]:
    """The pinned facts, or None when there is no readable pin (then nothing says an activation should exist)."""
    try:
        pin = json.loads(Path(pin_path or PIN_PATH).read_text())
        return pin if isinstance(pin, dict) and pin.get("activation_sha256") else None
    except (OSError, ValueError):
        return None


def inactive_reason() -> Optional[str]:
    """None when nfl_live.activation() is valid; otherwise exactly which of its checks fails (same checks, same order)."""
    path = nfl_live.activation_path()
    if not path.is_file():
        return f"no activation file ({FILE}): NFL has not been activated in this data folder (or the file was removed)"
    try:
        a = json.loads(path.read_text())
        s, r = a["sealed"], a["test_report"]
        choice, threshold = r["choice_sha256"], r["winner"]["frozen_threshold"]["threshold"]
        best = s["line_markets"]["markets"]["winner"]["best_threshold"]
        recorded, activated_at = a["definition"], a.get("activated_at")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return f"the activation file ({FILE}) is unreadable or incomplete ({type(exc).__name__}: {exc})"
    if not nfl_eval._valid_seal(s):
        return "seal invalid: the sealed choice no longer matches its own sha256"
    if choice != s["sha256"]:
        return f"seal invalid: the one-shot test report ({str(choice)[:12]}) is for another choice than the seal ({s['sha256'][:12]})"
    if threshold != best:
        return f"seal invalid: the frozen threshold {threshold} differs from the sealed best threshold {best}"
    current = nfl_live.definition()
    if recorded != current:
        return (f"definition mismatch: the activation was made for definition {recorded} but this code's NFL definition is "
                f"{current} (one of nfl_live.py, nfl_eval.py, coverage.py, markets.py, models/base.py, models/elo.py, "
                "models/points.py differs)")
    if not activated_at:
        return "not activated: the activation file has no activated_at"
    return None


def pin_problems(pin_path: Optional[Path] = None) -> list:
    """Every difference between the pin, the activation file and this checkout's NFL definition (empty: all agree)."""
    pin = load_pin(pin_path)
    if pin is None:
        return [f"no pin file or an unusable one at {pin_path or PIN_PATH}: the NFL activation is not pinned"]
    path = nfl_live.activation_path()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [f"the NFL activation file ({FILE}) is missing or unreadable ({type(exc).__name__}) though the pin says it was "
                f"activated {pin.get('activated_at')} with definition {pin.get('definition')}"]
    problems = []
    digest = hashlib.sha256(raw).hexdigest()
    if digest != pin["activation_sha256"]:
        problems.append(f"the NFL activation file sha256 {digest} differs from the pin {pin['activation_sha256']}")
    try:
        recorded = json.loads(raw)["definition"]
    except (ValueError, KeyError, TypeError):
        recorded = None
    current = nfl_live.definition()
    if recorded != current:
        problems.append(f"this checkout's NFL definition {current} differs from the activation's {recorded} "
                        f"(the pin: {pin.get('definition')})")
    return problems


def status(pin_path: Optional[Path] = None) -> dict:
    """The NFL state for logs, the Telegram run and the API. `unexpected`: inactive while the pin says an activation exists."""
    reason, pin = inactive_reason(), load_pin(pin_path)
    problems = pin_problems(pin_path) if pin is not None else []
    try:
        recorded = json.loads(nfl_live.activation_path().read_text()).get("definition")
    except (OSError, ValueError, AttributeError):
        recorded = None
    return {"status": "active" if reason is None else "inactive", "reason": reason, "unexpected": reason is not None and pin is not None,
            "pin": "no pin" if pin is None else "differs" if problems else "matches", "pin_problems": problems,
            "definition": nfl_live.definition(), "activation_definition": recorded,
            "pinned_definition": (pin or {}).get("definition"), "activated_at": (pin or {}).get("activated_at")}


def take_pin(pin_path: Optional[Path] = None) -> dict:
    """Writes the pin from the activation file now in the data folder (read only), if that activation is valid."""
    reason = inactive_reason()
    if reason:
        raise ValueError(f"not pinned: {reason}")
    raw = nfl_live.activation_path().read_bytes()
    a = json.loads(raw)
    pin = {"activation_sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "definition": a["definition"],
           "activated_at": a["activated_at"], "sealed_sha256": a["sealed"]["sha256"]}
    Path(pin_path or PIN_PATH).write_text(json.dumps(pin, indent=1, sort_keys=True) + "\n")
    return pin


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["pin"]):
        raise SystemExit("usage: python -m app.nfl_status [pin]")
    print(json.dumps(take_pin() if sys.argv[1:] else status(), indent=1))
