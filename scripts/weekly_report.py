#!/usr/bin/env python3
"""Weekly plain-language page for all four sports (Step H, owner Nov 11 2026). READ-ONLY: the sport stores, the scorecard
files and the Telegram ledger are opened with mode=ro URIs; the only thing written is one Markdown file,
<out-dir>/weekly-YYYY-MM-DD.md (default <data>/reports). Sends nothing.

Sections: 1. best picks named on paper (scripts/best_picks.py: counted cards and the v4 evaluation, not reimplemented);
2. hypotheses H1-H7 on new games (the recorder's own H items on counted cards); 3. cards where the named pick lost: what
won on that card and its full picture; 4. exact percentages that came up again (scripts/scorecard_report.py readers; key =
threshold_overview.percentage_record's: displayed % to one decimal, side, source, model version, line source; never ranges).

No interim look (docs/preregistration/best-pick-2026-11-11-v4.md section 9). Until a sport's 100-pick evaluation set is
complete (or the rule is dropped for too few games), sections 1-3 show COUNTS ONLY for that sport. Decided here (Step H):
H1-H7 on new games are the rule's score items on the very cards and picks the test grades (named = score >= 2, baseline =
highest book %), so their win counts are an interim look and are hidden too; section 3 is the named pick's outcome
itself, hidden; section 4 holds back every market of every GAME the recorder stored with a named or baseline pick (all rows,
reviewed or not; by game because the scorecard's canonical forecast is often regenerated after the card was sent, and the
other markets of the card are correlated), and if the ledger cannot be read, every NBA/NHL pick of a game after T0.
Every line carries its n; a comparison with fewer than scorecard_report.MIN_N (30) on a side says "too few to tell".

  NBA_DATA_DIR=<copy> python scripts/weekly_report.py --ledger <ledger copy> [--out-dir DIR] [--now ...]
      [--reviewed-at UTC --reviewed-recorder SHA256 --reviewed-commit SHA40]      (without them no card counts yet, as in best_picks.py)
"""
import argparse
import importlib.util
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'backend'))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bp = _load('best_picks_report', 'best_picks.py')
sr = _load('scorecard_report', 'scorecard_report.py')
from app import best_pick_record as bpr  # noqa: E402
from app import config  # noqa: E402
from app import autoresearch_budget, learning_challengers, learning_coverage, learning_eval  # noqa: E402

MIN_N = sr.MIN_N
SPORTS = ('nba', 'nhl', 'ncaab', 'nfl', 'soccer')
FMT = '%Y-%m-%dT%H:%M:%SZ'
MAX_REPEAT_LINES = 5            # per sport and market in section 4; newest lost cards in section 3
# The recorder's independent review (time written in HANDOFF.md, reviewed recorder sha256): kept in one small file next to
# this script; --reviewed-at / --reviewed-recorder / --reviewed-commit override it.
REVIEW_CONFIG = HERE / 'weekly_report_review.json'


REVIEW_SOURCE = {'path': str(REVIEW_CONFIG), 'unavailable': None, 'invalid': None}    # set by main(); shown on the page
ReviewConfigError, check_entry = bp.ReviewConfigError, bp.check_entry          # one strict validator, shared with best_picks.py


def review_defaults(path=REVIEW_CONFIG) -> tuple:
    """(earliest reviewed_at or None, {recorder sha256: [{reviewed_at, code_commit}, ...]}) from the review config's
    "reviews" list; (None, {}) when the file is absent. An invalid entry raises ReviewConfigError."""
    p = Path(path)
    if not p.is_file():
        return None, {}
    cfg = json.loads(p.read_text())
    if not isinstance(cfg, dict):
        raise ReviewConfigError('invalid review config: top level is not an object')
    reviews = cfg.get('reviews')
    if not isinstance(reviews, list) or not reviews:
        raise ReviewConfigError('invalid review entry #0: the config has no "reviews" list')
    out = {}
    for i, e in enumerate(reviews):
        out.setdefault(e['recorder_sha256'] if isinstance(e, dict) and isinstance(e.get('recorder_sha256'), str) else '', []).append(
            check_entry(i, e))
    return min(e['reviewed_at'] for v in out.values() for e in v), out

