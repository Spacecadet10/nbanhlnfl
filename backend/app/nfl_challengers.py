"""Prospective NFL weekly challengers: variants of parameters that ALREADY exist as configurable values in the NFL code.

The served NFL path (nfl_live, nfl_eval, coverage, markets, models/*) is never edited; this module only reads from it. Variants
(reference/learning_policies/nfl-challengers-v1.json) change ONE existing parameter of the sealed activation's learners:

* elo     winner Elo: k or home_advantage, a value from nfl_eval.GRID (carry and the margin multiplier stay as sealed).
* points  score model behind spread, total and the team totals: eta or carry, a value from nfl_eval.POINTS_GRID.

Recorded at the served cutoff (kickoff minus 120 minutes, state from results available by then, walked forward exactly as
nfl_live.record does) into the challenger store, at the line of the served forecast row (so every row pairs with an incumbent);
a game without its served forecast is skipped until it exists. The sport store is opened read-only and the served tables are never
written. At about 17 games a week, evidence this season stays far below any preregistered sample size: no claim.

  python -m app.learning_challengers record --sport nfl [--dry-run] [--now T --store PATH]
"""
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from . import learning_challengers as lc, nfl_eval, nfl_live
from .db import utc_now
from .models.elo import EloConfig
from .models.points import PointsConfig

FAMILY = "nfl_param"
ALLOWED = {"elo": ("k", "home_advantage"), "points": ("eta", "carry")}
POINT_MARKETS = ("spread", "total", "team_total_home", "team_total_away")
ORDER_VERSION = "nfl-live-v1"


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


def spec(raw: dict, sport: str = "nfl") -> Spec:
    if not isinstance(raw, dict) or set(raw) - set(Spec.__dataclass_fields__) or \
            not {"name", "sport", "family", "head", "features", "role", "why", "extra"} <= set(raw):
        raise lc.PolicyError(f"spec fields must be within {sorted(Spec.__dataclass_fields__)} and include extra")
    if raw["sport"] != sport:
        raise lc.PolicyError(f"spec {raw['name']}: sport {raw['sport']!r} is not the policy's sport {sport!r}")
    if raw["family"] != FAMILY:
        raise lc.PolicyError(f"spec {raw['name']}: family must be {FAMILY!r}")
    x = raw["extra"]
    if not isinstance(x, dict) or set(x) - set(ALLOWED):
        raise lc.PolicyError(f"spec {raw['name']}: extra may hold only {tuple(ALLOWED)} (unknown extra)")
    if len(x) != 1:
        raise lc.PolicyError(f"spec {raw['name']}: extra must hold exactly one of {tuple(ALLOWED)}")
    head, params = next(iter(x.items()))
    grid = nfl_eval.GRID if head == "elo" else nfl_eval.POINTS_GRID
    if not isinstance(params, dict) or not params or set(params) - set(ALLOWED[head]):
        raise lc.PolicyError(f"spec {raw['name']}: {head} may vary only {ALLOWED[head]}")
    for k, v in params.items():
        if v not in grid[k]:
            raise lc.PolicyError(f"spec {raw['name']}: {head} {k}={v!r} is not in the existing grid {grid[k]}")
    return Spec(**raw)


def _model(sp: Spec, sealed: dict):
    head, params = next(iter(sp.extra.items()))
    if head == "elo":
        return nfl_eval.NflElo(replace(EloConfig(**sealed["config"]), **params))
    sm = sealed["line_markets"]["score_model"]
    return nfl_eval.NflPoints(replace(PointsConfig(eta=sm["eta"], carry=sm["carry"]), **params))


def record(conn, path: Path, *, policies: list, now: Optional[str] = None, dry_run: bool = False) -> dict:
    """Challenger rows for scheduled games past their served cutoff (kickoff - 120 min) and before kickoff."""
    now = now or utc_now()
    out = {"status": "success", "dry_run": dry_run, "now": now, "created": 0, "would_record": 0, "already_recorded": 0,
           "eligible_games": 0, "skipped": {}, "errors": {}, "policies": [{"policy_id": p.policy_id, "sha": p.sha} for p in policies]}
    try:
        lc.check_unique_names(policies)
        a = nfl_live.activation()
        if not a:
            out["status"], out["errors"]["recorder"] = "failed", "NFL live activation is missing or its definition changed"
            return out
        s = a["sealed"]
        games = [g for g in conn.execute("SELECT * FROM games WHERE status='scheduled' AND season_type IN ('regular','postseason') "
                                         "AND tipoff_at>? ORDER BY tipoff_at, game_id", (now,))
                 if nfl_eval.cutoff_for(g["tipoff_at"]) <= now]
        out["eligible_games"] = len(games)
        served = {}
        for g in games:
            rows = {r["market"]: r for r in conn.execute(
                "SELECT market, line FROM game_forecasts WHERE game_id=? AND tipoff_at=? AND status='forecast' AND order_version=?",
                (g["game_id"], g["tipoff_at"], ORDER_VERSION))}
            if rows:
                served[g["game_id"]] = rows
            else:
                out["skipped"][g["game_id"]] = "no served forecast yet: nothing to pair with"
        due = [g for g in games if g["game_id"] in served]
        if not due:
            return out
        results = nfl_eval.load_rows(conn, through=max(g["season"] for g in due), sealed=s, access_log=nfl_eval.access_log_path(),
                                     purpose="prospective NFL challenger state initialization")
        results = sorted(results, key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id))
    except Exception as exc:
        out["status"], out["errors"]["recorder"] = "failed", f"{type(exc).__name__}: {exc}"
        return out
    for pol in policies:
        for c in pol.challengers:
            head = next(iter(c.spec.extra))
            if head == "elo" and s["model"] != "elo":
                out["skipped"][c.spec.name] = "the sealed winner model is not Elo"
                continue
            try:
                model, i, rows = _model(c.spec, s), 0, []
                for g in due:
                    cutoff = nfl_eval.cutoff_for(g["tipoff_at"])
                    while i < len(results) and results[i].result_available_at <= cutoff:
                        model.update(results[i].result)
                        i += 1
                    pg = nfl_live._game(g)
                    if head == "elo":
                        p = model.predict(pg)
                        if s.get("calibration", {}).get("accepted"):
                            p = nfl_eval.apply_frozen([{"p_home": p}], s["calibration"]["final"])[0]["p_home"]
                        todo = {"winner": (p, 0.0, 1 - p)}
                    else:
                        dist = model.distribution(pg)
                        todo = {m: dist.market(m, served[g["game_id"]][m]["line"]) for m in POINT_MARKETS if m in served[g["game_id"]]}
                    for market, probs in todo.items():
                        if market not in served[g["game_id"]]:
                            continue
                        pa, pm = round(probs[0], 9), round(probs[1], 9)
                        rows.append({"game_id": g["game_id"], "market": market, "line": served[g["game_id"]][market]["line"],
                                     "p": (pa, pm, round(1 - pa - pm, 9)), "generated_at": now, "tipoff_at": g["tipoff_at"]})
            except Exception as exc:
                out["errors"][c.spec.name] = f"{type(exc).__name__}: {exc}"
                continue
            definition = f"{a['definition']}+spec={c.spec_hash[:8]}"
            lc.append_rows(path, pol, c, rows, definition=definition, dataset_version=s.get("dataset_version", ""), now=now,
                           dry_run=dry_run, out=out)
    if out["status"] == "success" and (out["errors"] or out["skipped"]):
        out["status"] = "partial"
    return out
