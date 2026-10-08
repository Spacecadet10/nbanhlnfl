"""Recover missed PRIMARY markets retrospectively, never manufacture a live forecast.

Use the current sealed primary models, results available before start, and only archived
pregame quotes. Current schedule/score corrections may differ from their original values;
this is explicitly retrospective, not proof of what a sleeping machine would have predicted.
"""
from datetime import timedelta
from . import live, near_tipoff, market_live, markets, sports, tracking, combine
from .db import utc_now

NOTE = ('Reconstructed after the start with the current primary models and stored pregame-available results. '
        'Not live predictions; never included in live win rates or adaptive training. Model lines are used when no '
        'pregame quote was saved. Historical schedule/result corrections may affect reconstruction. '
        'Research, injury-aware and adaptive member forecasts are not reconstructed.')


def exists(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='recovered_forecasts'").fetchone())


def missing(conn, game, market):
    args = (game['game_id'], game['tipoff_at'], game['tipoff_at'])
    if market == 'winner':
        found = conn.execute("SELECT 1 FROM near_tipoff_forecasts WHERE game_id=? AND tipoff_at=? AND generated_at<? LIMIT 1", args).fetchone()
    else:
        found = conn.execute("SELECT 1 FROM market_forecasts WHERE game_id=? AND tipoff_at=? AND generated_at<? "
                             "AND market=? AND policy='near_tipoff' AND run_type='live' AND model_version NOT LIKE 'research-%' LIMIT 1",
                             (*args, market)).fetchone()
    return not found


def recover(conn, now=None, limit=12):
    now = now or utc_now()
    out = {'status': 'success', 'created': 0, 'errors': {}, 'note': NOTE}
    if not exists(conn):
        return {**out, 'status': 'unavailable', 'reason': 'recovery schema not migrated'}
    eng = live.engine(conn)
    # Start at the observed two-hour recording era, never backfill the historical database.
    start = conn.execute("SELECT min(at) FROM (SELECT min(generated_at) AS at FROM near_tipoff_forecasts "
                         "UNION ALL SELECT min(started_at) AS at FROM job_runs WHERE job='near_tipoff')").fetchone()[0]
    if not start:
        return {**out, 'reason': 'no two-hour recording start in this store'}
    games = conn.execute(f"SELECT * FROM games WHERE tipoff_at>=? AND tipoff_at<=? AND status IN ('scheduled','in_progress','final') "
                         f"AND season_type IN ({','.join('?' for _ in eng.eligible_types)}) ORDER BY tipoff_at,game_id",
                         (start, now, *eng.eligible_types)).fetchall()
    history = None
    processed = 0
    for g in games:
        todo = [m for m in sports.get(eng.sport).markets if missing(conn, g, m) and not conn.execute(
            'SELECT 1 FROM recovered_forecasts WHERE game_id=? AND market=? AND tipoff_at=?',
            (g['game_id'], m, g['tipoff_at'])).fetchone()]
        if not todo:
            continue
        if processed >= limit:
            out['more_pending'] = True
            break
        processed += 1
        cutoff = live._ts(live._parse(g['tipoff_at']) - timedelta(seconds=1))
        try:
            if history is None:
                history = eng.load_rows(conn)
            rows = [r for r in history if r.game.game_id != g['game_id'] and r.game.tipoff_at < cutoff and r.result_available_at <= cutoff]
            rows.sort(key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id))
            game = eng.pregame(g)
            winner = line_model = dist = None
            if 'winner' in todo:
                _, winner = live.primary_model(conn)
            if any(m != 'winner' for m in todo):
                line_model, line_version = market_live.market_model(conn)
            for r in rows:
                if winner: winner.update(r.result)
                if line_model: line_model.update(r.result)
            if line_model: dist = line_model.distribution(game)
            dataset = eng.dataset_version(rows)
            for m in todo:
                line = quote_id = None
                source = 'none'
                if m == 'winner':
                    p = winner.predict(game)
                    vec = (p, 0., 1-p)
                    version = near_tipoff.model_version(winner)
                else:
                    if m != 'reg3':
                        q = market_live.latest_quote(conn, g['game_id'], m, cutoff)
                        line = q['line'] if q else markets.model_line(dist, m)
                        source = 'market' if q else 'model'
                        quote_id = q['quote_id'] if q else None
                    vec = dist.market(m, line)
                    version = market_live.model_version(line_version, 'near_tipoff')
                cur = conn.execute('INSERT OR IGNORE INTO recovered_forecasts '
                    '(game_id,market,tipoff_at,cutoff_at,reconstructed_at,model_version,dataset_version,line,line_source,quote_id,'
                    'p_a,p_mid,p_b,results_used,latest_result_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (g['game_id'], m, g['tipoff_at'], cutoff, now, version, dataset, line, source, quote_id, *vec,
                     len(rows), max((r.result_available_at for r in rows), default=None)))
                out['created'] += cur.rowcount
        except Exception as exc:
            out['errors'][g['game_id']] = f'{type(exc).__name__}: {exc}'
    if out['errors']: out['status'] = 'partial'
    return out


def report(conn, now=None):
    now = now or utc_now()
    groups, records = {}, []
    if not exists(conn):
        return {'note': NOTE, 'groups': [], 'records': [], 'status': 'schema not migrated'}
    rows = conn.execute('SELECT r.*, g.status, g.home_score, g.away_score, g.result_available_at, g.tipoff_at AS current_start, '
                        'd.decision FROM recovered_forecasts r JOIN games g USING(game_id) LEFT JOIN game_decisions d USING(game_id) '
                        'WHERE r.reconstructed_at<=? ORDER BY r.tipoff_at DESC', (now,)).fetchall()
    for r in rows:
        vec = (r['p_a'], r['p_mid'], r['p_b'])
        side = combine.pick(vec, r['market'])
        outcome = 'pending'
        if r['current_start'] != r['tipoff_at'] or r['status'] in ('postponed','canceled'):
            outcome = 'void'
        elif r['status'] == 'final' and r['result_available_at'] and r['result_available_at'] <= now:
            result = markets.settle(r['market'], r['line'], status='final', home=r['home_score'], away=r['away_score'], decision=r['decision'])
            outcome = market_live.result_for_pick(r['market'], combine.SIDES[side], result) if side is not None and result in ('a','b','mid') else 'unscorable'
        key = (r['market'], r['model_version'], r['line_source'])
        if side is not None: groups.setdefault(key, []).append((vec[side], outcome))
        records.append({**dict(r), 'outcome': outcome, 'pick': combine.SIDES[side] if side is not None else None})
    return {'note': NOTE, 'status': 'retrospective', 'records': records,
            'groups': [{'market': k[0], 'model_version': k[1], 'line_source': k[2], 'thresholds': tracking.table(v, k[0])} for k,v in groups.items()]}