# H item: (sport, plain meaning of +1, of 0, of -1, the two values compared: better first)
H_ITEMS = {
    'H1': ('nba', 'winner, sportsbook 72%+', None, 'winner, sportsbook 58% or less', (1, -1)),
    'H2': ('nba', 'winner, app below sportsbook', None, 'winner, app 5.6+ points above sportsbook', (1, -1)),
    'H3': ('nba', "winner, app's spread pick on the other team", None, "winner, app's spread pick on the same team", (1, -1)),
    'H4': ('nba', 'winner, sportsbook moved 3+ toward the pick', None, 'winner, sportsbook moved 3+ away', (1, -1)),
    'H5a': ('nhl', 'puck line on the team the app picks to win', None, 'puck line on the other team', (1, -1)),
    'H5b': ('nhl', 'puck line, app 67%+', 'puck line, app below 67%', None, (1, 0)),
    'H5c': ('nhl', None, 'puck line, picked team rested', 'puck line, picked team played the day before', (0, -1)),
    'H6': (None, None, 'total, neither team played the day before', 'total, a team played the day before', (0, -1)),
    'H7': ('nhl', 'regulation pick on the winner pick at 60%+', None, 'regulation pick otherwise', (1, -1)),
}


def _t(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace('Z', '+00:00'))


def _ro(path) -> sqlite3.Connection:
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _wl(w: int, n: int) -> str:
    return f'{w} won, {n - w} lost (n={n})'


def _rate(w: int, n: int) -> str:
    return 'too few to tell' if n < MIN_N else f'won {100 * w / n:.1f}%'


# ---- output folder ------------------------------------------------------------------------------------------------------
def check_out_dir(out_dir) -> Path:
    """Inside a live data folder only its reports/ folder is allowed; anywhere else outside live data is fine."""
    out = Path(out_dir).expanduser().resolve()
    for p in (out, *out.parents):
        if config.protected_data_dir(p):
            allowed = (p / 'reports').resolve()
            if out != allowed and allowed not in out.parents:
                raise SystemExit(f'refused: {out} is inside the live data folder {p}; only {allowed} may be written')
            break
    return out


# ---- the running test (scripts/best_picks.py) ---------------------------------------------------------------------------
def test_state(ledger, store, sport: str, now: datetime, reviewed_at, reviewed: set) -> dict:
    """Counted cards, the v4 evaluation (counts only until complete) and whether results may be shown ('open')."""
    out = {'sport': sport, 'error': None, 'info': None, 'eval': None, 'open': False, 'held': None}
    if ledger is None:
        out['error'] = 'ledger not readable'
        return out
    try:
        info = bp.counted_cards(ledger, sport, reviewed_at, reviewed)
        has = ledger.execute("SELECT 1 FROM sqlite_master WHERE name='best_picks'").fetchone()
        # Games, not forecast ids: the scorecard's canonical pick is the latest forecast before the start and is often
        # regenerated after the card was sent, so its id differs from the recorded one; and the other markets of the same
        # card are correlated with the named pick. Every market of such a game is held back (review HIGH, Nov 11).
        out['held'] = set() if not has else {
            r[0] for r in ledger.execute('SELECT game_id FROM best_picks WHERE sport=? AND (named_forecast_id IS NOT NULL '
                                         'OR baseline_forecast_id IS NOT NULL)', (sport,))}
    except sqlite3.Error as exc:
        out['error'] = f'ledger not readable ({type(exc).__name__})'
        return out
    out['info'] = info
    if sport not in bpr.RULE_MARKETS:
        return out
    if info['cards']:
        if store is None:
            out['error'] = 'store not readable'
            return out
        out['eval'] = bp.evaluate(store, info['cards'], now)
        state = out['eval']['state']
        out['open'] = 'W' in out['eval'] or state.startswith('too few games')
    elif now > _t(bp.DEADLINE):
        out['open'] = True
    return out


