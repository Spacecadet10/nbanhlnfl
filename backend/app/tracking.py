"""Read-only threshold research for recorded forecasts. No threshold is automatically selected or promoted."""
import math
from collections import defaultdict
from . import agreement, live, market_live, near_tipoff
from .db import utc_now

THRESHOLDS = (0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9)
# A three-way pick can be the most probable side well below 50%: its table starts at 35% (as tracking-v2), so the first row
# is not a small fraction of the picks.
THRESHOLDS_3WAY = (0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7)


def interval(wins, n):
    """Wilson 95% interval; descriptive, not adjusted for searching thresholds or correlated games."""
    if not n:
        return None
    z = 1.959964
    p = wins / n
    den = 1 + z*z/n
    centre = (p + z*z/(2*n))/den
    half = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/den
    return [max(0, centre-half), min(1, centre+half)]


def table(rows, market=None):
    base = sum(outcome in ('win', 'loss') for _, outcome in rows)
    out = []
    for threshold in (THRESHOLDS_3WAY if market == 'reg3' else THRESHOLDS):
        selected = [(p, result) for p, result in rows if p + 1e-9 >= threshold]
        counts = {k: sum(result == k for _, result in selected)
                  for k in ('win', 'loss', 'push', 'pending', 'void', 'unscorable')}
        n = counts['win'] + counts['loss']
        out.append(dict(threshold=threshold, forecasts=len(selected), settled=n, **counts,
            accuracy=counts['win']/n if n else None, coverage=n/base if base else None,
            interval=interval(counts['win'], n)))
    return out


def report(conn, now=None):
    now = now or utc_now()
    eng = live.engine(conn)
    buckets = defaultdict(list)
    # Primary winner: one latest two-hour forecast per game, never every retry counted as a new game.
    primary_rows = conn.execute("SELECT p.* FROM predictions p WHERE p.run_type='live' "
        "AND p.feature_version LIKE '%two-hour%' AND p.model_version NOT LIKE 'research-%' AND p.generated_at<=? "
        "AND p.prediction_id=(SELECT q.prediction_id FROM predictions q WHERE q.game_id=p.game_id "
        "AND q.model_version=p.model_version AND q.run_type='live' AND q.generated_at<=? "
        "ORDER BY q.generated_at DESC,q.prediction_id DESC LIMIT 1)", (now,now))
    for r in primary_rows:
        if r['generated_at'] > now:
            continue
        g = conn.execute('SELECT * FROM games WHERE game_id=?', (r['game_id'],)).fetchone()
        outcome = 'pending'
        if g['status'] in ('postponed','canceled') or g['tipoff_at'] != r['cutoff_at']:
            outcome = 'void'
        elif g['status'] == 'final' and g['result_available_at'] and g['result_available_at'] <= now:
            outcome = 'win' if (r['home_win_prob'] >= .5) == (g['home_score'] > g['away_score']) else 'loss'
        buckets[('winner', r['model_version'], 'not_applicable')].append((max(r['home_win_prob'], 1-r['home_win_prob']), outcome))
    for r in market_live.latest(conn, policy='near_tipoff'):
        if '-window=2h-' not in r['model_version'] or r['generated_at'] > now:
            continue
        side = market_live.pick(r)
        conf = r['p_a'] if side == 'a' else r['p_b']
        result = market_live.outcome(r)
        available = conn.execute('SELECT result_available_at FROM games WHERE game_id=?', (r['game_id'],)).fetchone()[0]
        if result not in ('pending', 'void') and (not available or available > now):
            result = 'pending'
        if result in ('a','b','mid'):
            result = market_live.result_for_pick(r['market'], side, result)
        buckets[(r['market'], r['model_version'], r['line_source'] or 'no_line')].append((conf, result))
    # Each research model gets its own record. No mixing model definitions or old night-before forecasts.
    rows = conn.execute("SELECT p.*, g.status, g.tipoff_at, g.home_score, g.away_score, g.result_available_at "
        "FROM predictions p JOIN games g ON g.game_id=p.game_id WHERE p.run_type='live' AND p.feature_version=? "
        "AND p.generated_at<=? AND p.prediction_id=(SELECT MAX(q.prediction_id) FROM predictions q "
        "WHERE q.game_id=p.game_id AND q.model_version=p.model_version AND q.feature_version=p.feature_version "
        "AND q.generated_at<=?)", (agreement.FEATURE_VERSION, now, now)).fetchall()
    for r in rows:
        result = 'pending'
        if r['status'] in ('postponed','canceled') or r['tipoff_at'] != r['cutoff_at']:
            result = 'void'
        elif r['status'] == 'final' and r['result_available_at'] and r['result_available_at'] <= now:
            result = 'win' if (r['home_win_prob'] >= .5) == (r['home_score'] > r['away_score']) else 'loss'
        # Exactly 50% is an abstention in the agreement system, not a home prediction.
        if r['home_win_prob'] != .5:
            buckets[('winner', r['model_version'], 'research')].append((max(r['home_win_prob'],1-r['home_win_prob']), result))
    totals = dict(winner_rows=conn.execute('SELECT COUNT(*) FROM predictions').fetchone()[0],
                  market_rows=conn.execute('SELECT COUNT(*) FROM market_forecasts').fetchone()[0])
    return dict(sport=eng.sport, policy='two-hour-v1', all_stored_rows=totals,
        groups=[dict(market=k[0], model_version=k[1], line_source=k[2], thresholds=table(v, k[0])) for k,v in buckets.items()],
        note='All forecast versions remain stored. Tables count the latest forecast per game/model/market, not retries. '
             'Markets, model definitions and model-only/bookmaker lines are separate. Intervals are descriptive Wilson 95%; '
             'they do not account for game dependence or searching many thresholds. Choose rules on research data, then freeze '
             'and evaluate on later games; no automatic threshold tuning.')
