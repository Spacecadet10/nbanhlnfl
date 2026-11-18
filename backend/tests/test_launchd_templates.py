"""The launchd templates in scripts/launchd/ are what gets installed: no retired checkout path, daily agreement display on
for the two serving jobs, and the adaptive trainer on the live release with the release guard."""
import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = sorted((ROOT / 'scripts' / 'launchd').glob('com.nba-predictor.*.plist'))


def load(name):
    return plistlib.loads((ROOT / 'scripts' / 'launchd' / f'com.nba-predictor.{name}.plist').read_bytes())


def test_every_template_parses_and_names_its_label_without_the_retired_checkout():
    assert TEMPLATES
    for p in TEMPLATES:
        assert plistlib.loads(p.read_bytes())['Label'] == p.stem
        assert 'sprint1' not in p.read_text(), p.name


def test_daily_agreement_is_on_for_the_daily_and_both_api_jobs():
    for name in ('daily', 'api-delivery', 'api'):
        assert load(name)['EnvironmentVariables']['NBA_DAILY_AGREEMENT'] == '1'


def test_the_trainer_runs_on_the_live_release_with_the_release_guard():
    p = load('adaptive-train')
    env = p['EnvironmentVariables']
    assert env['NBA_DATA_DIR'] == '/Users/andry/Desktop/nbanhlnfl/data'
    assert env['NBA_RELEASE_GUARD'] == '1' and env['NBA_RELEASE_ROOT'] == '/Users/andry/nba-predictor-live'
    command = p['ProgramArguments'][-1]
    assert 'PY=/Users/andry/nba-predictor-live/backend/.venv/bin/python;' in command
    assert p['WorkingDirectory'] == '/Users/andry/nba-predictor-live/backend'