def _grades(store, cards, now) -> dict:
    fids = {p['forecast_id'] for c in cards for p in json.loads(c['picture'])['picks'] if p.get('forecast_id') is not None}
    return {f: bp.grade(store, f, now)[0] for f in sorted(fids)}


HIDDEN = ('hidden until {s} has {n} decided named picks (decided so far n={d}); preregistered test v4, no interim looks '
          '(sections 7 and 9)')


def section_best(states: dict) -> list:
    lines = ['## 1. Best picks named on paper', '']
    for sport in SPORTS:
        st, S = states[sport], sport.upper()
        if sport == 'soccer':
            lines.append('- SOCCER: Soccer cards are sent outside the best-pick recorder, so none is recorded or named (n=0).')
            continue
        if st['error']:
            lines.append(f"- {S}: {st['error']}; counts unknown (n=0 shown).")
            continue
        info = st['info']
        base = (f"- {S}: game cards sent since T0 n={info['sent']}; recorded n={info['rows']}; counted "
                f"n={len(info['cards'])}")
        if info['excluded']:
            lines.append(f"- {S}: {sum(info['excluded'].values())} games excluded: "
                         + ', '.join(f'{k} n={v}' for k, v in sorted(info['excluded'].items())) + '.')
        for why, label in (('code revision not reviewed', 'code revision'), ('recorder version not reviewed', 'recorder version')):
            for ident in sorted(info['unreviewed'].get(why, ())):
                lines.append(f"- {S}: games excluded: {label} {ident[:8]} not reviewed — append a review entry to "
                             f"{REVIEW_SOURCE['path']}.")
        if sport not in bpr.RULE_MARKETS:
            lines.append(base + '; no pick is named for this sport (v4 section 8), named n=0.')
            continue
        ev = st['eval'] or {'named': 0, 'named_decided': 0, 'state': 'counting'}
        lines.append(base + f"; named n={ev['named']}; decided n={ev['named_decided']}; not decided "
                     f"n={ev['named'] - ev['named_decided']}.")
        if not st['open']:
            lines.append(f"- {S} results: " + HIDDEN.format(s=S, n=bp.N_EVAL, d=min(ev['named_decided'], bp.N_EVAL)) + '.')
        elif 'W' in ev:
            lines.append(f"- {S} evaluation set: {_wl(ev['W'], ev['n'])}; result: {ev['state']}; the comparison pick "
                         f"(highest sportsbook %) won {100 * ev['full_hit_rate']['baseline']:.1f}% "
                         f"(n={ev['full_hit_rate']['baseline_n']}).")
        else:
            lines.append(f"- {S}: {ev['state']} (named n={ev['named']}).")
    return lines + ['']


def section_hypotheses(states: dict, stores: dict, now: datetime) -> list:
    lines = ['## 2. Hypotheses H1-H7 on new games', '',
             'Only cards counted by the test (after T0, reviewed recorder). Descriptive; not the preregistered test.', '']
    for sport in ('nba', 'nhl'):
        st, S = states[sport], sport.upper()
        if st['error'] or not st['info'] or not st['info']['cards']:
            n = 0 if st['error'] or not st['info'] else len(st['info']['cards'])
            lines.append(f"- {S}: no counted card yet (n={n}); {st['error'] or 'nothing to show'}.")
            continue
        cards = st['info']['cards']
        grades = _grades(stores[sport], cards, now)
        buckets = defaultdict(lambda: defaultdict(list))
        for c in cards:
            for p in json.loads(c['picture'])['picks']:
                for item, v in (p.get('h') or {}).items():
                    buckets[item][v].append(grades.get(p['forecast_id']))
        if not st['open']:
            d = st['eval']['named_decided'] if st['eval'] else 0
            lines.append(f"- {S} wins: " + HIDDEN.format(s=S, n=bp.N_EVAL, d=min(d, bp.N_EVAL))
                         + '. H1-H7 are the rule\'s own score on the same picks, so their results would be an interim look.')
        for item, (only, plus, zero, minus, (good, bad)) in H_ITEMS.items():
            if only not in (None, sport):
                continue
            parts = []
            for v, label in ((1, plus), (0, zero), (-1, minus)):
                if label is None:
                    continue
                gs = buckets[item][v]
                dec = [g for g in gs if g in ('win', 'loss')]
                if st['open']:
                    w = sum(g == 'win' for g in dec)
                    parts.append(f'{label}: {_wl(w, len(dec))}, {_rate(w, len(dec))}')
                else:
                    parts.append(f'{label}: picks n={len(gs)}, decided n={len(dec)}')
            verdict = ''
            if st['open']:
                dg = [[g for g in buckets[item][v] if g in ('win', 'loss')] for v in (good, bad)]
                if min(len(x) for x in dg) < MIN_N:
                    verdict = ' -> too few to tell'
                else:
                    rg, rb = (sum(g == 'win' for g in x) / len(x) for x in dg)
                    verdict = f" -> {'holding' if rg > rb else 'failing'} ({100 * (rg - rb):+.1f} pts)"
            lines.append(f"- {S} {item}: " + '; '.join(parts) + verdict)
    return lines + ['']


