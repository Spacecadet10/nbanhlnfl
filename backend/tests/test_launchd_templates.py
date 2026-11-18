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
    assert 'cd /Users/andry/nba-predictor-live/backend &&' in command   # the guard runs from delivery; training from live


def test_the_trainer_runs_below_the_forecast_jobs_priority():
    # The first scheduled run (2026-11-18) trained NHL daily for 87 min at nice 0; the forecast/odds jobs must keep priority.
    p = load('adaptive-train')
    assert p['Nice'] == 10 and p['ProcessType'] == 'Background'


def test_challenger_recorder_template_is_research_only_on_the_delivery_release():
    p = load('learning-challengers')
    env = p['EnvironmentVariables']
    assert p['StartInterval'] == 900 and p['Nice'] == 10 and p['ProcessType'] == 'Background'
    assert env['NBA_RELEASE_GUARD'] == '1' and env['NBA_RELEASE_ROOT'] == '/Users/andry/nba-predictor-delivery'
    assert p['WorkingDirectory'] == '/Users/andry/nba-predictor-delivery/backend'
    assert p['ProgramArguments'][-1].endswith('/scripts/learning_challengers_cycle.sh')
    assert 'RunAtLoad' not in p



def test_the_trainer_takes_the_shared_heavy_lock_without_changing_its_schedule():
    # Owner 2026-11-18: every heavy job (both trainer slots, deep runs, research) uses the same enforced lock.
    p = load('adaptive-train')
    args = p['ProgramArguments']
    i = args.index('app.learning_run')
    assert args[i - 2] == '/Users/andry/nba-predictor-delivery/backend/.venv/bin/python' and args[i - 1] == '-m'
    assert '--light' not in args
    assert args[args.index('--nice') + 1] == '0'   # os.nice adds to the plist's Nice 10: the trainer stays at 10
    assert p['WorkingDirectory'] == '/Users/andry/nba-predictor-delivery/backend'
    assert args[args.index('--defer-sports') + 1] == ''        # lock only: no game-window deferral, timing unchanged
    assert float(args[args.index('--max-minutes') + 1]) >= 240  # the first scheduled run took 105 min
    assert args[args.index('--', i) + 1:args.index('--', i) + 3] == ['/bin/sh', '-c']
    assert 'cd /Users/andry/nba-predictor-live/backend &&' in args[-1]
    assert 'LEARNING_HEAVY_LOCK' not in p['EnvironmentVariables']   # default: <NBA_DATA_DIR>/.learning-heavy.lock
