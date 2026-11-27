"""Prospective NCAAB challengers: refits of the FROZEN NCAAB parameters, recorded before tip-off and scored at read time.

NCAAB serving is frozen (models/ncaab_frozen_decision.json). Nothing here edits, refits or replaces it: the served rating
state (college.state at the cycle's clock) supplies each game's means, exactly as the served forecast reads them, and only
the calibration and the sigmas applied to those means are refit. Variants (reference/learning_policies/ncaab-challengers-v2.json; v1, priced at the recorder's own clock, is retired):

* C1  winner (sigma + Platt), spread (sigma), total (running_points, sigma) refit weekly; fitted WITHOUT the spent test seasons.
* C2  as C1 but fitted on every season with results (the owner allows fitting on the spent test seasons; evidence still
      comes only from the rows recorded here, before tip-off).
* C3  the total priced by the existing college_eff joint-normal total (its sigma refit as C1): the one alternative total
      model already in the codebase (college.market_moments); no new model family.
* C4  team-total sigmas (home and away, derived from the college_eff margin/total sigmas and their correlation) refit as C1.

Point in time: each (game, market) is priced AS OF the information time of the published bar it is compared with (the latest
run of that market in game_forecasts by start - 60 min, information time min(source_generated_at, generated_at)), at the bar's own
line, with cutoff_at = that time; the rating state is college.state at that time, and a fit uses only results with
result_available_at <= the start (Monday 00:00 UTC) of that time's ISO week, walked forward (backtest.run, each game predicted from
earlier results only), so it is cacheable per week and never newer than the bar. The cycle is the served NCAAB near-tipoff one:
games tipping off within two hours of `now`, accepted by near_tipoff.predict_now; a game whose bar is not yet fixed (before
start - 60 min) or that has none is skipped with a reason and retried. Rows go to the
challenger store only (learning_challengers); game_forecasts, market_forecasts, candidate_forecasts and the card tables are
never written, and the sport store is opened read-only.

  python -m app.learning_challengers record --sport ncaab [--dry-run] [--now T --store PATH]
"""
import hashlib
import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import backtest, learning_challengers as lc, market_live, near_tipoff, ncaab_app
from .db import utc_now
from .models import college

MIN_FIT_GAMES = 2000                       # fewer fit games than this: the variant is refused, never a made-up fit
FIT_SEASONS = ("exclude_test", "all")
MARKETS = ("winner", "spread", "total", "team_total_home", "team_total_away")
TOTAL_MODELS = ("running_points", "college_eff")
FAMILY = "frozen_refit"
FMT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class Spec:
    name: str
    sport: str
    family: str
    head: str
    features: str
    role: str
    why: str
    refit: str = "week"
    extra: Optional[dict] = None


def spec(raw: dict, sport: str = "ncaab") -> Spec:
    """Validated NCAAB challenger spec: extra = {markets, fit_seasons, total_model}."""
    if not isinstance(raw, dict) or set(raw) - set(Spec.__dataclass_fields__) or \
            not {"name", "sport", "family", "head", "features", "role", "why", "extra"} <= set(raw):
        raise lc.PolicyError(f"spec fields must be within {sorted(Spec.__dataclass_fields__)} and include extra")
    if raw["sport"] != sport:
        raise lc.PolicyError(f"spec {raw['name']}: sport {raw['sport']!r} is not the policy's sport {sport!r}")
    if raw["family"] != FAMILY:
        raise lc.PolicyError(f"spec {raw['name']}: family must be {FAMILY!r}")
    if raw.get("refit", "week") != "week":
        raise lc.PolicyError(f"spec {raw['name']}: only the weekly refit is implemented")
    x = raw["extra"]
    if not isinstance(x, dict) or set(x) - {"markets", "fit_seasons", "total_model"}:
        raise lc.PolicyError(f"spec {raw['name']}: extra must hold markets, fit_seasons and optionally total_model")
    if not isinstance(x.get("markets"), list) or not x["markets"] or any(m not in MARKETS for m in x["markets"]):
        raise lc.PolicyError(f"spec {raw['name']}: markets must be a non-empty list drawn from {MARKETS} (unknown market)")
    if x.get("fit_seasons") not in FIT_SEASONS:
        raise lc.PolicyError(f"spec {raw['name']}: fit_seasons must be one of {FIT_SEASONS}")
    if x.get("total_model", "running_points") not in TOTAL_MODELS:
        raise lc.PolicyError(f"spec {raw['name']}: total_model must be one of {TOTAL_MODELS}")
    return Spec(**raw)