def section_lost(states: dict, stores: dict, now: datetime) -> list:
    lines = ['## 3. Cards where the named pick lost', '']
    for sport in ('nba', 'nhl'):
        st, S = states[sport], sport.upper()
        if not st['open']:
            d = st['eval']['named_decided'] if st['eval'] else 0
            lines.append(f"- {S}: " + HIDDEN.format(s=S, n=bp.N_EVAL, d=min(d, bp.N_EVAL)) + '.')
            continue
        cards = st['info']['cards'] if st['info'] else []
        grades = _grades(stores[sport], cards, now) if cards else {}
        lost = [c for c in cards if c['named_forecast_id'] is not None and grades.get(c['named_forecast_id']) == 'loss']
        decided = sum(grades.get(c['named_forecast_id']) in ('win', 'loss') for c in cards if c['named_forecast_id'] is not None)
        lines.append(f"- {S}: the named pick lost on n={len(lost)} of n={decided} decided named picks.")
        for c in lost[-MAX_REPEAT_LINES:]:
            pic = json.loads(c['picture'])
            picks = pic['picks']
            named = next(p for p in picks if p['forecast_id'] == c['named_forecast_id'])
            won = [p for p in picks if grades.get(p['forecast_id']) == 'win']
            dec = sum(grades.get(p['forecast_id']) in ('win', 'loss') for p in picks)
            b2b = pic.get('played_previous_day') or {}
            won_txt = '; '.join(f"{p.get('market')} {p.get('pick_label')} app {p.get('app_pct')}% book {p.get('book_pct')}%"
                                f" H {json.dumps(p.get('h') or {}, sort_keys=True)}" for p in won) or 'nothing'
            lines.append(f"  - {c['matchup']} ({(c['tipoff_at'] or '')[:10]}): named {named.get('pick_label')} app "
                         f"{named.get('app_pct')}% book {named.get('book_pct')}% lost. Won on that card ({len(won)} of "
                         f"n={dec} decided picks): {won_txt}. Picture: home played the day before "
                         f"{'yes' if b2b.get('home') else 'no'}, away {'yes' if b2b.get('away') else 'no'}; lineup-flagged "
                         f"picks n={pic.get('excluded_lineup', 0)}, AVOID picks n={pic.get('excluded_avoid', 0)}.")
        if len(lost) > MAX_REPEAT_LINES:
            lines.append(f"  - older cards not listed: n={len(lost) - MAX_REPEAT_LINES}.")
    return lines + ['']


# ---- exact percentages that came up again (scorecard) -------------------------------------------------------------------
KEY = ('market', 'displayed_pct', 'side', 'source', 'model_version', 'line_source')


