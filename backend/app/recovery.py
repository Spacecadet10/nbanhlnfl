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


def _settle(r, now, game_status=None):
    """(picked side index, outcome) of a reconstructed vector against the result available at `now`."""
    vec = (r['p_a'], r['p_mid'], r['p_b'])
    side = combine.pick(vec, r['market'])
    outcome = 'pending'
    game_status = game_status or r['status']
    if r['current_start'] != r['tipoff_at'] or game_status in ('postponed', 'canceled'):
        outcome = 'void'
    elif game_status == 'final' and r['result_available_at'] and r['result_available_at'] <= now:
        result = markets.settle(r['market'], r['line'], status='final', home=r['home_score'], away=r['away_score'], decision=r['decision'])
        outcome = market_live.result_for_pick(r['market'], combine.SIDES[side], result) if side is not None and result in ('a', 'b', 'mid') else 'unscorable'
    return side, outcome


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
        side, outcome = _settle(r, now)
        key = (r['market'], r['model_version'], r['line_source'])
        if side is not None: groups.setdefault(key, []).append((vec[side], outcome))
        records.append({**dict(r), 'outcome': outcome, 'pick': combine.SIDES[side] if side is not None else None})
    return {'note': NOTE, 'status': 'retrospective', 'records': records,
            'groups': [{'market': k[0], 'model_version': k[1], 'line_source': k[2], 'thresholds': tracking.table(v, k[0])} for k,v in groups.items()]}


# --- Adaptive (research) reconstructions ---------------------------------------------------------------------------
ADAPTIVE_NOTE = ('Adaptive research forecasts reconstructed after the start for games the two-hour cycle missed. Each applies '
                 'only fits of the current definition whose training results and information time are at or before the '
                 'simulated pregame cutoff (one second before the start), with lines and quotes known by then; the fit '
                 'itself may have been published after the start. Never a live prediction; never used by live forecasts, '
                 'the selector or adaptive training. When no such fit exists the game is marked unreconstructable instead.')


def _adaptive_missing(conn, g, market, defn):
    if conn.execute("SELECT 1 FROM adaptive_forecasts WHERE game_id=? AND market=? AND tipoff_at=? AND status='forecast' "
                    "AND generated_at<? LIMIT 1", (g['game_id'], market, g['tipoff_at'], g['tipoff_at'])).fetchone():
        return False
    return not conn.execute('SELECT 1 FROM adaptive_recovered_forecasts WHERE game_id=? AND market=? AND tipoff_at=? '
                            'AND definition=?', (g['game_id'], market, g['tipoff_at'], defn)).fetchone()


def _insert_adaptive(conn, g, sport, defn, cutoff, now, market, status, reason, item=None, replay=None):
    from . import adaptive
    import json
    item = item or {}
    book = item.get('book')
    comb = item.get('combiner')
    p = item.get('p') if status == 'reconstructed' else None
    return conn.execute(
        'INSERT OR IGNORE INTO adaptive_recovered_forecasts (game_id, sport, market, tipoff_at, cutoff_at, reconstructed_at, '
        'definition, model_version, status, reason, line, line_source, line_ref, quote_id, quote_retrieved_at, bookmaker, '
        'p_a, p_mid, p_b, members, missing, weights, combiner_fit_id, combiner_sha256, combiner_training_cutoff, '
        'combiner_finished_at, results_used, latest_result_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (g['game_id'], sport, market, g['tipoff_at'], cutoff, now, defn, adaptive.model_version(defn), status, reason,
         item.get('line'), item.get('line_source'), item.get('line_ref'),
         item.get('quote_id') if item.get('quote_id') is not None else (book or {}).get('quote_id'),
         (book or {}).get('retrieved_at'), json.dumps(book) if book else None,
         *(p if p else (None, None, None)), json.dumps(item.get('member_rows', [])), json.dumps(item.get('missing', {})),
         json.dumps(item['weights']) if item.get('weights') else None,
         comb['fit_id'] if comb else None, comb['artifact_sha256'] if comb else None,
         comb['training_cutoff'] if comb else None, comb['finished_at'] if comb else None,
         (replay or {}).get('results_used'), (replay or {}).get('latest_result_at'))).rowcount