# --- Fit ------------------------------------------------------------------------------------------------------------
def week_start(now: str) -> str:
    d = datetime.strptime(now, FMT).replace(tzinfo=timezone.utc)
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%dT00:00:00Z")


def _rms(xs: list) -> float:
    return math.sqrt(sum(x * x for x in xs) / len(xs))


def _walk_fit(rows: list, box: dict, d: dict) -> dict:
    """{fit_seasons: {n, seasons, cutoff-free fit}} from walk-forward predictions of the frozen model configs over `rows`."""
    from . import ncaab_eval
    scores = {r.game.game_id: (r.home_score, r.away_score) for r in rows}
    eff = backtest.run(college.CollegeEff(college.CollegeEffConfig(**d["college_eff_config"]), box), rows,
                       extra=lambda m, g: dict(zip(("mu_h", "mu_a"), m.expected(g))))
    pts = backtest.run(college.RunningPoints(), rows, extra=lambda m, g: dict(zip(("pts_h", "pts_a"), m.expected(g))))
    test = set(ncaab_app.PERIODS["test"])
    burn = set(ncaab_app.PERIODS["warmup"])
    out = {}
    for which, keep in (("exclude_test", lambda s: s not in burn and s not in test), ("all", lambda s: s not in burn)):
        e = [{**p, "home_score": scores[p["game_id"]][0], "away_score": scores[p["game_id"]][1]} for p in eff if keep(p["season"])]
        q = [{**p, "home_score": scores[p["game_id"]][0], "away_score": scores[p["game_id"]][1]} for p in pts if keep(p["season"])]
        if len(e) < MIN_FIT_GAMES:
            out[which] = {"n": len(e), "seasons": sorted({p["season"] for p in e}), "refused": f"only {len(e)} fit games, fewer than {MIN_FIT_GAMES}"}
            continue
        rm = [(p["home_score"] - p["away_score"]) - (p["mu_h"] - p["mu_a"]) for p in e]
        rt = [(p["home_score"] + p["away_score"]) - (p["mu_h"] + p["mu_a"]) for p in e]
        sm, st = _rms(rm), _rms(rt)
        sigmas = {"eff_margin": sm, "eff_total": st, "eff_rho": sum(a * b for a, b in zip(rm, rt)) / (len(rm) * sm * st),
                  "points_total": _rms([(p["home_score"] + p["away_score"]) - (p["pts_h"] + p["pts_a"]) for p in q]),
                  "points_home": _rms([p["home_score"] - p["pts_h"] for p in q]),
                  "points_away": _rms([p["away_score"] - p["pts_a"] for p in q])}
        cal = ncaab_eval.fit_platt([{"p_home": college._phi((p["mu_h"] - p["mu_a"]) / sm), "home_won": p["home_score"] > p["away_score"]}
                                    for p in e])
        out[which] = {"n": len(e), "seasons": sorted({p["season"] for p in e}), "sigmas": sigmas, "calibration": cal}
    return out


def fit_code_version() -> str:
    """Digest of everything the cached fit depends on besides the data: this module, the models it walks, the backtest walk,
    the Platt fit, and the thresholds (MIN_FIT_GAMES, the season periods)."""
    h = hashlib.sha256(json.dumps([MIN_FIT_GAMES, {k: list(v) for k, v in sorted(ncaab_app.PERIODS.items())}]).encode())
    for f in (__file__, college.__file__, backtest.__file__, *(Path(__file__).with_name(n) for n in ("ncaab_eval.py", "ncaab.py", "ncaab_app.py")),
              Path(college.__file__).with_name("base.py")):
        h.update(Path(f).read_bytes())
    return h.hexdigest()[:8]


def refit(conn: sqlite3.Connection, now: str, cache_dir: Path, *, write_cache: bool) -> tuple:
    """(fits by fit_seasons, cache 'hit'|'miss', fingerprint). Rows are the eligible finals with result_available_at <= the
    week's start; the cache file is keyed by that week and the data fingerprint (rows hash + the frozen decision's digest)."""
    cutoff = week_start(now)
    d = college.load_decision()
    rows, box = college.rows_at(conn, cutoff)
    box_hash = hashlib.sha256(json.dumps(sorted(box.items()), default=str).encode()).hexdigest()[:8]     # the FGA/FTA CollegeEff reads
    fingerprint = f"{college.rows_version(rows)[:12]}-{box_hash}-{fit_code_version()}-{d['sha256'][:8]}"
    path = Path(cache_dir) / f"refit-{cutoff[:10]}-{fingerprint}.json"
    if path.is_file():
        try:
            c = json.loads(path.read_text())
            if c.get("fingerprint") == fingerprint and c.get("cutoff") == cutoff and isinstance(c.get("fits"), dict):
                return c["fits"], "hit", fingerprint, cutoff
        except (ValueError, AttributeError):
            pass
    fits = _walk_fit(rows, box, d)
    if write_cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(json.dumps({"cutoff": cutoff, "fingerprint": fingerprint, "fits": fits}, sort_keys=True))
        tmp.replace(path)
        _prune_cache(path.parent, cutoff)
    return fits, "miss", fingerprint, cutoff