def section_repeats(data_dir: Path, states: dict, now: datetime) -> list:
    as_of, week = now.strftime(FMT), now - timedelta(days=7)
    lines = ['## 4. Exact percentages that came up again', '',
             f'Same exact displayed % (one decimal), side, model and line source; no ranges. Listed when a pick at that '
             f'exact % was decided in the last 7 days and the % has 2+ decided picks. "Too few to tell" below n={MIN_N}.', '']
    for sport in SPORTS:
        S = sport.upper()
        if not sr_path(data_dir, sport).is_file():
            lines.append(f'- {S}: scorecard file missing, nothing to read (n=0).')
            continue
        try:
            rows, without = sr.graded_rows(data_dir, sport, as_of)
        except sqlite3.Error as exc:
            lines.append(f'- {S}: scorecard not readable ({type(exc).__name__}) (n=0).')
            continue
        if any(without.values()):
            lines.append(f'- {S}: decided picks with no scorecard row, left out: n={sum(without.values())}.')
        st, held_n = states.get(sport) or {}, 0
        if sport in bpr.RULE_MARKETS and not st.get('open'):
            held = st.get('held')
            keep = []
            for r in rows:
                if (r['game_id'] in held) if held is not None else _t(r['tipoff_at']) > _t(bpr.T0):
                    held_n += 1
                else:
                    keep.append(r)
            rows = keep
            games = (f'games with a recorded named or comparison pick: games n={len(held)}' if held is not None
                     else 'ledger not readable, so every game after T0')
            lines.append(f'- {S}: held back while the best-pick test runs, every market of {games}; picks n={held_n}.')
        groups = defaultdict(list)
        for r in rows:
            groups[tuple(r.get(k) for k in KEY)].append(r)
        shown = [(k, rs) for k, rs in groups.items()
                 if len(rs) >= 2 and any(week <= _t(r['tipoff_at']) <= now for r in rs)]
        shown.sort(key=lambda kv: (kv[0][0], -len(kv[1]), float(kv[0][1])))
        lines.append(f'- {S}: decided picks with a scorecard row n={len(rows)}; exact percentages repeated this week '
                     f'n={len(shown)}.')
        by_market = defaultdict(list)
        for k, rs in shown:
            by_market[k[0]].append((k, rs))
        listed = []
        for market in sorted(by_market):
            listed += by_market[market][:MAX_REPEAT_LINES]
            if len(by_market[market]) > MAX_REPEAT_LINES:
                listed.append((market, len(by_market[market]) - MAX_REPEAT_LINES))
        for item in listed:
            if isinstance(item[1], int):
                lines.append(f'  - {S} {item[0]}: more repeated percentages not listed: n={item[1]}.')
                continue
            (market, p, side, source, model, line_source), rs = item
            w = sum(r['grade'] == 'win' for r in rs)
            wk = [r for r in rs if week <= _t(r['tipoff_at']) <= now]
            ww = sum(r['grade'] == 'win' for r in wk)
            lines.append(f'  - {S} {market} {p}% side {side} (model {_model(model)}, line {line_source}): {_wl(w, len(rs))}, '
                         f'{_rate(w, len(rs))}; this week {_wl(ww, len(wk))}.')
    return lines + ['']


