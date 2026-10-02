"""Prospective winner-model comparisons. Research only; never selects or replaces the live model.

Every cohort uses one information snapshot and one two-hour policy. Old backtests, shadow predictions and
primary near-tipoff rows are not votes. A missing candidate or exact 50% prediction is not a vote for either team.
"""
import hashlib
import json
from pathlib import Path
from importlib.metadata import version

from . import backtest, live, near_tipoff, shadow, sports
from .db import utc_now
from .ledger import Forecast, record

FEATURE_VERSION = "agreement-research-v2:two-hour"
ARCHIVE_FEATURE = "agreement-research-v1:pregame"
NAMES = {"nba": ("elo", "logistic", "logistic_v2", "efficiency_margin", "gbt_v1", "availability_two_hour"),
         "nhl": ("hockey_elo", "goals_poisson", "hockey_logistic", "hockey_gbt")}
# Separately versioned shadow members (docs/MODEL_SELECTION.md §12): recorded and scored, never votes, averages or stacker inputs.
SHADOW = {"nba": (), "nhl": ("hockey_logistic_xg",)}
STALE_SHOT_GAMES = 3      # a team's most recent current-season finals that must have imported shots (xG member)


def models(conn):
    eng = live.engine(conn)
    decision, primary = eng.primary(conn)
    if eng.sport == "nhl":
        from .models.hockey import GoalsConfig, GoalsPoisson, hockey_logistic, hockey_gbt
        from .models.hockey_xg import hockey_logistic_xg
        gc = GoalsConfig(**decision["goals_config"])
        return [primary, GoalsPoisson(gc, sports.NHL.rating_key),
                hockey_logistic(primary.cfg, gc, sports.NHL.rating_key),
                hockey_gbt(primary.cfg, gc, sports.NHL.rating_key),
                hockey_logistic_xg(primary.cfg, gc, sports.NHL.rating_key)]
    from .models.logistic import LogisticElo, LogisticV2
    from .models.efficiency import EfficiencyMargin
    from .models.gbt import GradientBoostedTrees
    return [primary, LogisticElo(primary.cfg), LogisticV2(primary.cfg), EfficiencyMargin(),
            GradientBoostedTrees(primary.cfg)]


def definition_hash(built):
    """Changed model code/settings get a separate forward identity, not a relabelled history."""
    h = hashlib.sha256()
    h.update(version("scikit-learn").encode())
    root = Path(__file__).parent
    for path in sorted((root / "models").glob("*.py")) + [root / "backtest.py", root / "agreement.py", root / "availability.py", root / "availability_live.py"]:
        h.update(path.name.encode())
        h.update(path.read_bytes())
    h.update(json.dumps([(m.name, m.config()) for m in built], sort_keys=True).encode())
    return h.hexdigest()[:16]


