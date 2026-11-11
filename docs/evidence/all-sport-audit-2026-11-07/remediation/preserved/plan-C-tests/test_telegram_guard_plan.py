"""Proposed regression tests (plan C1). Must FAIL on snap3."""
from app import cli, config


def test_telegram_run_is_refused_by_the_release_guard_outside_the_release_checkout(tmp_path, monkeypatch):
    import app.telegram as tg
    called = []
    monkeypatch.setattr(tg, "command", lambda args: called.append(args) or 0)
    monkeypatch.setenv("NBA_RELEASE_GUARD", "1")
    monkeypatch.setenv("NBA_RELEASE_ROOT", str(tmp_path / "not-this-checkout"))
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path / "data"))
    rc = cli.main(["telegram", "run", "--once"])
    assert called == [] and rc != 0


def test_telegram_state_lives_in_the_configured_directory_not_the_code_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_TELEGRAM_DIR", str(tmp_path))
    from app import telegram
    with telegram.ledger() as conn:
        pass
    assert (tmp_path / ".telegram-delivery.sqlite").is_file()