# ---- learning (read-only: learning_coverage, the challenger store, the adaptive fit tables) -----------------------------------
def _challenger_counts(data_dir: Path, stores: dict) -> list:
    path = Path(data_dir) / 'research' / 'challengers.sqlite'
    cc = learning_challengers.open_ro(path)
    if cc is None:
        return ['- Challenger store: no challenger store in this data folder, rows recorded n=0 for every sport.']
    lines = []
    try:
        for sport in learning_challengers.SPORTS:
            pols = list(cc.execute("SELECT policy_id, COUNT(*) AS n, COUNT(DISTINCT game_id) AS g, COUNT(DISTINCT spec_name) AS s "
                                   "FROM challenger_forecasts WHERE policy_id LIKE ? GROUP BY policy_id ORDER BY policy_id", (f'{sport}-%',)))
            scored, claims = {}, set()
            store = stores.get(sport)
            if pols and store is not None:
                try:
                    out = learning_eval.evaluate(store, cc, f'{sport}-')
                    for g in out['groups']:
                        if not g['policy_id'].startswith(f'{sport}-'):      # a claim never leaks across sports
                            continue
                        scored[g['policy_id']] = scored.get(g['policy_id'], 0) + g['n_scored']
                        claims.add('no claim' if g['claim'].startswith('no claim') else 'sample size reached: preregistered rule applies')
                except Exception as exc:                                    # the report never dies on the learning section
                    claims.add(f'scoring unavailable ({type(exc).__name__}: {str(exc)[:80]})')
            for r in pols:
                lines.append(f"- {r['policy_id']}: rows recorded n={r['n']}; games n={r['g']}; specs n={r['s']}; settled and scored "
                             f"n={scored.get(r['policy_id'], 0)} (paired with the served forecast); "
                             f"{'; '.join(sorted(claims)) or 'no claim: scoring needs the sport store'} until a preregistered sample size is reached.")
            if not pols:
                lines.append(f"- {sport.upper()}: rows recorded n=0 (no {sport} challenger policy has recorded yet).")
    except Exception as exc:
        lines.append(f'- Challenger store not readable ({type(exc).__name__}: {str(exc)[:80]}).')
    finally:
        cc.close()
    return lines


def _adaptive_fits(stores: dict) -> list:
    lines = []
    for sport in ('nba', 'nhl'):
        S, store = sport.upper(), stores.get(sport)
        if store is None:
            lines.append(f'- {S} adaptive fits: store not readable (n=unknown).')
            continue
        try:
            rows = list(store.execute("SELECT status, COUNT(*), MAX(finished_at) FROM adaptive_fits WHERE kind='combiner' GROUP BY status ORDER BY status"))
        except sqlite3.Error as exc:
            lines.append(f'- {S} adaptive fits: not readable ({type(exc).__name__}) (n=unknown).')
            continue
        lines.append(f"- {S} adaptive fits (combiner): " + ('; '.join(f'{r[0]} n={r[1]} (latest {r[2] or "none"})' for r in rows) or 'none recorded n=0') + '.')
    return lines


def section_learning(data_dir: Path, stores: dict) -> list:
    lines = ['## 5. Learning', '',
             'Read-only counts of what is implemented, recorded and settled. Challengers are recorded before the game and scored '
             'against the served forecast at read time; registration is not promotion and nothing here claims improvement. No '
             'profit claim follows from anything here, and no difference between a challenger and the served forecast is shown: '
             'no claim until a preregistered sample size is reached.', '']
    try:
        cov = learning_coverage.coverage(data_dir)
    except Exception as exc:
        lines.append(f'- learning coverage unavailable: {type(exc).__name__}: {str(exc)[:120]} (n=unknown).')
    else:
        for sport in SPORTS:
            rows = [r for r in cov['rows'] if r['sport'] == sport]
            parts = [f"{label} n={sum(r[key]['state'] == 'yes' for r in rows)} of n={len(rows)}"
                     for key, label in (('trained', 'trained'), ('scheduled', 'scheduled'), ('recording_prospectively', 'recording rows'),
                                        ('forecasting_with_fits', 'forecasting with fits'), ('evaluated', 'with settled forecasts'))]
            lines.append(f"- {sport.upper()}: markets with " + ', '.join(parts) + f"; learning implemented: {'yes' if rows and rows[0]['learning_implemented'] else 'no'}.")
    lines += _adaptive_fits(stores) + _challenger_counts(data_dir, stores) + _autoresearch_spend()
    return lines + ['']


