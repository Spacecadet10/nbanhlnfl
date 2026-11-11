"""Guarded release for the Telegram sender and the ticket helper (app/release.py delivery_check).

Both used to import whatever the development working tree held, so an uncommitted edit went live within a minute (sender)
or at the next hourly review (helper). Under NBA_RELEASE_GUARD=1 they now run only from the clean release checkout, with
the production receipt ledger and credentials named explicitly and already present: a release checkout elsewhere must
never fall back to its own empty ledger (every receipt, duplicate guard and $5 reservation would be forgotten).
Temporary git repositories, ledgers and credential files only; Telegram is never constructed or called."""
import json
import os
import subprocess
import sys

import pytest

from app import cli, config, release, telegram, tickets


def git_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README").write_text("release\n")
    subprocess.run(["git", "-C", str(path), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "r"], check=True)
    release.write_pin(path)      # a release pins its commit (app.release pin), as the release procedure does
    return path


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Production-like telegram state outside the release checkout: an existing ledger and a private credentials file."""
    main = tmp_path / "main"
    (main / "data").mkdir(parents=True)
    ledger = main / ".telegram-delivery.sqlite"
    with telegram.ledger(ledger):
        pass
    creds = main / ".env.telegram"
    creds.write_text("TELEGRAM_BOT_TOKEN=fixture-token\nTELEGRAM_CHAT_ID=fixture-chat\n")
    os.chmod(creds, 0o600)
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_PAIR_CODE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NBA_DATA_DIR", str(main / "data"))
    return main, ledger, creds


@pytest.fixture
def no_telegram(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a refused delivery command must not construct or call Telegram")
    monkeypatch.setattr(telegram, "Telegram", refuse)
    monkeypatch.setattr(telegram, "command", refuse)
    monkeypatch.setattr(tickets, "settle", refuse)
    monkeypatch.setattr(tickets, "snapshot", refuse)


def test_explicit_production_paths_are_honoured(state, monkeypatch):
    main, ledger, creds = state
    monkeypatch.setenv(config.TELEGRAM_LEDGER_ENV, str(ledger))
    monkeypatch.setenv(config.TELEGRAM_CREDENTIALS_ENV, str(creds))
    assert config.telegram_ledger_path() == ledger
    assert config.telegram_credentials_path() == creds
    assert config.telegram_settings()["TELEGRAM_CHAT_ID"] == "fixture-chat"


def test_without_explicit_paths_a_release_elsewhere_would_lose_the_production_ledger(state, tmp_path, monkeypatch):
    main, ledger, creds = state
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path / "release")          # code imported from another checkout
    assert config.telegram_ledger_path() != ledger                          # the reason the guard demands explicit paths
    monkeypatch.setenv(release.ROOT_ENV, str(tmp_path / "release"))
    problems = " | ".join(release.delivery_check(root=git_repo(tmp_path / "release"))["problems"])
    assert release.TELEGRAM_LEDGER_ENV_NAME in problems and release.TELEGRAM_CREDENTIALS_ENV_NAME in problems


def test_clean_release_with_existing_state_passes(state, tmp_path, monkeypatch):
    main, ledger, creds = state
    root = git_repo(tmp_path / "release")
    monkeypatch.setenv(release.ROOT_ENV, str(root))
    monkeypatch.setenv(config.TELEGRAM_LEDGER_ENV, str(ledger))
    monkeypatch.setenv(config.TELEGRAM_CREDENTIALS_ENV, str(creds))
    info = release.delivery_check(root=root)
    assert info["problems"] == [] and len(info["commit"]) == 40
    assert info["ledger"] == str(ledger) and info["credentials"] == str(creds)


def test_every_unsafe_delivery_state_is_refused(state, tmp_path, monkeypatch):
    main, ledger, creds = state
    root = git_repo(tmp_path / "release")
    (root / "README").write_text("edited in the release checkout\n")
    monkeypatch.setenv(release.ROOT_ENV, str(tmp_path / "elsewhere"))
    monkeypatch.setenv(config.TELEGRAM_LEDGER_ENV, str(main / "missing.sqlite"))
    link = main / "linked.env"
    link.symlink_to(creds)
    monkeypatch.setenv(config.TELEGRAM_CREDENTIALS_ENV, str(link))
    monkeypatch.delenv("NBA_DATA_DIR")
    problems = " | ".join(release.delivery_check(root=root)["problems"])
    for reason in ("not the release checkout", "uncommitted changes: README", "NBA_DATA_DIR is not set",
                   "receipt ledger", "does not exist", "credentials", "symlink"):
        assert reason in problems, reason


def test_guarded_sender_refuses_from_the_development_checkout_before_any_request(state, tmp_path, monkeypatch, capsys,
                                                                                 no_telegram):
    main, ledger, creds = state
    monkeypatch.setenv(release.GUARD_ENV, "1")
    monkeypatch.setenv(release.ROOT_ENV, str(tmp_path / "release-not-this-checkout"))
    monkeypatch.setenv(config.TELEGRAM_LEDGER_ENV, str(ledger))
    monkeypatch.setenv(config.TELEGRAM_CREDENTIALS_ENV, str(creds))
    before = ledger.read_bytes()
    assert cli.main(["telegram", "run", "--once"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "failed" and out["reason"] == "release guard"
    assert any("not the release checkout" in p for p in out["problems"])
    assert ledger.read_bytes() == before


def test_guarded_ticket_helper_refuses_from_the_development_checkout(state, tmp_path, monkeypatch, capsys, no_telegram):
    main, ledger, creds = state
    monkeypatch.setenv(release.GUARD_ENV, "1")
    monkeypatch.setenv(release.ROOT_ENV, str(tmp_path / "release-not-this-checkout"))
    monkeypatch.setenv(config.TELEGRAM_LEDGER_ENV, str(ledger))
    monkeypatch.setenv(config.TELEGRAM_CREDENTIALS_ENV, str(creds))
    for action in (["preflight", "--notify"], ["settle"], ["snapshot", "--output", str(tmp_path / "s.json")]):
        monkeypatch.setattr(sys, "argv", ["tickets", *action])
        assert tickets.main() == 1, action
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "failed" and out["reason"] == "release guard"
    assert not (tmp_path / "s.json").exists()


def test_guarded_commands_proceed_from_a_clean_release(state, tmp_path, monkeypatch, capsys):
    main, ledger, creds = state
    root = git_repo(tmp_path / "release")
    monkeypatch.setenv(release.GUARD_ENV, "1")
    monkeypatch.setenv(release.ROOT_ENV, str(root))
    monkeypatch.setattr(release, "code_root", lambda: root)
    monkeypatch.setenv(config.TELEGRAM_LEDGER_ENV, str(ledger))
    monkeypatch.setenv(config.TELEGRAM_CREDENTIALS_ENV, str(creds))
    ran = []
    monkeypatch.setattr(telegram, "command", lambda args: ran.append(args.telegram_action) or 0)
    assert cli.main(["telegram", "status"]) == 0 and ran == ["status"]
    monkeypatch.setattr(tickets, "settle", lambda **k: {"as_of": "x", "tickets": [], "errors": []})
    monkeypatch.setattr(sys, "argv", ["tickets", "settle"])
    assert tickets.main() == 0 and json.loads(capsys.readouterr().out.splitlines()[-1])["errors"] == []


def test_the_guarded_ledger_is_never_created(tmp_path, monkeypatch):
    monkeypatch.setenv(release.GUARD_ENV, "1")
    missing = tmp_path / "absent.sqlite"
    with pytest.raises(ValueError, match="does not exist"):
        with telegram.ledger(missing):
            pass
    assert not missing.exists()


def test_development_runs_are_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv(release.GUARD_ENV, raising=False)
    monkeypatch.delenv(config.TELEGRAM_LEDGER_ENV, raising=False)
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path / "copy"))
    (tmp_path / "copy").mkdir()
    assert config.telegram_ledger_path() == tmp_path / "copy" / ".telegram-delivery.sqlite"
    with telegram.ledger():
        pass
    assert (tmp_path / "copy" / ".telegram-delivery.sqlite").exists()


def test_the_shipped_sender_and_helper_configuration_is_guarded():
    """The launchd template and the helper wrapper run the release with the guard and the same explicit state paths."""
    import plistlib
    import re
    root = config.REPO_ROOT
    plist = plistlib.loads((root / "scripts/launchd/com.nba-predictor.telegram.plist").read_bytes())
    env = plist["EnvironmentVariables"]
    live = env[release.ROOT_ENV]
    assert env[release.GUARD_ENV] == "1" and plist["ProgramArguments"][0] == f"{live}/backend/.venv/bin/python"
    assert plist["WorkingDirectory"] == f"{live}/backend" and plist["ProgramArguments"][1:] == ["-m", "app.cli", "telegram", "run", "--once"]
    for name in (config.TELEGRAM_LEDGER_ENV, config.TELEGRAM_CREDENTIALS_ENV, "NBA_DATA_DIR"):
        assert env[name] and not env[name].startswith(live), name      # production state never lives in the release
    script = (root / "scripts/release_tickets.sh").read_text()
    values = dict(re.findall(r"^(LIVE|MAIN)=(\S+)$", script, re.M))
    assert values["LIVE"] == live
    expanded = script.replace('"$LIVE"', live).replace('"$MAIN', values["MAIN"]).replace('$LIVE', live)
    for name in (release.GUARD_ENV, release.ROOT_ENV, "NBA_DATA_DIR", config.TELEGRAM_LEDGER_ENV, config.TELEGRAM_CREDENTIALS_ENV):
        assert f"{name}=" in script, name
    assert f'{config.TELEGRAM_LEDGER_ENV}={env[config.TELEGRAM_LEDGER_ENV]}"' in expanded
    assert f'{config.TELEGRAM_CREDENTIALS_ENV}={env[config.TELEGRAM_CREDENTIALS_ENV]}"' in expanded
    assert f'NBA_DATA_DIR={env["NBA_DATA_DIR"]}"' in expanded and "-m app.tickets" in script


def test_an_unguarded_run_from_another_checkout_still_finds_the_main_ledger(tmp_path, monkeypatch):
    """A manual `python -m app.tickets` in a release folder (no wrapper, no guard) must not start an empty ledger inside
    the live data folder: the live data folder of a main checkout keeps its ledger beside it."""
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    (main / "data").mkdir()
    monkeypatch.delenv(config.TELEGRAM_LEDGER_ENV, raising=False)
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path / "release")
    monkeypatch.setenv("NBA_DATA_DIR", str(main / "data"))
    assert config.telegram_ledger_path() == main / ".telegram-delivery.sqlite"
    monkeypatch.setenv("NBA_DATA_DIR", str(main / "data") + "/")
    assert config.telegram_ledger_path() == main / ".telegram-delivery.sqlite"


def test_a_moved_or_missing_pin_does_not_stop_the_sender_or_helper(tmp_path, state, monkeypatch, capsys):
    # Owner, Nov 11: the pin never stops a job; it only writes a log line. The clean-checkout check stays.
    main, ledger, creds = state
    root = git_repo(tmp_path / "release")
    monkeypatch.setenv(release.ROOT_ENV, str(root))
    monkeypatch.setenv(config.TELEGRAM_LEDGER_ENV, str(ledger))
    monkeypatch.setenv(config.TELEGRAM_CREDENTIALS_ENV, str(creds))
    release.pin_path(root).write_text("0000000\n")
    info = release.delivery_check(root)
    assert info["problems"] == [] and "not the pinned commit" in info["pin_note"]
    assert "release pin (not a refusal)" in capsys.readouterr().err
    release.pin_path(root).unlink()
    assert release.delivery_check(root)["problems"] == []
    (root / "README").write_text("edited\n")
    assert any("uncommitted changes" in p for p in release.delivery_check(root)["problems"])