def predict(conn, *, now=None, write=True, game_ids=None, collect=None):
    """`collect`, if given, receives each model's walk-forward predictions ({name: [backtest rows]}) for the research
    stacker (app/research_live.py); it is never part of the returned job detail."""
    fixed_clock = now is not None
    now = now or utc_now()
    eng = live.engine(conn)
    horizon = live._ts(live._parse(now) + near_tipoff.WINDOW)
    rows = live.available_rows(conn, now)
    known = {r.game.game_id: r for r in rows}
    schedule = conn.execute(
        f"SELECT * FROM games WHERE status NOT IN ('postponed','canceled') AND tipoff_at <= ? "
        f"AND season_type IN ({','.join('?' for _ in eng.eligible_types)}) ORDER BY tipoff_at, game_id",
        (horizon, *eng.eligible_types)).fetchall()
    schedule = [g for g in schedule if g['game_id'] not in known]
    eligible = [g for g in schedule if g['status'] == 'scheduled' and now < g['tipoff_at'] <= horizon and (game_ids is None or g['game_id'] in game_ids)]
    out = dict(sport=eng.sport, status="success", eligible_games=len(eligible), created=0, already_recorded=0,
               errors={}, skipped={}, forecasts=[])
    if not eligible:
        return out
    events = [] if eng.sport == 'nhl' else [e for e in backtest.load_box_events(conn, 'live')
        if e.game.game_id in known and max(e.available_at, known[e.game.game_id].result_available_at) <= now]
    built = models(conn)
    ids = {g['game_id'] for g in eligible}
    shots = []
    if eng.sport == 'nhl':     # shots reach only the xG member, once both they and the result were available
        from . import nhl_shots
        shots = [e for e in nhl_shots.load_events(conn, rows)
                 if max(e.available_at, known[e.game.game_id].result_available_at) <= now]
    if eng.sport == 'nba' and len(built) == 5:
        try:
            from .availability_live import build
            built.append(build(conn, built[0].cfg, now, ids))
        except Exception as exc:
            out['errors']['availability_two_hour'] = f"{type(exc).__name__}: {exc}"
    # Identity does not change merely because an input is missing on one run.
    digest = definition_hash(models(conn))
    shots_version = hashlib.sha256(json.dumps(sorted((e.game.game_id, e.fetch_id) for e in shots)).encode()).hexdigest()[:16]
    dataset = hashlib.sha256(json.dumps([eng.dataset_version(rows), backtest.box_version(events), shots_version, digest,
        [(m.name, getattr(m, "input_version", None)) for m in built],
        [(g['game_id'], g['tipoff_at'], g['home_team_id'], g['away_team_id'], g['neutral_site']) for g in schedule]],
        sort_keys=True).encode()).hexdigest()[:24]
    out['dataset_version'], out['definition'] = dataset, digest
    recent = {}
    for r in sorted(rows, key=lambda r: r.game.tipoff_at, reverse=True):
        for team in (r.game.home_team_id, r.game.away_team_id):
            recent.setdefault((r.game.season, team), []).append(r.game.game_id)
    ids = {g['game_id'] for g in eligible}
    # Finish computing a model before writing any of its rows. Failure is visible, never substituted with Elo.
    for model in built:
        try:
            from dataclasses import replace
            training = [replace(r, cutoff_at=live._ts(live._parse(r.game.tipoff_at)-near_tipoff.WINDOW))
                        for r in rows] if getattr(model, 'training_two_hour', False) else rows
            history = backtest.run(model, training, shots if hasattr(model, 'xg') else events, deliver_until=now)
            if collect is not None:
                collect[model.name] = history
            forecasts = []
            for g in schedule:
                game = eng.pregame(g)
                p = round(model.predict(game), 9)
                if game.game_id not in ids:
                    continue
                eff = getattr(model, 'eff', None)
                if eng.sport == 'nba' and eff is not None and any(
                    gid not in eff.lines for team in (game.home_team_id, game.away_team_id)
                    for gid in recent.get((game.season, team), [])[:shadow.STALE_RECENT_GAMES]):
                    out['skipped'].setdefault(model.name, []).append(game.game_id)
                    continue
                xg = getattr(model, 'xg', None)
                if xg is not None and (xg.valued == 0 or any(
                    gid not in xg.lines for team in (game.home_team_id, game.away_team_id)
                    for gid in recent.get((game.season, team), [])[:STALE_SHOT_GAMES])):
                    out['skipped'].setdefault(model.name, []).append(game.game_id)
                    continue
                if hasattr(model, 'oos_from_season') and model.oos_from_season() is None:
                    out['skipped'].setdefault(model.name, []).append(game.game_id)
                    continue
                forecasts.append(Forecast(game.game_id, 'live', f"research-v2:{model.name}:{digest}", dataset,
                    FEATURE_VERSION, game.tipoff_at, now, p))
            from dataclasses import replace
            completed_at = now if fixed_clock else utc_now()
            for f in forecasts:
                if completed_at >= f.cutoff_at:
                    out['skipped'].setdefault(model.name, []).append(f.game_id)
                    continue
                f = replace(f, generated_at=completed_at)
                pid = None
                if write:
                    pid, new = record(conn, f)
                    out['created'] += new
                    out['already_recorded'] += not new
                out['forecasts'].append(dict(game_id=f.game_id, model=model.name, home_win_prob=f.home_win_prob,
                                             prediction_id=pid, generated_at=f.generated_at, model_version=f.model_version))
            if write and forecasts:
                from . import fit_register
                try:     # a record of the fit in force; never a reason to withhold or fail a forecast
                    fit_register.record(conn, eng.sport, model.name, digest, fit_register.identity(model, rows, now), now)
                except Exception as exc:
                    out.setdefault('fit_register_errors', {})[model.name] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            out['errors'][model.name] = f"{type(exc).__name__}: {exc}"
    core = lambda d: [k for k in d if k not in SHADOW[eng.sport]]   # a shadow member is reported, never fails the run
    if core(out['errors']) or core(out['skipped']):
        out['status'] = 'failed'
    elif out['errors'] or out['skipped']:
        out['status'] = 'partial'
    return out