def _autoresearch_spend(now=None) -> list:
    """Autoresearch spend: budget-ledger.jsonl and runs.jsonl against the owner budget. Read-only; counts, no claims."""
    lines = ['', '### Autoresearch spend', '']
    try:
        x = autoresearch_budget.summary(autoresearch_budget.home(), now or datetime.now(timezone.utc))
    except Exception as exc:                                     # no budget file, or cost tracking unreadable: said, not hidden
        return lines + [f'- autoresearch spend unavailable: {str(exc)[:160]} (n=unknown).']
    d = x['decisions']
    lines += [f"- this week: ${x['week_spend']:.2f} of ${x['period_usd_cap']:.2f} cap; runs {x['week_runs']} of {x['max_runs_per_period']}; "
              f"total runs {x['total_runs']} of {x['max_total_runs']} (an owner assessment is required after {x['max_total_runs']}).",
              f"- trials completed n={x['trials']}; market decisions keep n={d['keep']}, discard n={d['discard']}, other n={d['other']}.",
              f"- model requested {x['model_requested']}; model used {', '.join(x['models']) or 'none recorded'}"
              + ('; MODEL MISMATCH STOP is set, runs are blocked until the owner reviews.' if x['stop_marker'] else '.')]
    lines += [f'- run {rid}: {out}.' for rid, out in x['outcomes']] or ['- no run this week.']
    return lines + ['A run that finds nothing kept is a valid result; nothing here is a promotion.']


def _model(version) -> str:
    """Short model label: family plus a 6-character tag of the full version (different versions stay apart)."""
    if not version:
        return 'unknown'
    import hashlib
    return f"{str(version).split('-')[0].split(':')[0]} #{hashlib.sha256(str(version).encode()).hexdigest()[:6]}"


def sr_path(data_dir: Path, sport: str) -> Path:
    return Path(data_dir) / 'scorecard' / f'{sport}.sqlite'


# ---- page ---------------------------------------------------------------------------------------------------------------
def build(data_dir: Path, ledger_path, now: datetime, reviewed_at, reviewed) -> str:
    ledger = None
    try:
        if ledger_path and Path(ledger_path).is_file():
            ledger = _ro(ledger_path)
            ledger.execute('SELECT 1 FROM deliveries LIMIT 1')
    except sqlite3.Error:
        ledger = None
    stores = {}
    for sport in ('nba', 'nhl', 'ncaab', 'nfl', 'soccer'):
        p = Path(data_dir) / f'{sport}.sqlite'
        stores[sport] = _ro(p) if p.is_file() else None
    states = {s: test_state(ledger, stores.get(s), s, now, reviewed_at, reviewed) for s in ('nba', 'nhl', 'ncaab', 'nfl')}
    states['soccer'] = {'open': True, 'error': None, 'info': None, 'held': set()}
    closed = [s.upper() for s in ('nba', 'nhl') if not states[s]['open']]
    missing = [s.upper() for s in SPORTS if not sr_path(data_dir, s).is_file()]
    head = [f"# Weekly report, week to {now:%Y-%m-%d}", '',
            f"Made {now.strftime(FMT)} from read-only copies of the stores, scorecard and ledger. Nothing was changed or sent.",
            '', '## Limits first', '',
            '- Win counts and sample sizes only. No profit claim follows from anything here.',
            f'- A preregistered best-pick test is running (v4, T0 {bpr.T0}). No interim looks: until a sport has '
            f'{bp.N_EVAL} decided named picks, sections 1-3 show counts only'
            + (f" (still closed: {', '.join(closed)})." if closed else ' (all evaluations complete).'),
            '- Why hypotheses are hidden too: H1-H7 are the rule\'s own score, counted on the same cards and picks the test '
            'grades; their win counts on new games would show how the test is going.',
            '- Exact-percentage records leave out every market of every game whose card has a named or comparison pick '
            'until that test is complete; the numbers held back are shown.',
            f'- "Too few to tell" whenever a side has fewer than {MIN_N} decided picks (scorecard report threshold). '
            'Patterns from old games are candidates, not facts.',
            '- Counted cards need an independent review of their recorder and exact code commit; review times per entry in '
            f"{REVIEW_SOURCE['path']}."]
    if REVIEW_SOURCE['invalid']:
        head.append(f"- {REVIEW_SOURCE['invalid']}: no card counts until the review config is fixed.")
    if REVIEW_SOURCE['unavailable']:
        head.append(f"- review config unavailable: {REVIEW_SOURCE['unavailable']} (missing or unreadable): no card counts until it is fixed.")
    if missing:
        head.append(f"- Scorecard file missing for: {', '.join(missing)} (section 4 has nothing for them).")
    if ledger is None:
        head.append('- Telegram ledger not readable: test counts unknown; section 4 holds back every NBA/NHL pick of a '
                    'game after T0.')
    body = (section_best(states) + section_hypotheses(states, stores, now) + section_lost(states, stores, now)
            + section_repeats(data_dir, states, now) + section_learning(data_dir, stores))
    for c in [ledger, *stores.values()]:
        if c is not None:
            c.close()
    return '\n'.join(head + [''] + body).rstrip() + '\n'


