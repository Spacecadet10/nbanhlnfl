"""NBA roster snapshots for the injury-aware member (jobs.roster_snapshot): which teams, how often, and retrieval times."""
import json

from app import availability_live as al, jobs
from test_live import event, ingest_events, store  # noqa: F401  (fixture)

NOW = '2026-10-21T20:00:00Z'


def _teams_with_games(conn):
    ingest_events(conn, [event('900', '2026-10-21T23:30:00Z', '1', '2'), event('901', '2026-10-23T23:30:00Z', '3', '4')],
                  '2026-10-21T12:00:00Z')


def test_rosters_are_saved_for_teams_playing_in_the_next_day_with_the_time_they_arrived(store):
    _teams_with_games(store)
    calls, clock = [], iter(['2026-10-21T20:00:03Z', '2026-10-21T20:00:05Z'])
    fetch = lambda team: calls.append(team) or json.dumps({'athletes': [{'id': f'{team}0', 'fullName': 'X Y',
                                                                         'experience': {'years': 0}}]}).encode()
    out = jobs.roster_snapshot(store, now=NOW, fetch=fetch, sleep=lambda _s: None, clock=lambda: next(clock))
    assert sorted(calls) == ['1', '2'] and out['fetched'] == 2 and out['status'] == 'success'   # team 3/4 play in 2 days
    rows = store.execute("SELECT r.request_key, f.retrieved_at FROM raw_fetches f JOIN raw_responses r USING(raw_id) "
                         "WHERE r.source=? ORDER BY 1", (al.ROSTER_SOURCE,)).fetchall()
    assert [tuple(r) for r in rows] == [('team=1', '2026-10-21T20:00:03Z'), ('team=2', '2026-10-21T20:00:05Z')]
    assert set(al.latest_rosters(store, '2026-10-21T20:00:05Z')) == {'1', '2'}
    assert al.latest_rosters(store, '2026-10-21T20:00:04Z').keys() == {'1'}       # never read before it arrived


def test_a_fresh_roster_is_not_fetched_again_and_a_failure_is_reported_not_fatal(store):
    _teams_with_games(store)
    jobs.roster_snapshot(store, now=NOW, fetch=lambda t: b'{"athletes": []}', sleep=lambda _s: None, clock=lambda: NOW)
    again = jobs.roster_snapshot(store, now='2026-10-21T21:00:00Z', fetch=lambda t: 1 / 0, sleep=lambda _s: None)
    assert again['fetched'] == 0 and again['fresh'] == 2
    later = jobs.roster_snapshot(store, now='2026-10-22T03:00:00Z', fetch=lambda t: 1 / 0, sleep=lambda _s: None)
    assert later['status'] == 'failed' and later['failed'] == 2 and 'ZeroDivisionError' in later['errors']['1']