def _prune_cache(cache_dir: Path, cutoff: str) -> None:
    """Keeps this week's and the previous week's refit files (any fingerprint); removes older weeks and abandoned .tmp<pid> files."""
    keep_from = (datetime.strptime(cutoff[:10], "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    for f in cache_dir.glob("refit-*.json"):
        if f.name[len("refit-"):len("refit-") + 10] < keep_from:
            f.unlink(missing_ok=True)
    for f in cache_dir.glob("refit-*.tmp*"):
        if time.time() - f.stat().st_mtime > 3600:
            f.unlink(missing_ok=True)


# --- Pricing --------------------------------------------------------------------------------------------------------
def price(sp: Spec, market: str, line: Optional[float], means: dict, sig: dict, cal) -> Optional[tuple]:
    """(a, push, b) of one market at the served line from the served means and the refit sigmas; None if not this spec's market."""
    if market not in sp.extra["markets"]:
        return None
    if market == "winner":
        mh, ma = means["eff"]
        p = float(college.platt(college._phi((mh - ma) / sig["eff_margin"]), cal))
        return p, 0.0, 1.0 - p
    kind = "college_eff"
    if market == "total":
        kind = sp.extra.get("total_model", "running_points")
    if market == "spread":
        kind = "college_eff"
    mean, sd = college.market_moments(kind, means, sig, market)
    return college.market_probs(market, float(line), mean, sd)


def _t(at: str) -> datetime:
    return datetime.strptime(at, FMT).replace(tzinfo=timezone.utc)


def _bars(conn, accepted: list, now: str, out: dict) -> dict:
    """{game_id: {market: (information_at, line)}}: the published bar of each (game, market) under FORWARD_PAIRING, from the canonical
    ledger (game_forecasts, status 'forecast', the game's current start): the latest run of THAT market made by start - as_of_minutes,
    with its information time min(source_generated_at, generated_at) (as in learning_eval). Markets move independently (a ledger row
    is added only when a market's content changes), so each is priced at its own bar. A game whose bar is not yet fixed (now is before
    start - as_of_minutes), with no published run by then, or whose market bar is more than two hours before the start (the store's
    window trigger) is skipped with a reason and retried next cycle. Times are compared parsed."""
    from .autoresearch import FORWARD_PAIRING
    asof_min = timedelta(minutes=FORWARD_PAIRING["as_of_minutes"])
    bars = {}
    for gid in accepted:
        tip = conn.execute("SELECT tipoff_at FROM games WHERE game_id=?", (gid,)).fetchone()[0]
        start = _t(tip)
        asof = start - asof_min
        if _t(now) < asof:
            out["skipped"][gid] = f"bar_not_fixed: the published bar is the latest run by {asof.strftime(FMT)}"
            continue
        latest = {}
        try:
            for market, line, gen, src in conn.execute(
                    "SELECT market, line, generated_at, source_generated_at FROM game_forecasts WHERE game_id=? AND status='forecast' "
                    "AND tipoff_at=? ORDER BY generated_at, forecast_id", (gid, tip)):
                if _t(gen) <= asof:
                    latest[market] = (min(_t(src or gen), _t(gen)), line)
        except ValueError as exc:                      # one unreadable time skips this game, never the cycle
            out["skipped"][gid] = f"bar_unreadable_time: {exc}"
            continue
        if not latest:
            out["skipped"][gid] = "no_published_bar"
            continue
        for market, (info, line) in sorted(latest.items()):
            if info < start - timedelta(hours=2):
                out["skipped"][f"{gid}:{market}"] = f"bar_outside_window: information time {info.strftime(FMT)} is more than two hours before the start"
            else:
                bars.setdefault(gid, {})[market] = (info.strftime(FMT), line)
    return bars


def record(conn, path: Path, *, policies: list, now: Optional[str] = None, dry_run: bool = False,
           cache_dir: Optional[Path] = None) -> dict:
    """Challenger rows for NCAAB games tipping off within two hours of `now`, each (game, market) priced AS OF the information time of
    the published bar it is compared with (never later): results, ratings and refit available at that instant, the bar's own line,
    cutoff_at = that time. `conn` is the NCAAB store, used read-only. A failure in one information-time group is recorded and the
    next group still runs."""
    now = now or utc_now()
    cache_dir = Path(cache_dir) if cache_dir else Path(path).parent / "challengers" / "ncaab"
    out = {"status": "success", "dry_run": dry_run, "now": now, "created": 0, "would_record": 0, "already_recorded": 0,
           "eligible_games": 0, "skipped": {}, "errors": {}, "fits": {}, "fits_by_group": {}, "fits_cache": None,
           "lines_from": {"published": 0}, "policies": [{"policy_id": p.policy_id, "sha": p.sha} for p in policies]}
    try:
        lc.check_unique_names(policies)
        near = near_tipoff.predict_now(conn, now=now, write=False)
        accepted = [f["game_id"] for f in near["forecasts"]]
        out["eligible_games"] = len(accepted)
        out["refused_by_served_cycle"] = [{k: r[k] for k in ("game_id", "reason")} for r in near["refused"]]
        bars = _bars(conn, accepted, now, out)
    except Exception as exc:
        out["status"], out["errors"]["recorder"] = "failed", f"{type(exc).__name__}: {exc}"
        return out
    by_info: dict = {}
    for gid, markets in bars.items():
        for market, (info, line) in markets.items():
            by_info.setdefault(info, {}).setdefault(gid, []).append((market, line))
    caches, failed = [], 0
    for info, group in sorted(by_info.items()):
        try:
            fits, cache, fingerprint, cutoff = refit(conn, info, cache_dir, write_cache=not dry_run)
            st = college.state(conn, info)
        except Exception as exc:
            out["errors"][f"group:{info}"] = f"{type(exc).__name__}: {exc}"
            failed += 1
            continue
        caches.append(cache)
        summary = {k: {"n": v["n"], "seasons": v["seasons"], "cutoff": cutoff, **({"sigmas": v["sigmas"], "calibration": v["calibration"]} if "sigmas" in v else {"refused": v["refused"]})}
                   for k, v in fits.items()}
        out["fits_by_group"][info] = summary
        out["fits"] = out["fits"] or summary
        games = {}
        for gid in group:
            try:
                g = college._pregame(conn, gid, info)
                games[gid] = ({"eff": st.eff.expected(g), "points": st.points.expected(g)}, g.tipoff_at)
            except Exception as exc:
                out["errors"][gid] = f"{type(exc).__name__}: {exc}"
        lines = [(gid, m, ln) for gid in games for m, ln in group[gid]]
        out["lines_from"]["published"] += len(lines)
        for pol in policies:
            for c in pol.challengers:
                sp, fit = c.spec, fits[c.spec.extra["fit_seasons"]]
                if "sigmas" not in fit:
                    out["skipped"][sp.name] = f"fit refused: {fit['refused']}"
                    continue
                sig = {**st.decision["sigmas"], **fit["sigmas"]}
                definition = f"{st.decision['sha256'][:8]}+refit={cutoff[:10]}-{fingerprint}-{sp.extra['fit_seasons']}"
                rows = []
                for gid, market, line in lines:
                    try:
                        p = price(sp, market, line, games[gid][0], sig, fit["calibration"])
                    except Exception as exc:
                        out["errors"][f"{sp.name}:{gid}:{market}"] = f"{type(exc).__name__}: {exc}"
                        continue
                    if p is None:
                        continue
                    pa, pm = round(p[0], 9), round(p[1], 9)
                    rows.append({"game_id": gid, "market": market, "line": line, "p": (pa, pm, round(1 - pa - pm, 9)),
                                 "generated_at": info, "cutoff_at": info, "tipoff_at": games[gid][1]})
                lc.append_rows(path, pol, c, rows, definition=definition, dataset_version=fingerprint, now=now,
                               dry_run=dry_run, out=out)
    if by_info and failed == len(by_info):
        out["status"] = "failed"
    out["fits_cache"] = None if not caches else "hit" if all(x == "hit" for x in caches) else "miss"
    if out["status"] == "success" and (out["errors"] or out["skipped"]):
        out["status"] = "partial"
    return out