def main(argv=None) -> str:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--data-dir', help='data folder (default: NBA_DATA_DIR)')
    ap.add_argument('--ledger', required=True, help='Telegram ledger (opened read-only)')
    ap.add_argument('--out-dir', help='where the page goes (default <data>/reports)')
    ap.add_argument('--reviewed-at', help="UTC time the recorder's independent review was written in HANDOFF.md")
    ap.add_argument('--reviewed-recorder', action='append', default=[], help='recorder_sha256 of a reviewed recorder')
    ap.add_argument('--reviewed-commit', help='the exact (40 hex) code commit the override covers; required with the two options above')
    ap.add_argument('--review-config', default=str(REVIEW_CONFIG),
                    help='JSON {"reviews": [{recorder_sha256, code_commit, reviewed_at, ...}]} used when the three options above are not given')
    ap.add_argument('--now', help='report time (default: now, UTC)')
    a = ap.parse_args(argv)
    REVIEW_SOURCE.update(path=a.review_config, unavailable=None, invalid=None)
    if a.reviewed_at is None and not a.reviewed_recorder:
        try:
            if not Path(a.review_config).is_file():
                raise OSError('missing')
            cfg_at, cfg_sha = review_defaults(a.review_config)
        except ReviewConfigError as exc:
            cfg_at, cfg_sha = None, {}               # fail closed: nothing counts, and the page names the entry
            REVIEW_SOURCE['invalid'] = str(exc)
        except (OSError, ValueError, KeyError, TypeError):
            cfg_at, cfg_sha = None, {}
            REVIEW_SOURCE['unavailable'] = a.review_config
        a.reviewed_at, a.reviewed_recorder = cfg_at, cfg_sha
    else:                                            # the single-pair override still names the exact code commit
        if not (a.reviewed_at and a.reviewed_recorder and a.reviewed_commit):
            ap.error('--reviewed-at, --reviewed-recorder and --reviewed-commit go together')
        shas, a.reviewed_recorder = list(a.reviewed_recorder), {}
        try:
            for sha in shas:
                a.reviewed_recorder.setdefault(sha, []).append(check_entry(0, {'recorder_sha256': sha, 'reviewed_at': a.reviewed_at,
                                                                               'code_commit': a.reviewed_commit}))
        except ReviewConfigError as exc:
            ap.error(str(exc))
    data_dir = Path(a.data_dir) if a.data_dir else config.data_dir()
    if config.protected_data_dir(data_dir):
        from app import release
        # Only a report file is written, never a store: the checkout checks apply, the store-schema check does not.
        if not release.enabled() or release._checkout(release.code_root().resolve())[1]:
            raise SystemExit('release guard: the weekly report on live data requires the clean release checkout')
    out_dir = check_out_dir(a.out_dir or Path(data_dir) / 'reports')
    now = _t(a.now) if a.now else datetime.now(timezone.utc).replace(microsecond=0)
    text = build(data_dir, a.ledger, now, a.reviewed_at, a.reviewed_recorder if isinstance(a.reviewed_recorder, dict) else set(a.reviewed_recorder))
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f'weekly-{now:%Y-%m-%d}.md'
    tmp = target.with_suffix('.md.tmp')
    tmp.write_text(text)
    os.replace(tmp, target)
    print(target)
    return str(target)


if __name__ == '__main__':
    main()
    raise SystemExit(0)
