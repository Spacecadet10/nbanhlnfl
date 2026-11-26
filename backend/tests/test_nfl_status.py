"""NFL's served configuration is pinned and its inactivity is visible (app/nfl_status.py, docs/nfl_activation_pin.json).
nfl_live.activation() returns None, silently, as soon as one of the seven hashed files changes; this module says why, the
release check names a drifted activation, the Telegram run records it and the delivery API shows it. Nothing here is a
refusal, nothing is sent to anyone. The approved artifact is the byte copy in tests/fixtures."""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import nfl_live, nfl_status, release, telegram as tg
from app.main import app

ARTIFACT = Path(__file__).parent / "fixtures" / "nfl_activation_2026-11-02.json"
REPO_PIN = Path(__file__).resolve().parents[2] / "docs" / "nfl_activation_pin.json"


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    return tmp_path


def put(raw: bytes):
    target = nfl_live.activation_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)


def changed(**edits) -> bytes:
    a = json.loads(ARTIFACT.read_text())
    for k, v in edits.items():
        if v is None:
            a.pop(k)
        else:
            a[k] = v
    return json.dumps(a, indent=1).encode()


def test_the_committed_pin_is_the_approved_activation_taken_from_the_real_file():
    pin = json.loads(REPO_PIN.read_text())
    raw = ARTIFACT.read_bytes()
    a = json.loads(raw)
    assert pin["activation_sha256"] == hashlib.sha256(raw).hexdigest() and pin["bytes"] == len(raw)
    assert pin["definition"] == a["definition"] == "b6074877a7e7ccfe" and pin["activated_at"] == a["activated_at"]
    assert pin["sealed_sha256"] == a["sealed"]["sha256"]


def test_the_approved_activation_reports_active_and_matches_the_pin(data):
    put(ARTIFACT.read_bytes())
    assert nfl_status.inactive_reason() is None
    assert nfl_status.pin_problems() == []
    st = nfl_status.status()
    assert st["status"] == "active" and st["definition"] == st["activation_definition"] == "b6074877a7e7ccfe"
    assert st["pin"] == "matches" and st["unexpected"] is False and st["reason"] is None


def test_every_way_activation_returns_none_has_its_own_precise_reason(data, monkeypatch):
    assert "no activation file" in nfl_status.inactive_reason()                           # not activated at all / missing file
    put(b"{not json")
    assert "unreadable" in nfl_status.inactive_reason()
    put(b"[]")
    assert "unreadable" in nfl_status.inactive_reason()
    put(changed(sealed=None))
    assert "unreadable" in nfl_status.inactive_reason() and "sealed" in nfl_status.inactive_reason()
    put(changed(activated_at=""))
    assert "not activated" in nfl_status.inactive_reason()
    sealed = json.loads(ARTIFACT.read_text())["sealed"]
    put(changed(sealed={**sealed, "chosen_at": "2026-01-01T00:00:00Z"}))                   # edited after it was sealed
    assert "seal" in nfl_status.inactive_reason()
    put(changed(definition="0123456789abcdef"))
    reason = nfl_status.inactive_reason()
    assert "0123456789abcdef" in reason and nfl_live.definition() in reason and "definition" in reason
    put(ARTIFACT.read_bytes())
    monkeypatch.setattr(nfl_live, "definition", lambda: "ffffffffffffffff")                # a sealed source file changed
    assert "b6074877a7e7ccfe" in nfl_status.inactive_reason() and "ffffffffffffffff" in nfl_status.inactive_reason()


def test_status_agrees_with_activation_in_every_case(data, monkeypatch):
    cases = [None, b"{x", changed(activated_at=""), changed(definition="0" * 16), ARTIFACT.read_bytes()]
    for raw in cases:
        if raw is not None:
            put(raw)
        assert (nfl_status.inactive_reason() is None) == (nfl_live.activation() is not None)


def test_inactive_while_the_pin_says_it_should_exist_is_unexpected(data):
    st = nfl_status.status()                                    # no activation file at all
    assert st["status"] == "inactive" and st["unexpected"] is True and "no activation file" in st["reason"]
    assert nfl_status.status(pin_path=data / "no-pin.json")["unexpected"] is False        # nothing pinned: expected


def test_pin_problems_name_the_difference(data, monkeypatch):
    put(ARTIFACT.read_bytes() + b"\n")                           # same JSON, other bytes
    (problem,) = nfl_status.pin_problems()
    assert hashlib.sha256(ARTIFACT.read_bytes() + b"\n").hexdigest() in problem
    assert json.loads(REPO_PIN.read_text())["activation_sha256"] in problem
    put(changed(definition="0123456789abcdef"))
    assert any("0123456789abcdef" in p and nfl_live.definition() in p for p in nfl_status.pin_problems())
    put(ARTIFACT.read_bytes())
    monkeypatch.setattr(nfl_live, "definition", lambda: "ffffffffffffffff")
    (problem,) = nfl_status.pin_problems()
    assert "ffffffffffffffff" in problem and "b6074877a7e7ccfe" in problem
    monkeypatch.undo()
    (data / "research" / "nfl_live" / "activation.json").unlink()
    (problem,) = nfl_status.pin_problems()
    assert "missing" in problem
    assert "no pin file" in nfl_status.pin_problems(pin_path=data / "gone.json")[0]


