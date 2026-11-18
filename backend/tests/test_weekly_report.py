"""Step H weekly page (scripts/weekly_report.py): read-only, no interim look at the preregistered best-pick test (v4 section
9), a sample size on every line, "too few to tell" below the scorecard report's threshold."""
import hashlib
import json
import importlib.util
import plistlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import best_pick_record as bpr, telegram as tg
from test_best_pick_record import REVIEWED, cards_for

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 12, 31, tzinfo=timezone.utc)
SHA = hashlib.sha256(Path(bpr.__file__).read_bytes()).hexdigest()


def wr():
    spec = importlib.util.spec_from_file_location('weekly_report', ROOT / 'scripts' / 'weekly_report.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


COMMIT = 'ab' * 20


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('NBA_RELEASE_COMMIT', COMMIT)           # the code commit every recorded row carries


H_OF = {0: {'H1': 1, 'H2': 1, 'H3': 0, 'H4': 0}, 1: {'H1': -1, 'H2': 1, 'H3': -1, 'H4': 0}}


def write_ledger(path, cards):
    """Recorder rows (bpr.record itself) whose pictures carry the H items, plus the matching sent deliveries."""
    with tg.ledger(path) as conn:
        for key, gid, at, tip, nf, bf, q, qp in cards:
            picks = [dict(forecast_id=f, market='winner', pick_label=f'P{f}', candidate=True, score=2 if f == nf else 0,
                          book_pct='70.0', book_raw=q, app_pct='60.0', quote={'power': qp}, h=H_OF[f % 2],
                          lineup_flags=[], excluded=[]) for f in sorted({nf, bf} - {None})]
            pic = dict(sport='nba', game_id=gid, matchup=f'A @ {gid}', tipoff_at=tip, picks=picks, named_forecast_id=nf,
                       baseline_forecast_id=bf, excluded_lineup=0, excluded_avoid=0,
                       played_previous_day={'home': False, 'away': True})
            bpr.record(conn, key, 'nba', gid, pic, at)
            conn.execute("INSERT INTO deliveries VALUES (?,?,?,?,?,NULL,1,NULL)",
                         (key, '1', f'<b>PROP51 PICKS · NBA</b>\n<b>A @ {gid}</b>\nStarts {tip}\n<b>Recommended pick</b>\nX win',
                          'sent', at))


def write_store(path, results):
    """results: {fid: (gid, tipoff, home_won or None, seen_at)} -> a file store the grader reads."""
    c = sqlite3.connect(path)
    c.executescript('''CREATE TABLE games (game_id TEXT, status TEXT, tipoff_at TEXT, home_score INT, away_score INT,
                         result_first_seen_at TEXT, result_available_at TEXT);
                       CREATE TABLE game_forecasts (forecast_id INTEGER, game_id TEXT, market TEXT, line REAL, p_a REAL,
                         p_mid REAL, p_b REAL, tipoff_at TEXT);''')
    for fid, (gid, tip, won, seen) in results.items():
        if not c.execute('SELECT 1 FROM games WHERE game_id=?', (gid,)).fetchone():
            c.execute('INSERT INTO games VALUES (?,?,?,?,?,?,NULL)', (gid, 'scheduled' if won is None else 'final', tip,
                      None if won is None else (100 if won else 90), None if won is None else 95, seen))
        c.execute("INSERT INTO game_forecasts VALUES (?,?,'winner',NULL,.6,0,.4,?)", (fid, gid, tip))
    c.commit()
    c.close()


def sc_row(fid, pct, grade, tip, sport='nba', market='winner', side='a', game='other'):
    return dict(forecast_id=fid, game_id=game, sport=sport, market=market, displayed_pct=pct, side=side, source='s',
                model_version='m',
                line_source='none', tipoff_at=tip, game_date=tip[:10], matchup='X @ Y', pick_label='Y', grade=grade,
                book_devig=.6, confidence=float(pct) / 100)


def setup(tmp_path, n_cards, monkeypatch, m, repeats=()):
    data = tmp_path / 'data'
    data.mkdir()
    cards, res = cards_for(n_cards)
    write_ledger(tmp_path / 'ledger.sqlite', cards)
    write_store(data / 'nba.sqlite', res)
    (data / 'scorecard').mkdir()
    (data / 'scorecard' / 'nba.sqlite').write_bytes(b'')            # present; its reader is replaced below
    monkeypatch.setattr(m.sr, 'graded_rows', lambda d, sport, as_of: ([r for r in repeats if r['sport'] == sport], {}))
    return data, cards, res


def page(m, tmp_path, data, **kw):
    out = tmp_path / 'out'
    path = m.main(['--data-dir', str(data), '--ledger', str(tmp_path / 'ledger.sqlite'), '--out-dir', str(out),
                   '--reviewed-at', REVIEWED, '--reviewed-recorder', SHA, '--reviewed-commit', COMMIT, '--now', '2026-12-31T00:00:00Z', *kw.get('extra', ())])
    return Path(path).read_text()


def section(text, number):
    return text.split(f'## {number}.')[1].split('\n## ')[0]


# ---- gating: no interim look -------------------------------------------------------------------------------------------
def test_before_completion_named_picks_hypotheses_and_lost_cards_show_counts_only(tmp_path, monkeypatch):
    m = wr()
    named_fid = 2                                          # card g1 names forecast 2 (cards_for: 2i), after T0
    # Real shape: the scorecard's canonical pick is the LATEST forecast before the start, often regenerated after the card
    # was sent, so its forecast id differs from the recorder's named/baseline id for the same game and market (review HIGH).
    repeats = [sc_row(7001, '60.0', 'win', '2026-11-16T04:00:00Z', game='g1'),                     # canonical winner of g1
               sc_row(7002, '62.0', 'loss', '2026-11-16T04:00:00Z', game='g1', market='total'),   # other market, same game
               sc_row(7003, '62.0', 'win', '2026-11-16T04:00:00Z', game='g2', market='team_total_home'),
               sc_row(9001, '60.0', 'win', '2026-12-28T00:00:00Z'), sc_row(9002, '60.0', 'loss', '2026-12-29T00:00:00Z'),
               sc_row(9003, '62.0', 'win', '2026-12-28T00:00:00Z', market='total'),
               sc_row(9004, '62.0', 'loss', '2026-12-29T00:00:00Z', market='total')]
    assert named_fid not in {r['forecast_id'] for r in repeats}
    data, _, _ = setup(tmp_path, 99, monkeypatch, m, repeats)
    m.bp.poisson_binomial_sf = m.bp.permutation_p = lambda *a, **k: pytest.fail('no test before completion')
    text = page(m, tmp_path, data)
    s1, s2, s3 = section(text, 1), section(text, 2), section(text, 3)
    assert 'named n=99' in s1 and 'decided n=99' in s1 and 'hidden until' in s1
    for s in (s1, s2, s3):
        assert not re.search(r'\d+ won|\d+W|hit rate \d', s), s
    assert 'H1' in s2 and 'hidden until' in s2 and 'v4' in s2          # the reason is on the page
    assert 'hidden until' in s3
    s4 = section(text, 4)
    assert re.search(r'held back[^\n]*games n=99\b[^\n]*picks n=3\b', s4), s4   # every market of g1 and g2
    winner = next(line for line in s4.splitlines() if 'winner 60.0%' in line)
    total = next(line for line in s4.splitlines() if 'total 62.0%' in line)
    assert '1 won, 1 lost (n=2)' in winner and '1 won, 1 lost (n=2)' in total     # only games with no recorded card


def test_after_completion_the_page_shows_wins(tmp_path, monkeypatch):
    m = wr()
    data, _, _ = setup(tmp_path, 103, monkeypatch, m)
    text = page(m, tmp_path, data)
    s1 = section(text, 1)
    assert '75 won, 25 lost (n=100)' in s1
    s2 = section(text, 2)
    assert re.search(r'H1[^\n]*\d+ won', s2) and 'hidden until' not in s2
    s3 = section(text, 3)
    assert 'named pick lost' in s3 and re.search(r'lost[^\n]*n=26', s3)


def test_unreadable_ledger_fails_closed_holding_back_every_new_rule_market_pick(tmp_path, monkeypatch):
    m = wr()
    repeats = [sc_row(1, '60.0', 'win', '2026-11-16T04:00:00Z'), sc_row(2, '60.0', 'loss', '2026-11-17T04:00:00Z'),
               sc_row(5, '61.0', 'win', '2026-12-28T00:00:00Z', market='team_total_home'),
               sc_row(6, '61.0', 'loss', '2026-12-29T00:00:00Z', market='team_total_home')]
    data, _, _ = setup(tmp_path, 3, monkeypatch, m, repeats)
    out = tmp_path / 'out'
    text = Path(m.main(['--data-dir', str(data), '--ledger', str(tmp_path / 'missing.sqlite'), '--out-dir', str(out),
                        '--now', '2026-12-31T00:00:00Z'])).read_text()
    s4 = section(text, 4)
    assert re.search(r'held back[^\n]*picks n=4\b', s4) and '60.0%' not in s4 and '61.0%' not in s4   # every market
    assert 'not readable' in section(text, 1)


# ---- n on every line, too few to tell -----------------------------------------------------------------------------------
def test_every_data_line_carries_n_and_small_samples_say_too_few_to_tell(tmp_path, monkeypatch):
    m = wr()
    many = [sc_row(5000 + i, '55.5', 'win' if i % 3 else 'loss', f'2026-12-{1 + i % 28:02d}T00:00:00Z') for i in range(40)]
    few = [sc_row(6000, '70.1', 'win', '2026-12-27T00:00:00Z'), sc_row(6001, '70.1', 'loss', '2026-12-28T00:00:00Z')]
    data, _, _ = setup(tmp_path, 103, monkeypatch, m, many + few)
    text = page(m, tmp_path, data)
    body = text.split('## 1.')[1]
    bullets = [line for line in body.splitlines() if line.startswith('- ')]
    assert bullets and all('n=' in line for line in bullets), [line for line in bullets if 'n=' not in line]
    s4 = section(text, 4)
    line_few = next(line for line in s4.splitlines() if '70.1%' in line)
    line_many = next(line for line in s4.splitlines() if '55.5%' in line)
    assert 'too few to tell' in line_few and '(n=2)' in line_few
    assert 'too few to tell' not in line_many and '65.0%' in line_many and '(n=40)' in line_many
    assert 'too few to tell' in section(text, 2)                       # H groups of ~50 split below 30 per side
    assert m.MIN_N == m.sr.MIN_N == 30


# ---- read-only --------------------------------------------------------------------------------------------------------
def test_reads_only_never_writes_stores_ledger_or_scorecard(tmp_path, monkeypatch):
    m = wr()
    data, _, _ = setup(tmp_path, 103, monkeypatch, m)
    files = [p for p in tmp_path.rglob('*') if p.is_file()]
    before = {p: p.read_bytes() for p in files}
    opened = []
    real = sqlite3.connect

    def spy(target, *a, **k):
        opened.append((str(target), k.get('uri')))
        return real(target, *a, **k)
    monkeypatch.setattr(sqlite3, 'connect', spy)
    text = page(m, tmp_path, data)
    assert opened and all('mode=ro' in t and uri for t, uri in opened), opened
    after = {p for p in tmp_path.rglob('*') if p.is_file()}
    assert {p: p.read_bytes() for p in files} == before
    assert after - set(files) == {tmp_path / 'out' / 'weekly-2026-12-31.md'}
    assert text.startswith('# ')


def test_refuses_an_output_folder_inside_live_data_other_than_reports(tmp_path):
    m = wr()
    repo = tmp_path / 'repo'
    (repo / '.git').mkdir(parents=True)
    live = repo / 'data'
    live.mkdir()
    for bad in (live, live / 'scorecard', live / 'reports-x'):
        with pytest.raises(SystemExit):
            m.check_out_dir(bad)
    assert m.check_out_dir(live / 'reports') == (live / 'reports').resolve()
    assert m.check_out_dir(tmp_path / 'scratch') == (tmp_path / 'scratch').resolve()


def test_live_data_needs_the_release_guard(tmp_path, monkeypatch):
    m = wr()
    repo = tmp_path / 'repo'
    (repo / '.git').mkdir(parents=True)
    (repo / 'data').mkdir()
    monkeypatch.delenv('NBA_RELEASE_GUARD', raising=False)
    with pytest.raises(SystemExit, match='release guard'):
        m.main(['--data-dir', str(repo / 'data'), '--ledger', str(tmp_path / 'l.sqlite'), '--now', '2026-12-31T00:00:00Z'])
    assert not (repo / 'data' / 'reports').exists()


def test_launch_agent_is_weekly_guarded_and_points_at_the_release_checkout():
    p = plistlib.loads((ROOT / 'scripts' / 'launchd' / 'com.nba-predictor.weekly-report.plist').read_bytes())
    env = p['EnvironmentVariables']
    assert env['NBA_RELEASE_GUARD'] == '1' and env['NBA_RELEASE_ROOT'] == '/Users/andry/nba-predictor-delivery'
    assert p['StartCalendarInterval'] == {'Weekday': 1, 'Hour': 9, 'Minute': 0} and p['RunAtLoad'] is False
    assert p['ProgramArguments'][1] == '/Users/andry/nba-predictor-delivery/scripts/weekly_report.py'
    assert '--ledger' in p['ProgramArguments'] and 'StartInterval' not in p
    args = p['ProgramArguments']
    assert args[args.index('--review-config') + 1] == '/Users/andry/Desktop/nbanhlnfl/data/reports/weekly_report_review.json'
    raw = (ROOT / 'scripts' / 'launchd' / 'com.nba-predictor.weekly-report.plist').read_text()
    assert '023a26a4' not in raw and '23:52' not in raw                      # the values live in the config only


def test_review_time_and_recorder_come_from_the_config_unless_given(tmp_path, monkeypatch):
    m = wr()
    at, shas = m.review_defaults()
    assert at == '2026-11-12T00:20:00Z'
    assert 'ff2d53ab3985fae70e9ee8214bf43ffe32f7d3c096bd697202f076f8a7c8de3e' in shas
    # tripwire: a recorder change without a new review entry fails here
    assert bpr.RECORDER_SHA256 in shas
    assert {e['code_commit'] for e in shas[bpr.RECORDER_SHA256]} == {'61376d9b44e21bd48db36df0d11b2a9c8112eb5f',
                                                                   '30d9005202f5eda741f2d577928a51e8b0a863f6',
                                                                   '4dd665ddf61a0e9fe8b0be07221cb1b30a39ba10',
                                                                   '7aed6bccf41173dfff42ba9e0800bcd63e14e8aa'}
    data, _, _ = setup(tmp_path, 3, monkeypatch, m)
    cfg = tmp_path / 'review.json'
    cfg.write_text(json.dumps({"reviews": [{"recorder_sha256": SHA, "code_commit": COMMIT, "reviewed_at": REVIEWED}]}))
    args = ['--data-dir', str(data), '--ledger', str(tmp_path / 'ledger.sqlite'), '--out-dir', str(tmp_path / 'o'),
            '--now', '2026-12-31T00:00:00Z']
    text = Path(m.main(args + ['--review-config', str(cfg)])).read_text()
    assert 'counted n=3' in section(text, 1)
    text = Path(m.main(args + ['--review-config', str(tmp_path / 'none.json')])).read_text()
    assert 'counted n=0' in section(text, 1) and 'review config unavailable' in text


def test_a_named_pick_carried_by_a_pre_t0_earliest_card_never_counts(tmp_path, monkeypatch):
    m = wr()
    data, cards, _ = setup(tmp_path, 3, monkeypatch, m)
    with sqlite3.connect(tmp_path / 'ledger.sqlite') as led:                    # an earlier card of game g0, before T0
        led.execute("INSERT INTO deliveries VALUES ('pre','1',?,'sent','2026-11-11T21:00:00Z',NULL,1,NULL)",
                    (f'<b>PROP51 PICKS · NBA</b>\n<b>A @ g0</b>\nStarts {cards[0][3]}\n<b>Recommended pick</b>\nX win',))
    text = page(m, tmp_path, data)
    assert 'counted n=2' in section(text, 1)


def test_repeats_are_capped_per_market_so_winner_lines_are_not_crowded_out(tmp_path, monkeypatch):
    m = wr()
    rows = [sc_row(10000 + 10 * i + j, f'50.{i}', 'win', '2026-12-28T00:00:00Z', market='team_total_home')
            for i in range(9) for j in range(5)]
    rows += [sc_row(20000 + j, '64.4', 'win', '2026-12-29T00:00:00Z') for j in range(2)]
    data, _, _ = setup(tmp_path, 103, monkeypatch, m, rows)
    s4 = section(page(m, tmp_path, data), 4)
    assert 'winner 64.4%' in s4
    assert sum('team_total_home' in line and '%' in line for line in s4.splitlines()) == m.MAX_REPEAT_LINES
    assert re.search(r'team_total_home[^\n]*not listed[^\n]*n=%d' % (9 - m.MAX_REPEAT_LINES), s4)


def test_launch_agent_logs_into_the_reports_folder():
    p = plistlib.loads((ROOT / 'scripts' / 'launchd' / 'com.nba-predictor.weekly-report.plist').read_bytes())
    assert p['StandardOutPath'] == p['StandardErrorPath'] == '/Users/andry/Desktop/nbanhlnfl/data/reports/weekly-report.log'


def test_an_unreviewed_release_commit_is_excluded_with_a_visible_explanation(tmp_path, monkeypatch):
    """Fail closed and say so: rows of a code commit with no review entry never count, and the page names the commit and the
    file to append to, instead of showing silent zeros."""
    m = wr()
    new_commit = 'abcdef0123456789abcdef0123456789abcdef01'
    monkeypatch.setenv('NBA_RELEASE_COMMIT', new_commit)
    data, _, _ = setup(tmp_path, 3, monkeypatch, m)
    cfg = tmp_path / 'review.json'
    cfg.write_text(json.dumps({"reviews": [{"recorder_sha256": SHA, "code_commit": '61376d9b44e21bd48db36df0d11b2a9c8112eb5f',
                                           "reviewed_at": REVIEWED}]}))
    text = Path(m.main(['--data-dir', str(data), '--ledger', str(tmp_path / 'ledger.sqlite'), '--out-dir', str(tmp_path / 'o'),
                        '--review-config', str(cfg), '--now', '2026-12-31T00:00:00Z'])).read_text()
    assert 'counted n=0' in section(text, 1)
    assert 'code revision abcdef01 not reviewed' in section(text, 1)
    assert f'append a review entry to {cfg}' in section(text, 1)
    assert 'NBA: 3 games excluded' in section(text, 1)


def test_rows_from_the_deliberately_unreviewed_commit_8d59134_are_excluded_with_the_visible_reason(tmp_path, monkeypatch):
    m = wr()
    monkeypatch.setenv('NBA_RELEASE_COMMIT', '8d59134caebdeb7d5fdf821dfec8da059db9c3db')
    data, _, _ = setup(tmp_path, 3, monkeypatch, m)
    args = ['--data-dir', str(data), '--ledger', str(tmp_path / 'ledger.sqlite'), '--out-dir', str(tmp_path / 'o'),
            '--review-config', str(ROOT / 'scripts' / 'weekly_report_review.json'), '--now', '2026-12-31T00:00:00Z']
    text = Path(m.main(args)).read_text()
    assert 'counted n=0' in section(text, 1)
    assert 'code revision 8d59134c not reviewed' in section(text, 1)


@pytest.mark.parametrize("content", [None, "{not json"])
def test_a_missing_or_unreadable_review_config_fails_closed_and_says_so(tmp_path, monkeypatch, content):
    m = wr()
    data, _, _ = setup(tmp_path, 3, monkeypatch, m)
    cfg = tmp_path / 'operator_review.json'
    if content is not None:
        cfg.write_text(content)
    text = Path(m.main(['--data-dir', str(data), '--ledger', str(tmp_path / 'ledger.sqlite'), '--out-dir', str(tmp_path / 'o'),
                        '--review-config', str(cfg), '--now', '2026-12-31T00:00:00Z'])).read_text()
    assert 'counted n=0' in section(text, 1)
    assert f'review config unavailable: {cfg}' in text


GOOD = {"recorder_sha256": SHA, "code_commit": COMMIT, "reviewed_at": REVIEWED}


def _page_with_entry(tmp_path, monkeypatch, entry):
    m = wr()
    data, _, _ = setup(tmp_path, 3, monkeypatch, m)
    cfg = tmp_path / 'review.json'
    cfg.write_text(json.dumps({"reviews": [entry]}))
    return Path(m.main(['--data-dir', str(data), '--ledger', str(tmp_path / 'ledger.sqlite'), '--out-dir', str(tmp_path / 'o'),
                        '--review-config', str(cfg), '--now', '2026-12-31T00:00:00Z'])).read_text(), cfg


def test_a_well_formed_entry_counts(tmp_path, monkeypatch):
    text, cfg = _page_with_entry(tmp_path, monkeypatch, GOOD)
    assert 'counted n=3' in section(text, 1) and f'review times per entry in {cfg}' in text


@pytest.mark.parametrize("mutate, reason", [
    (lambda e: e.pop("code_commit"), "code_commit"),
    (lambda e: e.update(code_commit=""), "code_commit"),
    (lambda e: e.update(code_commit=None), "code_commit"),                    # null is for the legacy recorder only
    (lambda e: e.update(code_commit="61376d9b"), "code_commit"),
    (lambda e: e.update(code_commit="A" * 40), "code_commit"),
    (lambda e: e.update(reviewed_at="2026-11-18 03:00:00"), "reviewed_at"),
    (lambda e: e.pop("reviewed_at"), "reviewed_at"),
    (lambda e: e.update(recorder_sha256="abc"), "recorder_sha256"),
])
def test_an_invalid_review_entry_fails_closed_with_a_visible_line_not_a_crash(tmp_path, monkeypatch, mutate, reason):
    entry = dict(GOOD)
    mutate(entry)
    text, _ = _page_with_entry(tmp_path, monkeypatch, entry)
    assert 'counted n=0' in section(text, 1)
    assert re.search(rf'invalid review entry #0: .*{reason}', text), text


def test_the_command_line_override_needs_the_exact_code_commit(tmp_path, monkeypatch):
    m = wr()
    data, _, _ = setup(tmp_path, 3, monkeypatch, m)
    base = ['--data-dir', str(data), '--ledger', str(tmp_path / 'ledger.sqlite'), '--out-dir', str(tmp_path / 'o'),
            '--reviewed-at', REVIEWED, '--reviewed-recorder', SHA, '--now', '2026-12-31T00:00:00Z']
    with pytest.raises(SystemExit):
        m.main(base)
    with pytest.raises(SystemExit):
        m.main(base + ['--reviewed-commit', 'abc'])
    assert 'counted n=3' in section(Path(m.main(base + ['--reviewed-commit', COMMIT])).read_text(), 1)


def test_page_wording_names_games_and_soccer_outside_the_recorder(tmp_path, monkeypatch):
    text = page(wr(), tmp_path, setup(tmp_path, 3, monkeypatch, wr())[0])
    assert 'Counted cards need an independent review of their recorder and exact code commit' in text
    assert 'Soccer cards are sent outside the best-pick recorder, so none is recorded or named' in text


@pytest.mark.parametrize("content", ["[]", '"x"', "3", "null"])
def test_a_review_config_that_is_not_an_object_fails_closed_with_a_visible_line(tmp_path, monkeypatch, content):
    m = wr()
    data, _, _ = setup(tmp_path, 3, monkeypatch, m)
    cfg = tmp_path / 'review.json'
    cfg.write_text(content)
    text = Path(m.main(['--data-dir', str(data), '--ledger', str(tmp_path / 'ledger.sqlite'), '--out-dir', str(tmp_path / 'o'),
                        '--review-config', str(cfg), '--now', '2026-12-31T00:00:00Z'])).read_text()
    assert 'counted n=0' in section(text, 1) and 'invalid review config: top level is not an object' in text


# ---- NCAAB and the Learning section -------------------------------------------------------------------------------------
def seed_challengers(data, rows):
    from app import learning_challengers as lch
    cc = lch.open_store(data / 'research' / 'challengers.sqlite')
    for pid, gid, mk, line in rows:
        at = '2026-12-30T21:30:00Z' if pid.startswith(('ncaab', 'nfl')) else '2026-12-30T06:00:00Z'
        cc.execute("INSERT INTO challenger_forecasts (policy_id, policy_sha, spec_name, spec_json, spec_hash, definition, game_id, market, line, "
                   "p_a, p_mid, p_b, cutoff_at, tipoff_at, generated_at, dataset_version, model_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (pid, 's' + pid, 'spec_a', '{}', 'h', 'd', gid, mk, line, .5, 0, .5, at, '2026-12-30T23:00:00Z', at, 'v', 'm'))
    cc.close()


def test_ncaab_has_the_same_sections_as_nfl_counts_only(tmp_path, monkeypatch):
    m = wr()
    text = page(m, tmp_path, setup(tmp_path, 3, monkeypatch, m)[0])
    s1, s4 = section(text, 1), section(text, 4)
    assert re.search(r'- NCAAB: game cards sent since T0 n=\d+; recorded n=\d+; counted n=\d+; no pick is named for this sport', s1), s1
    assert 'NCAAB' in s4 and 'NCAAB' in text.split('Scorecard file missing for:')[1].split('\n')[0]


def test_learning_section_reports_counts_per_policy_without_a_claim(tmp_path, monkeypatch):
    m = wr()
    data = setup(tmp_path, 3, monkeypatch, m)[0]
    seed_challengers(data, [('ncaab-challengers-v1', 'g1', 'winner', None), ('ncaab-challengers-v1', 'g2', 'winner', None),
                            ('nfl-challengers-v1', 'n1', 'spread', -3.5), ('soccer-challengers-v1', 'm1', 'soccer_btts', None)])
    text = page(m, tmp_path, data)
    s5 = section(text, 5)
    assert 'Learning' in text.split('## 5.')[1].split('\n')[0]
    assert re.search(r'ncaab-challengers-v1: rows recorded n=2; games n=2', s5), s5
    assert re.search(r'nfl-challengers-v1: rows recorded n=1; games n=1', s5) and re.search(r'soccer-challengers-v1: rows recorded n=1', s5)
    assert 'settled and scored n=0' in s5 or 'no settled' in s5
    assert 'no claim' in s5.lower()
    for sport in ('NBA', 'NHL', 'NCAAB', 'NFL', 'SOCCER'):
        assert re.search(rf'- {sport}: markets with .*n=\d+ of n=\d+', s5), (sport, s5)
    assert 'adaptive fits' in s5.lower()
    assert not re.search(r'log loss|brier|profit|better than', s5, re.I) or 'no profit' in s5.lower()
    assert not re.search(r'improv\w+ (?!is |was |claim)', s5.replace('improved_prospectively', '')), s5


def test_learning_section_without_a_challenger_store_says_none_recorded(tmp_path, monkeypatch):
    m = wr()
    s5 = section(page(m, tmp_path, setup(tmp_path, 3, monkeypatch, m)[0]), 5)
    assert 'no challenger store' in s5 or 'rows recorded n=0' in s5


def test_learning_section_failure_never_breaks_the_report(tmp_path, monkeypatch):
    m = wr()
    monkeypatch.setattr(m.learning_coverage, 'coverage', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    text = page(m, tmp_path, setup(tmp_path, 3, monkeypatch, m)[0])
    assert 'learning coverage unavailable: RuntimeError' in section(text, 5)
    assert 'Best picks named on paper' in text


def test_learning_scoring_is_per_sport_and_an_error_never_kills_the_report(tmp_path, monkeypatch):
    m = wr()
    data = setup(tmp_path, 3, monkeypatch, m)[0]
    seed_challengers(data, [('nfl-challengers-v1', 'n1', 'spread', -3.5), ('ncaab-challengers-v1', 'g1', 'winner', None)])
    sqlite3.connect(data / 'ncaab.sqlite').close()
    seen = []
    def fake(store, cc, prefix=None):
        seen.append(prefix)
        if prefix == 'nfl-':
            return {'groups': [{'policy_id': 'ncaab-challengers-v1', 'n_scored': 99, 'claim': 'sample size reached: apply the rule'},
                               {'policy_id': 'nfl-challengers-v1', 'n_scored': 2, 'claim': 'no claim: x'}]}
        raise RuntimeError('boom')
    monkeypatch.setattr(m.learning_eval, 'evaluate', fake)
    (data / 'nfl.sqlite').write_bytes(b'') if not (data / 'nfl.sqlite').exists() else None
    s5 = section(page(m, tmp_path, data), 5)
    assert 'nfl-challengers-v1: rows recorded n=1; games n=1; specs n=1; settled and scored n=2' in s5 and 'sample size reached' not in s5
    assert 'scoring unavailable (RuntimeError: boom)' in s5 and 'nfl-' in seen


@pytest.fixture(autouse=True)
def _no_real_autoresearch_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'no-home'))


def test_autoresearch_spend_subsection_without_a_budget_file_says_unavailable(tmp_path, monkeypatch):
    m = wr()
    s5 = section(page(m, tmp_path, setup(tmp_path, 3, monkeypatch, m)[0]), 5)
    assert 'Autoresearch spend' in s5 and 'autoresearch spend unavailable' in s5


def test_autoresearch_spend_subsection_reports_week_total_trials_and_model(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timezone
    m = wr()
    now = datetime.now(timezone.utc)
    f = '%Y-%m-%dT%H:%M:%SZ'
    h = tmp_path / 'hm' / 'autoresearch-sports'
    d = h / 'experiments' / 'agent'
    (d / 'runs' / 'r1').mkdir(parents=True)
    (h / 'AGENT_BUDGET.json').write_text(json.dumps({
        'approved_by': 'owner', 'approved_at': '2026-11-01T00:00:00Z', 'period': 'weekly', 'period_usd_cap': 5.0, 'max_runs_per_period': 2,
        'max_usd_per_run': 2.5, 'max_total_runs': 4, 'counting_from': '2026-11-01T00:00:00Z', 'model': 'claude-opus-5-5', 'max_trials': 4,
        'sports': ['nba'], 'expires_at': '2099-01-01T00:00:00Z', 'gate_sha256': '0' * 64}))
    at = now.strftime(f)
    (d / 'runs.jsonl').write_text(json.dumps({'event': 'started', 'run_id': 'r1', 'at': at, 'model': 'claude-opus-5-5'}) + '\n' +
                                  json.dumps({'event': 'finished', 'run_id': 'r1', 'at': at, 'cost_usd': 1.25, 'trials': 2,
                                              'outcome': 'no improvement found', 'report': 'agent/runs/r1/report.json'}) + '\n')
    (d / 'runs' / 'r1' / 'report.json').write_text(json.dumps({'trials': [{'markets': {'ml': {'decision': 'discard'}, 'tot': {'decision': 'keep'}}}]}))
    (d / 'budget-ledger.jsonl').write_text(json.dumps({'run_id': 'r1', 'model_used': ['claude-opus-5-5']}) + '\n')
    monkeypatch.setenv('HOME', str(h.parent))
    s5 = section(page(m, tmp_path, setup(tmp_path, 3, monkeypatch, m)[0]), 5)
    assert '$1.25 of $5.00 cap; runs 1 of 2; total runs 1 of 4' in s5, s5
    assert 'trials completed n=2; market decisions keep n=1, discard n=1' in s5 and 'model used claude-opus-5-5' in s5