def recover_adaptive(conn, now=None, limit=2):
    """Bounded, retrospective adaptive reconstructions (separate ledger). Runs after the live work of the cycle."""
    from . import adaptive
    from .db import transaction
    now = now or utc_now()
    out = {'status': 'success', 'created': 0, 'unreconstructable': 0, 'deferred': {}, 'errors': {}, 'note': ADAPTIVE_NOTE}
    if not (exists(conn) and adaptive._has(conn, 'adaptive_recovered_forecasts') and adaptive._has(conn, 'adaptive_forecasts')):
        return {**out, 'status': 'unavailable', 'reason': 'adaptive recovery schema not migrated'}
    if not adaptive.enabled():
        return {**out, 'status': 'disabled', 'reason': 'RESEARCH_SELECTION=0'}
    eng = live.engine(conn)
    sport = eng.sport
    defn = adaptive.definition(conn)
    out['definition'] = defn
    usable = ','.join('?' * len(adaptive.IN_FORCE))
    # The adaptive era starts when a combiner (any definition) was first in force or the two-hour cycle first attempted an
    # adaptive forecast; earlier games are never replayed.
    start = conn.execute(f"SELECT min(at) FROM (SELECT min(finished_at) AS at FROM adaptive_fits WHERE sport=? AND kind='combiner' "
                         f"AND status IN ({usable}) UNION ALL SELECT min(generated_at) AS at FROM adaptive_forecasts WHERE sport=?)",
                         (sport, *adaptive.IN_FORCE, sport)).fetchone()[0]
    if not start:
        return {**out, 'reason': 'adaptive system not yet in force in this store'}
    earliest = conn.execute(f"SELECT min(training_cutoff) FROM adaptive_fits WHERE sport=? AND kind='combiner' AND definition=? "
                            f"AND status IN ({usable})", (sport, defn, *adaptive.IN_FORCE)).fetchone()[0]
    games = conn.execute(f"SELECT * FROM games WHERE tipoff_at>=? AND tipoff_at<=? AND status IN ('scheduled','in_progress','final') "
                         f"AND season_type IN ({','.join('?' for _ in eng.eligible_types)}) ORDER BY tipoff_at, game_id",
                         (start, now, *eng.eligible_types)).fetchall()
    processed = 0
    for g in games:
        todo = [m for m in sports.get(sport).markets if _adaptive_missing(conn, g, m, defn)]
        if not todo:
            continue
        gid = g['game_id']
        cutoff = live._ts(live._parse(g['tipoff_at']) - timedelta(seconds=1))
        if earliest is None:
            # Training for this definition has not published yet; its snapshot may still precede this game. Wait.
            out['deferred'][gid] = 'no combiner fit for the current definition yet (training pending)'
            continue
        if earliest > cutoff:
            reason = (f'every current-definition combiner was trained on results through {earliest}, after this game\'s '
                      f'cutoff {cutoff}; future-trained weights are never applied')
            with transaction(conn):
                for m in todo:
                    out['unreconstructable'] += _insert_adaptive(conn, g, sport, defn, cutoff, now, m, 'unreconstructable', reason)
            continue
        no_line = [m for m in todo if m not in ('winner', 'reg3')
                   and adaptive._primary_line(conn, g, m, sport, cutoff, replay=True) is None]
        if no_line:
            out['deferred'][gid] = f'awaiting a primary line known before the cutoff for {", ".join(no_line)}'
            continue
        if processed >= limit:
            out['more_pending'] = True
            break
        processed += 1
        r = adaptive.record(conn, now=cutoff, write=False, replay=g)
        if r['status'] == 'failed' or r['errors']:
            # An evaluation error (missing raw file, library failure) is not evidence about the game: record nothing, so a
            # transient failure never becomes a permanent 'unreconstructable' row; the game is retried next cycle.
            out['errors'][gid] = r.get('error') or r['errors']
            continue
        items = {i['market']: i for i in r['forecasts']}
        with transaction(conn):
            for m in todo:
                i = items.get(m)
                if i is None:      # retried next cycle
                    out['deferred'][f'{gid}:{m}'] = (r['skipped'].get(m) or {}).get(gid, 'not evaluated')
                    continue
                if i['status'] == 'forecast':
                    out['created'] += _insert_adaptive(conn, g, sport, defn, cutoff, now, m, 'reconstructed', None, i, r)
                else:
                    out['unreconstructable'] += _insert_adaptive(conn, g, sport, defn, cutoff, now, m, 'unreconstructable',
                                                                 i['reason'] or 'no combined probability', i, r)
    if out['errors']:
        out['status'] = 'partial'
    return out


def adaptive_report(conn, now=None):
    import json
    now = now or utc_now()
    if not (exists(conn) and conn.execute("SELECT 1 FROM sqlite_master WHERE name='adaptive_recovered_forecasts'").fetchone()):
        return {'note': ADAPTIVE_NOTE, 'groups': [], 'records': [], 'unreconstructable': [], 'status': 'schema not migrated'}
    rows = conn.execute('SELECT a.*, g.status AS game_status, g.home_score, g.away_score, g.result_available_at, '
                        'g.tipoff_at AS current_start, d.decision FROM adaptive_recovered_forecasts a JOIN games g USING(game_id) '
                        'LEFT JOIN game_decisions d USING(game_id) WHERE a.reconstructed_at<=? ORDER BY a.tipoff_at DESC, a.market',
                        (now,)).fetchall()
    groups, records, missing = {}, [], {}
    for r in rows:
        base = {k: r[k] for k in ('game_id', 'market', 'tipoff_at', 'cutoff_at', 'reconstructed_at', 'definition', 'status',
                                  'reason', 'line', 'line_source', 'p_a', 'p_mid', 'p_b', 'combiner_fit_id',
                                  'combiner_training_cutoff', 'combiner_finished_at', 'quote_retrieved_at')}
        if r['status'] != 'reconstructed':
            missing.setdefault(r['reason'], []).append(r['game_id'])
            records.append({**base, 'outcome': 'unreconstructable', 'pick': None})
            continue
        side, outcome = _settle(r, now, r['game_status'])
        if side is not None:
            groups.setdefault((r['market'], r['definition'], r['line_source']), []).append(((r['p_a'], r['p_mid'], r['p_b'])[side], outcome))
        records.append({**base, 'outcome': outcome, 'pick': combine.SIDES[side] if side is not None else None,
                        'members': len(json.loads(r['members']))})
    return {'note': ADAPTIVE_NOTE, 'status': 'retrospective', 'records': records,
            'unreconstructable': [{'reason': k, 'rows': len(v), 'games': len(set(v))} for k, v in missing.items()],
            'groups': [{'market': k[0], 'definition': k[1], 'line_source': k[2], 'thresholds': tracking.table(v, k[0])}
                       for k, v in groups.items()]}