# --- release-check -------------------------------------------------------------------------------------------------------
def git_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README").write_text("release\n")
    subprocess.run(["git", "-C", str(path), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "r"], check=True)
    release.write_pin(path)
    return path


def test_release_check_reports_the_pin_for_nfl_only_and_never_adds_a_refusal(tmp_path, data, monkeypatch, capsys):
    root = git_repo(tmp_path / "release")
    monkeypatch.setenv(release.ROOT_ENV, str(root))
    monkeypatch.setattr(release, "_PIN_LOGGED", set())
    base = release.check("nfl", root=root)
    assert base["nfl_pin_problems"] and "missing" in base["nfl_pin_problems"][0]           # no activation in this data folder
    assert not any("activation" in p for p in base["problems"])        # the refusal list the NFL jobs act on is unchanged
    assert "NFL activation pin (not a refusal)" in capsys.readouterr().err               # a log line, once
    put(ARTIFACT.read_bytes())
    assert release.check("nfl", root=root)["nfl_pin_problems"] == []
    for sport in ("nba", "nhl", "ncaab"):
        assert "nfl_pin_problems" not in release.check(sport, root=root)


def test_a_failing_pin_check_cannot_break_or_block_release_check(tmp_path, data, monkeypatch):
    root = git_repo(tmp_path / "release")
    monkeypatch.setenv(release.ROOT_ENV, str(root))

    def boom(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(nfl_status, "pin_problems", boom)
    info = release.check("nfl", root=root)
    assert not any("RuntimeError" in p or "activation" in p for p in info["problems"])
    assert "RuntimeError" in info["nfl_pin_problems"][0]


def test_nfl_release_check_command_lists_pin_problems_as_problems_and_exits_1(data, capsys):
    from app import cli
    put(changed(definition="0123456789abcdef"))
    rc = cli.main(["nfl", "release-check"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and any("0123456789abcdef" in p for p in out["problems"]) and out["nfl_pin_problems"]
    put(ARTIFACT.read_bytes())
    cli.main(["nfl", "release-check"])
    assert json.loads(capsys.readouterr().out)["nfl_pin_problems"] == []


# --- Telegram run and API ------------------------------------------------------------------------------------------------
class Bot:
    def __init__(self):
        self.sends = []

    def call(self, method, body, **kw):
        if method == "getUpdates":
            return []
        self.sends.append(body)
        return {"message_id": 1}


def run_once(tmp_path, monkeypatch):
    from app import db, freshness
    monkeypatch.setenv("NBA_NCAAB_CARDS", "0")

    class Source:
        def __init__(self, sport): self.sport = sport
        def execute(self, sql): pass
        def close(self): pass
    monkeypatch.setattr(db, "connect_readonly", Source)
    monkeypatch.setattr(db, "store_sport", lambda s: s.sport)
    monkeypatch.setattr(freshness, "summary", lambda *_: {"source_state": "stale", "failing": False})
    bot = Bot()
    with tg.ledger(tmp_path / "receipts.sqlite") as conn:
        result = tg.run_once(bot, {"TELEGRAM_CHAT_ID": "123"}, conn, now=datetime(2026, 11, 2, 20, tzinfo=timezone.utc))
    return result, bot


def test_the_telegram_run_records_why_nfl_is_inactive_instead_of_skipping_silently(data, tmp_path, monkeypatch):
    result, bot = run_once(tmp_path, monkeypatch)
    nfl = result["sports"]["nfl"]
    assert nfl["status"] == "inactive" and "no activation file" in nfl["reason"] and nfl["unexpected"] is True
    assert not bot.sends and set(result["sports"]) == {"nba", "nhl", "nfl", "ncaab"}
    assert result["sports"]["nba"] == {"status": "source_not_fresh"}                       # the other sports are unchanged


def test_the_telegram_run_does_not_mention_nfl_as_inactive_when_it_is_active(data, tmp_path, monkeypatch):
    put(ARTIFACT.read_bytes())
    result, _ = run_once(tmp_path, monkeypatch)
    assert result["sports"]["nfl"] != {} and result["sports"]["nfl"].get("status") != "inactive"


def test_the_api_shows_the_nfl_activation_state_read_only(data):
    client = TestClient(app)
    r = client.get("/api/nfl/activation")
    assert r.status_code == 200 and r.json()["status"] == "inactive" and r.json()["unexpected"] is True
    put(ARTIFACT.read_bytes())
    body = client.get("/api/nfl/activation").json()
    assert body["status"] == "active" and body["pin"] == "matches" and body["definition"] == "b6074877a7e7ccfe"
    assert client.post("/api/nfl/activation").status_code == 405