def summary(conn, game_id, sport, as_of, policy="near_tipoff", definition=None):
    feature = FEATURE_VERSION if policy == "near_tipoff" else ARCHIVE_FEATURE
    rows = conn.execute("SELECT * FROM predictions WHERE game_id=? AND run_type='live' AND feature_version=? "
        "AND generated_at<=? AND generated_at<cutoff_at AND (? IS NULL OR model_version LIKE ?) "
        "ORDER BY generated_at DESC, prediction_id DESC",
        (game_id, feature, as_of, definition, "%:"+definition if definition else None)).fetchall()
    expected = NAMES[sport]
    if policy == 'pregame' and sport == 'nba':
        expected = expected[:-1]
    if not rows:
        return dict(policy=policy, status='unavailable', expected=len(expected), available=0, agreeing=0, pick=None,
                    home_votes=0, away_votes=0, abstentions=0, models=[], missing=list(expected), generated_at=None, cutoff_at=None)
    # Never fill a missing member with an older cohort or a different feature/policy definition.
    cohort = rows[0]['dataset_version']
    members = {}
    for r in rows:
        parts = r['model_version'].split(':')
        if r['dataset_version'] == cohort and len(parts) == 3 and parts[0] == ('research-v2' if policy == 'near_tipoff' else 'research-v1') and parts[1] in expected:
            members.setdefault(parts[1], r)
    votes = [r['home_win_prob'] for r in members.values()]
    home = sum(p > .5 for p in votes)
    away = sum(p < .5 for p in votes)
    return dict(policy=policy, status='complete' if len(members) == len(expected) else 'partial', expected=len(expected),
        available=len(members), agreeing=max(home, away), pick='home' if home > away else 'away' if away > home else None,
        home_votes=home, away_votes=away, abstentions=sum(p == .5 for p in votes),
        cutoff_at=rows[0]['cutoff_at'], missing=[n for n in expected if n not in members], generated_at=max(r['generated_at'] for r in members.values()),
        models=[dict(name=n, model_version=r['model_version'], home_win_prob=r['home_win_prob'],
                     generated_at=r['generated_at']) for n, r in members.items()])


def performance(conn, as_of=None, policy="near_tipoff"):
    feature = FEATURE_VERSION if policy == "near_tipoff" else ARCHIVE_FEATURE
    """Descriptive forward record, separated by exact model definition. Never tunes or promotes a candidate."""
    as_of = as_of or utc_now()
    sport = live.engine(conn).sport
    games = conn.execute("SELECT DISTINCT g.*, substr(p.model_version, instr(p.model_version, ':')+1+instr(substr(p.model_version,instr(p.model_version, ':')+1), ':')) AS definition FROM games g JOIN predictions p ON p.game_id=g.game_id "
                         "WHERE p.run_type='live' AND p.feature_version=?", (feature,)).fetchall()
    groups = {}
    partial = 0
    for g in games:
        s = summary(conn, g['game_id'], sport, as_of, policy, g['definition'])
        if s['status'] != 'complete':
            partial += 1
            continue
        definition = s['models'][0]['model_version'].rsplit(':', 1)[1]
        group = groups.setdefault(definition, dict(definition=definition, games=0, pending=0, excluded=0,
            majority=dict(picks=0, wins=0, losses=0), unanimous=dict(picks=0, wins=0, losses=0)))
        group['games'] += 1
        if g['status'] in ('postponed', 'canceled') or (g['tipoff_at'] if policy == 'near_tipoff' else backtest.cutoff_for(g['tipoff_at'])) != s['cutoff_at']:
            group['excluded'] += 1
            continue
        if g['status'] != 'final' or not g['result_available_at'] or g['result_available_at'] > as_of:
            group['pending'] += 1
            continue
        won = 'home' if g['home_score'] > g['away_score'] else 'away'
        for name, selected in [('majority', s['agreeing'] > s['expected'] / 2),
                               ('unanimous', s['agreeing'] == s['expected'])]:
            if selected:
                r = group[name]
                r['picks'] += 1
                r['wins' if s['pick'] == won else 'losses'] += 1
    for group in groups.values():
        settled = group['games'] - group['pending'] - group['excluded']
        for name in ('majority', 'unanimous'):
            r = group[name]
            r['accuracy'] = r['wins'] / r['picks'] if r['picks'] else None
            r['coverage'] = r['picks'] / settled if settled else None
    return dict(sport=sport, policy=policy, market='winner', partial_games=partial, groups=list(groups.values()),
                note='Prospective research only. Complete cohorts; majority means more than half of all expected models. '
                     'Definitions are reported separately. No claim of improvement or betting value.')
