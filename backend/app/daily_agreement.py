"""Daily unanimous-rule discovery, frozen inside existing adaptive combiner artifacts.

No fitting or registration on a read path. Forecasts use the existing append-only
adaptive ledger. This policy is separate from averaged/learned combinations.
"""
import json

import numpy as np

from . import combination_thresholds as ct, markets, sports
from .errors import InvalidRequest

VERSION = 'daily-agreement-v1'
POLICY = {'min_search_picks': 100, 'min_validation_picks': 50,
          'min_validation_coverage': 0.05, 'switch_margin': 0.02,
          'budget_per_size': 512, 'beam_width': 8, 'seed': 17}


def make_table(market, rows):
    """Final chronological third validates earlier search; cutoff embargo at the split."""
    rows = sorted(rows, key=lambda r: (r['tipoff_at'], r['game_id']))
    if len(rows) < 3:
        return None
    boundary = rows[len(rows) * 2 // 3]['cutoff_at']
    early = np.array([r['tipoff_at'] < boundary and r['result_available_at'] < boundary for r in rows])
    later = np.array([r['cutoff_at'] >= boundary for r in rows])
    names = sorted({m for r, ok in zip(rows, early) if ok for m in r['vectors']})
    if not names or not early.any() or not later.any():
        return None
    t = ct.Table(market, rows, names, later_seasons=[])
    t.periods = {'earlier': early, 'later': later}
    return t


def _score(rule, market):
    """Selection score of a rule: its LATER-period Wilson lower bound, after sample/coverage/baseline/stability gates.
    NOTE: this is the selection statistic. The caller takes the arg-max of it over many candidates, so the chosen rule's
    later-period record is optimistically biased (a winner's-curse) and is not unbiased validation. The only unbiased
    evidence is the prospective ledger recorded before games."""
    r = rule['later']['record']
    baseline = rule['later']['coverage_matched_best_single']
    if (r['wins'] + r['losses'] < POLICY['min_validation_picks']
            or (r['coverage'] or 0) < POLICY['min_validation_coverage'] or not baseline
            or baseline['win_rate'] is None or r['win_rate'] <= baseline['win_rate']):
        return None
    # Per-season stability: require at least one sizeable season, and no sizeable season below the break-even floor.
    # Break-even is 50% only for two-way markets; a three-way market (the draw is a third outcome) has no 50% floor,
    # so applying one there would silently reject every regulation-three-way rule. There the overall baseline-beating
    # gate above carries stability. This gate is conservative, not evidence of positive lift per season.
    floor = None if market in ct.combine.THREE_WAY else 0.5
    seasons = [s for s in rule['later']['by_season'] if s['wins'] + s['losses'] >= 20]
    if not seasons or (floor is not None and any(s['win_rate'] < floor for s in seasons)):
        return None
    return (r['win_rate_ci95'] or [0])[0]


def assess(market, rows, cutoff, incumbent=None):
    rows = [r for r in rows if r['result_available_at'] < cutoff and r['tipoff_at'] < cutoff]
    t = make_table(market, rows)
    out = {'version': VERSION, 'cutoff': cutoff, 'policy': POLICY, 'status': 'insufficient_evidence',
           'rule': None, 'reason': 'Need chronological search and validation samples.', 'rules': [],
           'search': None, 'historical_kind': 'reconstructed chronological out-of-sample; exploratory'}
    # Every decision carries the pairwise pick overlap (pseudo-agreement disclosure), also on this early return: no pairs
    # when no table could be built or only one model has earlier forecasts.
    out['member_overlap'] = (ct.member_overlap(t, t.models) if t is not None
                             else {'period': 'earlier', 'pairs': [], 'note': ct.MEMBER_OVERLAP_NOTE})
    if t is None or len(t.models) < 2:
        return out
    searched = ct.search_large_subsets(t, budget_per_size=POLICY['budget_per_size'],
                                      beam_width=POLICY['beam_width'], min_picks=POLICY['min_search_picks'])
    # Collapse equivalent earlier evidence for selection, retaining size-stratified search output for inspection.
    unique = {}
    for rule in sorted(searched['rules'], key=lambda r: (len(r['members']), r['rule'])):
        unique.setdefault(ct._earlier_signature(t, rule['members'], rule['thresholds']), rule)
    ranked = sorted((r for r in unique.values() if _score(r, market) is not None),
                    key=lambda r: (-_score(r, market), len(r['members']), r['rule']))
    chosen = ranked[0] if ranked else None
    reason = 'No candidate meets the frozen sample, coverage, baseline and stability gates.'
    if incumbent and all(m in t.models for m in incumbent['members']):
        current = ct.evaluate_rule(t, incumbent['members'], incumbent['thresholds'], searched['best_single'])
        score = _score(current, market)
        if score is not None and (chosen is None or _score(chosen, market) < score + POLICY['switch_margin']):
            chosen, reason = current, ('Retained: the challenger did not beat the incumbent on the later-period selection '
                                       'lower bound by 2 points. That later-period record is the selection statistic, so it '
                                       'is optimistic, not unbiased validation; the prospective ledger is the real evidence.')
    if chosen and reason.startswith('No candidate'):
        reason = ('Selected as the maximum later-period Wilson lower bound over the candidates, after sample, coverage, '
                  'baseline and stability gates. Because the rule is chosen by maximizing this same later-period record, '
                  'that record is optimistically biased (a winner\'s-curse), not unbiased validation. The prospective '
                  'ledger recorded before games is the only unbiased evidence.')
    out.update({'rules': searched['rules'], 'search': searched['search'], 'best_single': searched['best_single'],
                'member_overlap': ct.member_overlap(t, searched['search']['models']),
                'equivalent_earlier_rules_collapsed': len(searched['rules']) - len(unique),
                'reason': reason, 'status': 'selected' if chosen else 'insufficient_evidence',
                'rule': {k: chosen[k] for k in ('members', 'thresholds', 'rule')} if chosen else None})
    return out


def apply(decision, vectors, market):
    result = {'version': VERSION, 'status': 'no_pick', 'side': None, 'probability': None,
              'rule': decision.get('rule') if decision else None}
    if not decision or not decision.get('rule'):
        return {**result, 'reason': 'No frozen daily agreement rule meets the evidence gates.'}
    rule = decision['rule']
    try:
        members, thresholds = rule['members'], np.asarray(rule['thresholds'], dtype=float)
        valid = (isinstance(members, list) and len(members) > 0
                 and all(isinstance(name, str) for name in members) and len(set(members)) == len(members)
                 and thresholds.shape == (len(members),) and np.isfinite(thresholds).all()
                 and (thresholds >= 0).all() and (thresholds <= 1).all())
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        return {**result, 'reason': 'Invalid frozen rule: members and finite thresholds must correspond.'}
    sides, confidence = [], []
    allowed = ct.combine.sides(market)
    for name, threshold in zip(members, thresholds):
        if name not in vectors:
            return {**result, 'reason': f'Missing member: {name}'}
        try:
            v = np.asarray(vectors[name], dtype=float)
            valid = (v.shape == (3,) and np.isfinite(v).all() and (v >= 0).all() and (v <= 1).all()
                     and np.isclose(v.sum(), 1., rtol=0., atol=1e-6))
        except (TypeError, ValueError):
            valid = False
        if not valid:
            return {**result, 'reason': f'Invalid probability vector: {name}'}
        total = sum(v[i] for i in allowed)
        winners = [i for i in allowed if abs(v[i] - max(v[j] for j in allowed)) <= ct.EPS]
        if total <= 0 or len(winners) != 1:
            return {**result, 'reason': f'{name} abstains'}
        side = winners[0]
        if v[side] / total < threshold - ct.EPS:
            return {**result, 'reason': f'{name} below its threshold'}
        sides.append(side); confidence.append(v[side] / total)
    if len(set(sides)) != 1:
        return {**result, 'reason': 'Members disagree'}
    return {**result, 'status': 'pick', 'side': sides[0], 'probability': sum(confidence) / len(confidence),
            'reason': 'Unanimous frozen subset; probability is descriptive mean member confidence, not a calibrated combiner.'}


def _outcome(market: str, r, sel: dict, as_of: str) -> str:
    """The prospective ledger outcome (a counter key) of one recorded pick as of `as_of`."""
    from . import combination_research as cr
    if r['tipoff_at'] != r['current_tipoff'] or r['game_status'] in ('postponed', 'canceled'):
        return 'void'
    if r['game_status'] == 'final' and r['result_available_at'] and r['result_available_at'] <= as_of:
        y = cr._realised(market, r['line'], r['home_score'], r['away_score'], r['decision'])
        if y is None:       # final, but never scorable (e.g. reg3 without a stored regulation decision)
            return 'unscorable'
        return 'pushes' if y == 1 and market not in ct.combine.THREE_WAY else ('wins' if y == sel['side'] else 'losses')
    return 'pending'


def report(conn, as_of):
    from . import adaptive, config, live, combination_research as cr
    sport = live.engine(conn).sport
    with adaptive.cadence('daily'):
        defn = adaptive.definition(conn)
        members, excluded = adaptive.members(conn)
        fit = adaptive.latest_fit(conn, sport, 'combiner', 'combiner', defn, as_of) if adaptive._has(conn, 'adaptive_fits') else None
        art, error = {}, None
        if fit:
            try:
                art = adaptive.load_fit(fit)
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'
        in_force, in_force_error = adaptive.combiners_in_force(conn, sport, defn, as_of)
        usable, unavailable = adaptive._load_member_artifacts(conn, sport, defn, members, as_of) if adaptive._has(conn, 'adaptive_fits') else ({}, {})
        attempts = [dict(r) for r in conn.execute("SELECT fit_id,name,status,training_cutoff,finished_at,detail FROM adaptive_fits WHERE definition=? ORDER BY fit_id DESC LIMIT 40", (defn,))] if adaptive._has(conn, 'adaptive_fits') else []
    forecasts = []
    if adaptive._has(conn, 'adaptive_forecasts'):
        forecasts = [dict(r) for r in conn.execute("SELECT a.*, g.status game_status,g.tipoff_at current_tipoff,g.home_score,g.away_score,g.result_available_at,d.decision FROM adaptive_forecasts a JOIN games g USING(game_id) LEFT JOIN game_decisions d USING(game_id) WHERE a.definition=? AND a.generated_at<=? ORDER BY a.generated_at DESC,a.forecast_id DESC", (defn, as_of))]
    latest = {}
    for r in forecasts:
        latest.setdefault((r['game_id'], r['market']), r)
    out = {'sport': sport, 'definition': defn, 'as_of': as_of, 'version': VERSION,
           'recording_enabled': config.daily_agreement_enabled(),
           'schedule': 'Daily when new eligible results exist and an upcoming eligible game is on file. Operator must schedule adaptive-train --cadence daily; no scheduler installed by this change.',
           'fit': dict(fit) if fit else None, 'error': error, 'attempts': attempts, 'markets': [],
           'historical_record_bias': 'selection',
           'note': ('Historical records are exploratory and the later third both picks the chosen rule and shows its '
                    'record, so the chosen rule\'s historical win rate is optimistically biased (a winner\'s-curse) and '
                    'its 95% interval is nominal, not selection-adjusted. Prospective outcomes are scored only from rules '
                    'frozen before games and are the unbiased evidence. No claim of superiority or profit.')}
    for market in sports.get(sport).markets:
        try:            # one market's failure (e.g. an invalid saved vector) is that market's error, never the sport's
            out['markets'].append(_market_report(market, in_force, latest, members, usable, unavailable, excluded, error,
                                                 in_force_error, as_of))
        except Exception as exc:
            msg = f'This market could not be reported: {type(exc).__name__}: {exc}'
            out['markets'].append({'market': market, 'models': [], 'history_games': 0,
                                   'source_counts': {'walk_forward': 0, 'prospective': 0}, 'fit_id': None, 'thresholds': None,
                                   'decision': None, 'levels': list(ct._levels(market)), 'incumbent_error': None,
                                   'prospective': {**dict(picks=0, wins=0, losses=0, pushes=0, pending=0, void=0, unscorable=0,
                                                          no_pick=0), 'eligible': 0, 'win_rate': None, 'win_rate_ci95': None},
                                   'ledger': [], 'reason': msg, 'error': msg})
    return out


def _market_report(market, in_force, latest, members, usable, unavailable, excluded, error, in_force_error, as_of):
    market_fit, market_art = in_force.get(market, (None, {}))
    rows = market_art.get('agreement_history', {}).get(market, [])
    decision = market_art.get('daily_agreement', {}).get(market)
    t = make_table(market, rows)
    ladder = ct.thresholds(t, t.models) if t is not None else None
    ledger = []
    counts = dict(picks=0, wins=0, losses=0, pushes=0, pending=0, void=0, unscorable=0, no_pick=0)
    for r in latest.values():
        if r['market'] != market:
            continue
        sel = json.loads(r['selection']).get('daily_agreement', {})
        outcome = 'no_pick'
        if sel.get('status') == 'pick':
            counts['picks'] += 1
            outcome = _outcome(market, r, sel, as_of)
        counts[outcome] += 1
        ledger.append({'game_id': r['game_id'], 'tipoff_at': r['tipoff_at'], 'generated_at': r['generated_at'],
                       'line': r['line'], 'fit_id': r['combiner_fit_id'], 'selection': sel, 'outcome': outcome})
    decided = counts['wins'] + counts['losses']
    member_rows = []
    for m in members:
        mf = usable[m.name][0] if m.name in usable else None
        member_rows.append({'name': m.name, 'supported': market in m.markets,
                            'reason': unavailable.get(m.name) if market in m.markets else 'Model does not produce this market.',
                            'fit_id': mf['fit_id'] if mf else None,
                            'historical_games': sum(m.name in r['vectors'] for r in rows)})
    member_rows += [{'name': n, 'supported': False, 'reason': reason, 'fit_id': None, 'historical_games': 0} for n, reason in excluded.items()]
    return ({'market': market, 'models': member_rows, 'history_games': len(rows),
                           'source_counts': {source: sum(r.get('source', 'walk_forward') == source for r in rows) for source in ('walk_forward', 'prospective')}, 'fit_id': market_fit['fit_id'] if market_fit else None,
                           'thresholds': ladder, 'decision': decision, 'levels': list(ct._levels(market)),
                           'incumbent_error': market_art.get('daily_incumbent_error'),
                           'prospective': {**counts, 'eligible': len(ledger), 'win_rate': counts['wins']/decided if decided else None,
                                           'win_rate_ci95': ct.nhl_eval._wilson(counts['wins'], decided)},
                           'ledger': ledger,
                           'reason': error or in_force_error or (None if rows else 'No daily-cadence out-of-sample artifact for this definition. Training has not been run.')})


def evidence(conn, as_of, market, rule, period='later', offset=0):
    """Bounded game drill-down and complete common-threshold ladder for any exact subset."""
    from . import adaptive, live
    sport = live.engine(conn).sport
    if market not in sports.get(sport).markets or period not in ('earlier', 'later'):
        raise InvalidRequest('Unsupported market or evidence period')
    with adaptive.cadence('daily'):
        defn = adaptive.definition(conn)
        in_force, _ = adaptive.combiners_in_force(conn, sport, defn, as_of)
        fit, art = in_force.get(market, (None, {}))
    if not fit:
        raise InvalidRequest('No daily fit for this definition')
    t = make_table(market, art.get('agreement_history', {}).get(market, []))
    if t is None:
        raise InvalidRequest('No chronological daily evidence for this market')
    names, ts = ct._parse_rule(rule, t.models)
    eligible, side, qualifying = ct._qualifying(t, names, ts)
    mask = t.periods[period]
    idx = np.nonzero(qualifying & eligible & mask)[0]
    games = []
    for i in idx[offset:offset + 100]:
        r = t.rows[i]
        games.append({'game_id': r['game_id'], 'tipoff_at': r['tipoff_at'], 'line': r['line'],
                      'source': r.get('source', 'walk_forward'), 'side': int(side[i]),
                      'result': {1: 'win', 0: 'loss', 2: 'push', -1: 'pending'}[int(t.O[i, side[i]])],
                      'probabilities': {m: float(t.conf[m][i]) for m in names}})
    # Unanimity at a common threshold is min(member confidence), never the group's average.
    common_eligible, common_side, unanimous = ct._qualifying(t, names, [0.0] * len(names))
    t.side['unanimous'] = np.where(unanimous, common_side, -1)
    t.conf['unanimous'] = np.min(np.stack([t.conf[m] for m in names]), axis=0)
    return {'definition': defn, 'fit_id': fit['fit_id'], 'period': period, 'rule': rule,
            'evaluation': ct.evaluate_rule(t, names, ts, ct._best_single(t, t.models)),
            'ladder': ct._ladder(t, 'unanimous', common_eligible, mask, ct._levels(market)),
            'games': games, 'total': len(idx), 'offset': offset, 'limit': 100,
            'historical_record_bias': 'selection',
            'note': ('Fixed unequal thresholds govern the game list. The ladder varies a common threshold across these '
                     'members; all must agree. These are historical records on the saved out-of-sample forecasts; when '
                     'the rule shown is the auto-selected one, its later-period record is the selection statistic and is '
                     'optimistically biased. The prospective ledger recorded before games is the unbiased evidence.')}
