"""Persistent agreement tracking `tracking-v2` (docs/MODEL_SELECTION.md §18). Research only.

A *group* is a registered model subset with one agreement rule (single, unanimous, majority, probability average, the
cohort's learned stacker, or the pool stacker fitted on earlier out-of-sample evidence). Groups are sealed in
models/tracking_groups.json (≤ 500 rule configurations = groups × thresholds per sport and market), registered before any
result was computed. Each two-hour cycle records one immutable observation per group and research cohort
(`agreement_observations`); cumulative thresholds and non-overlapping confidence bands are derived from the stored
statistic when read. Historical results come from the saved out-of-sample tables (no refit) and are exploratory; they are
reported separately and never as recorded forecasts. Nothing here selects, recommends or promotes.

Confidence (`statistic`) per rule, for the picked outcome:
- single: the member's probability;
- unanimous: the minimum probability any member assigns to the agreed outcome (every member present and agreeing);
- avg: the equal average's probability for its most probable outcome;
- stack / stack_pool: the stacker's probability for its most probable outcome;
- majority: vote count is recorded; confidence = mean over ALL members (dissenters included) of the probability assigned to
  the majority outcome. A pick needs a strict majority of all members; a member's exact tie is an abstention; equal leading
  counts are a tie (no pick).
A group with a missing member is `incomplete`: never computed from a subset, never merged across lines.
"""
import gzip
import hashlib
import importlib.util
import json
import math
import sqlite3
from bisect import bisect_left
from collections import Counter, defaultdict
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from typing import Optional

from . import combine, config, nhl_eval, selector, sports
from .errors import InvalidRequest
from .db import transaction, utc_now

STUDY = "tracking-v2"
REGISTRY_PATH = Path(__file__).parent / "models" / "tracking_groups.json"
CAP = 500                                   # new rule configurations (groups × thresholds) per sport and market
THRESHOLDS = {"winner": (0.55, 0.60, 0.65, 0.70, 0.75, 0.80), "reg3": (0.40, 0.45, 0.50, 0.55, 0.60),
              "line": (0.55, 0.60, 0.65, 0.70)}
# Descriptive reporting grid (reconciliation 2026-10-03): the user's required cumulative 50–90% thresholds (reg3 35–70%) and
# 5-point non-overlapping bands, incl. the NHL three-way lower bands. Derived from the stored statistic when read; the sealed
# registry's thresholds above stay the only ones the exploratory leaderboard and checks use (cells carry `sealed`).
REPORT_THRESHOLDS = {"winner": tuple(round(0.50 + 0.05 * i, 2) for i in range(9)),
                     "line": tuple(round(0.50 + 0.05 * i, 2) for i in range(9)),
                     "reg3": tuple(round(0.35 + 0.05 * i, 2) for i in range(8))}
GRID = "report-grid-v1"
MIN_EVIDENCE = 100                          # decided picks before a result is more than "insufficient evidence"
MIN_COVERAGE = 0.05
STACK_MIN_TRAIN = 300
SIDE_NAMES = ("a", "mid", "b")
PROBABILISTIC = ("single", "avg", "stack", "stack_pool")
KIND_SEMANTICS = {
    "single": "member probability of its most probable outcome",
    "unanimous": "minimum probability assigned by the members to the agreed outcome (all present, all agree)",
    "avg": "average probability assigned to the average's most probable outcome",
    "majority": "mean probability of the strict-majority outcome over all members (dissenters included); votes recorded",
    "stack": "probability from the cohort's learned stacker (refit on the two earlier seasons)",
    "stack_pool": "probability from the pool stacker fitted on earlier out-of-sample forecasts (frozen weights)",
}
# exact-rules-v1 (app/exact_rules.py): a registered subset with one threshold PER MEMBER, tracked here as kind 'exact'.
# Confidence = each member's probability of the agreed side among the market's allowed sides (the explorer's definition);
# the rule's statistic is the minimum of them: descriptive, never an ensemble probability (no vector, no Brier/log loss).
EXACT_KIND = "exact"
EXACT_EPS = 1e-12          # the explorer's tolerance (combination_thresholds.EPS): the same boundary games qualify in both
EXACT_SEMANTICS = {"exact": "minimum member confidence for the agreed side (each member's probability of that side among the "
                            "allowed sides) with every member at or above its OWN threshold; descriptive only"}


EXTRA_MODULES = {"research": "ngboost"}      # optional dependency group -> importable module (candidates/registry.py)
# Only the groups above can be tracking members. Any other optional group (e.g. deep networks applied from stored weights)
# is excluded on purpose: the sealed group registry was frozen without it, so it is recorded as a candidate, never tracked.
UNTRACKED_EXTRA = "optional dependency group not in the sealed tracking registry"


class RegistryError(ValueError):
    pass


def kind_of(market: str) -> str:
    return "winner" if market == "winner" else "reg3" if market == "reg3" else "line"


def bands(market: str) -> list[list[float]]:
    """Non-overlapping confidence bands [lo, hi): below the first threshold, between thresholds, at or above the last."""
    t = THRESHOLDS[kind_of(market)]
    edges = [0.0, *t, 1.0 + 1e-9]
    return [[edges[i], edges[i + 1]] for i in range(len(edges) - 1)]


def report_thresholds(market: str) -> tuple:
    k = kind_of(market)
    return tuple(sorted(set(THRESHOLDS[k]) | set(REPORT_THRESHOLDS[k])))


def report_bands(market: str) -> list[list[float]]:
    """Descriptive non-overlapping bands: below the floor (1/3 for reg3, where only a majority's mean can fall), then 5-point
    bands up to the last threshold, then at or above it."""
    k = kind_of(market)
    floor = [1 / 3] if k == "reg3" else []
    edges = [0.0, *floor, *REPORT_THRESHOLDS[k], 1.0 + 1e-9]
    return [[edges[i], edges[i + 1]] for i in range(len(edges) - 1)]


def band_label(market: str, lo: float, hi: float) -> str:
    if lo == 0.0:      # also holds majority confidences below the floor (dissenters lower the mean)
        return f"below {hi:.2f}"
    return f"{lo:.2f}–{hi:.2f}" if hi <= 1.0 else f"≥ {lo:.2f}"


# --- Registry -------------------------------------------------------------------------------------------------------
def seal(reg: dict) -> dict:
    body = {k: v for k, v in reg.items() if k != "sha256"}
    return {**body, "sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()}


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict:
    reg = json.loads(Path(path).read_text())
    if seal(reg)["sha256"] != reg.get("sha256"):
        raise RegistryError("tracking registry does not match its seal")
    return reg


def load_registry(path: Optional[Path] = None) -> dict:
    p = path or REGISTRY_PATH
    if not p.is_file():
        raise RegistryError(f"tracking registry missing: {p.name}")
    return _load(str(p), p.stat().st_mtime)


def cohort_members(sport: str, market: str) -> tuple[list, list, list]:
    """(core members, shadow members, core stacker inputs) as the research cohorts record them."""
    from . import agreement, research_live
    if market == "winner":
        return list(agreement.NAMES[sport]), list(agreement.SHADOW[sport]), list(research_live.CORE[sport])
    return list(research_live.LINE_MEMBERS[sport]), list(research_live.SHADOW_LINE_MEMBERS[sport]), \
        list(research_live.LINE_MEMBERS[sport])


def candidates_for(sport: str, market: str, live_ok: bool = True) -> list:
    from .candidates import registry
    out = []
    for s in registry.SPECS:
        if s.sport != sport or market not in s.markets:
            continue
        if s.extra and s.extra not in EXTRA_MODULES:
            continue          # UNTRACKED_EXTRA: explicit, never a member (live or historical)
        if live_ok and s.extra and importlib.util.find_spec(EXTRA_MODULES[s.extra]) is None:
            continue          # optional dependency absent: the candidate is never recorded live, so it cannot be a member
        out.append(s.name)
    return out


def _oof(sport: str) -> dict:
    """Saved out-of-sample tables (candidate pool stages 1 and 2): every core member and candidate on the shared walk."""
    base = config.data_dir() / "research" / "candidates"
    merged = {}
    for stage in ("screen", "assess"):
        p = base / f"oof-{sport}-{stage}.json.gz"
        if not p.is_file():
            continue
        d = json.loads(gzip.decompress(p.read_bytes()))
        for market, rows in d["tables"].items():
            seen = merged.setdefault(market, {})
            for r in rows:
                seen.setdefault(r["game_id"], {**r, "stage": stage})
    return {m: sorted(v.values(), key=lambda r: (r["tipoff_at"], r["game_id"])) for m, v in merged.items()}


def _groups_for(sport: str, market: str, anchor: str, oof_rows: list, live_ok: bool) -> tuple[list, list, dict]:
    core, shadow, stack_in = cohort_members(sport, market)
    cands = candidates_for(sport, market, live_ok)
    sources = core + shadow
    prio: list[tuple[str, dict]] = []

    def add(tag, kind, members, **extra):
        cohort = "expanded" if (set(members) & set(shadow)) or extra.get("cohort") == "expanded" else "core"
        g = {"kind": kind, "members": list(members), "cohort": cohort, "priority": tag, **extra}
        g["id"] = f"{kind}{':' + extra['stack_of'] if 'stack_of' in extra else ''}|{'+'.join(members)}"
        prio.append((tag, g))

    for m in sources + cands:
        add("P1 single", "single", [m])
    for kind in ("avg", "unanimous", "majority"):
        if len(stack_in) >= 2 and not (kind == "majority" and len(stack_in) < 3):
            add("P2 core set", kind, stack_in)
    if len(core) != len(stack_in) and len(core) >= 3:          # NBA winner: the six registered voters
        for kind in ("unanimous", "majority"):
            add("P2 core set", kind, core)
    add("P2 core stacker", "stack", stack_in, stack_of="core")
    if shadow:
        full = stack_in + shadow
        for kind in ("avg", "unanimous", "majority"):
            add("P3 expanded set", kind, full)
        add("P3 expanded stacker", "stack", full, stack_of="expanded", cohort="expanded")
    pool_members = [m for m in sources + cands if oof_rows and all(m in r["vectors"] for r in oof_rows)]
    stack_model = None
    if len(pool_members) >= 2:
        fit_rows = [([r["vectors"][m] for m in pool_members], r["realised"]) for r in oof_rows if r["realised"] is not None]
        if len(fit_rows) >= STACK_MIN_TRAIN:
            stack_model = combine.fit_stack(fit_rows, market)
            stack_model["members"] = pool_members
            stack_model["training_seasons"] = sorted({r["season"] for r in oof_rows})
            stack_model["weights"] = [round(w, 8) for w in stack_model["weights"]]
            stack_model["bias"] = [round(b, 8) for b in stack_model["bias"]]
            add("P3 pool stacker", "stack_pool", pool_members)
    for pair in combinations(sources, 2):
        for kind in ("unanimous", "avg"):
            add("P4 pair", kind, pair)
    # Triples: least-evaluated first — those containing a shadow member or a member without historical inputs (NBA
    # availability_two_hour, NHL xG triples were the gaps in docs/EVIDENCE_AUDIT.md §6), then registry order.
    novel = set(shadow) | {m for m in sources if oof_rows and any(m not in r["vectors"] for r in oof_rows)}
    triples = sorted(combinations(sources, 3), key=lambda t: -len(novel & set(t))) if len(sources) >= 4 else []
    for tri in triples:
        for kind in ("unanimous", "avg"):
            add("P5 triple", kind, tri)
    if anchor in sources:
        for c in cands:
            for kind in ("unanimous", "avg"):
                add("P6 candidate+anchor", kind, [anchor, c])
    for c in cands:
        for m in sources:
            if m != anchor:
                for kind in ("unanimous", "avg"):
                    add("P7 candidate+member", kind, [m, c])
    for pair in combinations(cands, 2):
        add("P8 candidate pair", "avg", pair)
    for tri in triples:
        add("P9 triple majority", "majority", tri)
    if len(sources) >= 5:
        for quad in combinations(sources, 4):
            for kind in ("unanimous", "avg"):
                add("P10 four", kind, quad)
    n_t = len(THRESHOLDS[kind_of(market)])
    kept, skipped, seen = [], [], set()
    for tag, g in prio:
        if g["id"] in seen:
            continue
        seen.add(g["id"])
        if (len(kept) + 1) * n_t <= CAP:
            kept.append(g)
        else:
            skipped.append({"id": g["id"], "priority": tag, "reason": f"allocation cap ({CAP} rule configurations)"})
    return kept, skipped, ({"model": stack_model} if stack_model else {})


def build_registry(effective_from: str, live_ok: bool = True) -> dict:
    """Deterministic from code, the sealed combination registry's comparators and the saved out-of-sample tables."""
    from . import rule_tracking
    comps = rule_tracking.load_registry()["markets"]
    markets = {}
    for sport in ("nba", "nhl"):
        oof = _oof(sport)
        for market in sports.get(sport).markets:
            key = f"{sport}:{market}"
            core, shadow, stack_in = cohort_members(sport, market)
            anchor = comps.get(key, {}).get("comparator") or stack_in[0]
            groups, skipped, extra = _groups_for(sport, market, anchor, oof.get(market, []), live_ok)
            n_t = len(THRESHOLDS[kind_of(market)])
            markets[key] = {"sport": sport, "market": market, "anchor": anchor, "core": core, "shadow": shadow,
                            "stack_inputs": stack_in, "candidates": candidates_for(sport, market, live_ok),
                            "thresholds": list(THRESHOLDS[kind_of(market)]), "bands": bands(market),
                            "configurations": len(groups) * n_t, "groups": groups, "skipped": skipped,
                            "pool_stacker": extra.get("model")}
    return seal({"study": STUDY, "version": 1, "effective_from": effective_from, "cap": CAP, "min_evidence": MIN_EVIDENCE,
                 "kinds": KIND_SEMANTICS, "markets": markets})


# --- Classification (pure) ------------------------------------------------------------------------------------------
def classify(group: dict, vectors: dict, market: str, pool_model: Optional[dict] = None) -> dict:
    """One group's classification of one game from member probability vectors; never reads outcomes."""
    kind, members = group["kind"], group["members"]
    if kind == "stack":
        key = f"stack:{group['stack_of']}"
        missing = {} if key in vectors else {key: "no stacker probability in this cohort"}
    else:
        missing = {m: "member forecast missing" for m in members if m not in vectors}
    if kind == "stack_pool" and pool_model is None:
        missing["pool_stacker"] = "no pool stacker fitted"
    if missing:
        return {"status": "incomplete", "side": None, "statistic": None, "vector": None, "votes": None, "missing": missing}
    out = {"status": "pick", "side": None, "statistic": None, "vector": None, "votes": None, "missing": {}}

    def from_vector(v):
        v = tuple(float(x) for x in v)
        s = combine.pick(v, market)
        out["vector"] = v
        if s is None:
            out["status"] = "tie"
        else:
            out["side"], out["statistic"] = s, v[s]
        return out

    if kind == "single":
        return from_vector(vectors[members[0]])
    if kind == "avg":
        return from_vector(combine.average([vectors[m] for m in members]))
    if kind == "stack":
        return from_vector(vectors[f"stack:{group['stack_of']}"])
    if kind == "stack_pool":
        return from_vector(combine.stack(pool_model, [vectors[m] for m in pool_model["members"]]))
    if kind == EXACT_KIND:
        return _classify_exact(group, vectors, market, out)
    vecs = [vectors[m] for m in members]
    picks = [combine.pick(v, market) for v in vecs]
    if kind == "unanimous":
        if any(p is None for p in picks):
            out["status"] = "tie"
        elif len(set(picks)) > 1:
            out["status"] = "disagree"
        else:
            s = picks[0]
            out["side"], out["statistic"] = s, min(v[s] for v in vecs)
        return out
    if kind == "majority":
        counts = {i: picks.count(i) for i in combine.sides(market)}
        out["votes"] = {SIDE_NAMES[i]: c for i, c in counts.items()} | {"abstain": picks.count(None), "n": len(picks)}
        top = max(counts.values())
        leaders = [i for i, c in counts.items() if c == top]
        if top == 0:
            out["status"] = "no_majority"           # every member abstained
        elif len(leaders) > 1:
            out["status"] = "tie"
        elif top <= len(picks) / 2:
            out["status"] = "no_majority"
        else:
            s = leaders[0]
            out["side"], out["statistic"] = s, sum(v[s] for v in vecs) / len(vecs)
        return out
    raise ValueError(kind)


def exact_confidence(vec, side: int, market: str) -> float:
    """A member's probability of `side` among the market's allowed sides: the explorer's confidence (the push mass of a
    two-way line market is left out), so a registered threshold means what it meant where the rule was chosen."""
    allowed = combine.sides(market)
    tot = sum(float(vec[k]) for k in allowed)
    return float(vec[side]) / tot if tot > 0 else 0.0


def _classify_exact(group: dict, vectors: dict, market: str, out: dict) -> dict:
    """Every member present (checked by the caller), all on the same side, each at or above its own threshold. The reasons
    of a non-pick go to `missing` (member -> reason), as the missing members of an incomplete observation do."""
    members, ths = group["members"], group["thresholds"]
    if len(ths) != len(members):
        raise ValueError("exact rule needs one threshold per member")
    vecs = [tuple(float(x) for x in vectors[m]) for m in members]
    picks = [combine.pick(v, market) for v in vecs]
    if any(p is None for p in picks):
        out["status"] = "tie"
        out["missing"] = {m: "no pick: its allowed sides tie exactly" for m, p in zip(members, picks) if p is None}
        return out
    if len(set(picks)) > 1:
        out["status"] = "disagree"
        out["missing"] = {m: f"picks {SIDE_NAMES[p]}" for m, p in zip(members, picks)}
        return out
    side = picks[0]
    confs = [exact_confidence(v, side, market) for v in vecs]
    below = {m: f"confidence {c:.4f} below its threshold {float(t):.4g}" for m, c, t in zip(members, confs, ths)
             if c < float(t) - EXACT_EPS}
    if below:
        out["status"] = "below_threshold"
        out["missing"] = below
        return out
    out["side"], out["statistic"] = side, min(confs)
    return out


# --- Prospective recording ------------------------------------------------------------------------------------------
def _key_digest(*parts) -> str:
    return hashlib.sha256("\x1f".join(json.dumps(p, sort_keys=True, default=str) for p in parts).encode()).hexdigest()[:16]


def _member_version(conn, ref: dict) -> Optional[str]:
    table = ref.get("table") if ref else None
    if table == "predictions":
        r = conn.execute("SELECT model_version FROM predictions WHERE prediction_id=?", (ref["id"],)).fetchone()
    elif table == "market_forecasts":
        r = conn.execute("SELECT model_version FROM market_forecasts WHERE forecast_id=?", (ref["id"],)).fetchone()
    else:
        return None
    return r[0] if r else None


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _inputs(conn, core_row, expanded_row, now: str = "9999") -> tuple[dict, dict, str]:
    """Member vectors for one cohort: core members, shadows/expanded stacker from the paired expanded cohort, the core
    stacker, and the latest candidate forecast made against this core cohort (same exact line) before the start."""
    vectors, info = {}, {}
    latest = core_row["information_at"]
    for row in (core_row, expanded_row):
        if row is None:
            continue
        for m in json.loads(row["members"]):
            if m["name"] not in info:
                vectors[m["name"]] = tuple(m["p"])
                info[m["name"]] = {"name": m["name"], "ref": m.get("ref"), "version": _member_version(conn, m.get("ref")),
                                   "p": m["p"], "generated_at": row["generated_at"]}
    st = json.loads(core_row["stack"])
    if st and st.get("p"):
        vectors["stack:core"] = tuple(st["p"])
    if expanded_row is not None:
        st = json.loads(expanded_row["stack"])
        if st and st.get("p"):
            vectors["stack:expanded"] = tuple(st["p"])
    if _has(conn, "candidate_forecasts"):
        for r in conn.execute(
                "SELECT * FROM candidate_forecasts f WHERE f.game_id=? AND f.market=? AND f.line_ref=? AND f.line IS ? "
                "AND f.tipoff_at=? AND f.generated_at < f.tipoff_at AND f.generated_at <= ? AND f.forecast_id = (SELECT x.forecast_id FROM "
                "candidate_forecasts x WHERE x.game_id=f.game_id AND x.market=f.market AND x.line_ref=f.line_ref "
                "AND x.candidate=f.candidate AND x.generated_at < x.tipoff_at AND x.generated_at <= ? ORDER BY x.generated_at DESC, x.forecast_id DESC LIMIT 1)",
                (core_row["game_id"], core_row["market"], f"research_cohorts:{core_row['cohort_id']}", core_row["line"],
                 core_row["tipoff_at"], now, now)):
            vectors[r["candidate"]] = (r["p_a"], r["p_mid"], r["p_b"])
            info[r["candidate"]] = {"name": r["candidate"], "ref": {"table": "candidate_forecasts", "id": r["forecast_id"]},
                                    "version": r["model_version"], "p": [r["p_a"], r["p_mid"], r["p_b"]],
                                    "generated_at": r["generated_at"]}
            latest = max(latest, r["generated_at"])
    return vectors, info, latest


def _pairs(conn, game_ids: list, now: str) -> list:
    """(core cohort, paired expanded cohort or None) for the games' cohorts made before the current start, while that start
    is still ahead of `now` (an observation is only ever recorded before the start)."""
    from .research_live import EXPANDED
    out = []
    for gid in game_ids:
        rows = conn.execute("SELECT c.* FROM research_cohorts c JOIN games g ON g.game_id=c.game_id WHERE c.game_id=? "
                            "AND c.tipoff_at=g.tipoff_at AND c.generated_at < c.tipoff_at AND c.generated_at <= ? AND c.tipoff_at > ? "
                            "ORDER BY c.generated_at, c.cohort_id", (gid, now, now)).fetchall()
        core = [r for r in rows if not r["definition"].endswith(f":{EXPANDED}")]
        exp = [r for r in rows if r["definition"].endswith(f":{EXPANDED}")]
        for c in core:
            match = [e for e in exp if e["market"] == c["market"] and e["information_at"] == c["information_at"]
                     and e["line"] == c["line"] and e["definition"] == f"{c['definition']}:{EXPANDED}"]
            out.append((c, match[-1] if match else None))
    return out


def _has_0028(conn) -> bool:
    """Whether migration 0028 itself is applied (not the highest version: later migrations do not imply it)."""
    return _has(conn, "schema_migrations") and conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = 28").fetchone() is not None


def _observation_row(registry: str, group_id: str, sport: str, core, src, kind: str, mem: list, res: dict, sig: str,
                     inputs: str, now: str) -> tuple:
    """One `agreement_observations` row (positional, in INSERT order) from a classification and its stored inputs."""
    info_at = max([src["information_at"], core["information_at"]] +
                  [x["generated_at"] for x in mem if x["ref"] and x["ref"].get("table") == "candidate_forecasts"])
    return (registry, group_id, sport, core["game_id"], core["market"], core["line"], core["line_source"],
            core["cohort_id"], src["cohort_id"], src["definition"], kind, json.dumps(mem),
            json.dumps(res["missing"], sort_keys=True), sig, inputs, res["status"],
            None if res["side"] is None else SIDE_NAMES[res["side"]], res["statistic"],
            json.dumps(res["vector"]), json.dumps(res["votes"]), info_at, core["tipoff_at"], now)


_NO_EXPANDED = {"status": "incomplete", "side": None, "statistic": None, "vector": None, "votes": None,
                "missing": {"expanded_cohort": "no expanded cohort recorded with this core cohort"}}
NO_FIT_REFERENCE = "no fit reference: {table} rows carry a model version (code and configuration), not a parameter fit"


def _fit_provenance(conn, ref: Optional[dict]) -> dict:
    """The parameter fit behind a member forecast, when the forecast row records one (candidate_forecasts: fit_id, artifact
    digest, training cutoff). A `model_version` names code and configuration; a refit with the same configuration keeps it,
    so this is the only record that separates fits. Rows without a fit reference say so instead of implying one."""
    table = ref.get("table") if ref else None
    if table == "candidate_forecasts":
        r = conn.execute("SELECT fit_id, artifact_sha256, training_cutoff, dataset_version FROM candidate_forecasts "
                         "WHERE forecast_id=?", (ref["id"],)).fetchone()
        if r is not None:
            return {"fit": {"fit_id": r["fit_id"], "artifact_sha256": r["artifact_sha256"],
                            "training_cutoff": r["training_cutoff"], "dataset_version": r["dataset_version"]}, "fit_note": None}
    return {"fit": None, "fit_note": NO_FIT_REFERENCE.format(table=table or "member")}


def _exact_registry(exact_registry: Optional[dict], load_exact: bool) -> tuple:
    """(registry or None, error text or None): a broken registry file is an explicit failure, never 'no rules'."""
    from . import exact_rules as er
    if exact_registry is not None:
        return exact_registry, None
    if not load_exact:
        return None, None
    try:
        return er.load_registry(), None
    except Exception as exc:        # RegistryError (unreadable, tampered, malformed): reported, never raised to the caller
        return None, f"{type(exc).__name__}: {exc}"


def record(conn, *, game_ids: list, now: Optional[str] = None, write: bool = True, reg: Optional[dict] = None,
           exact_registry: Optional[dict] = None, load_exact: Optional[bool] = None) -> dict:
    """Observations for every registered group and every pregame core cohort of the games. Idempotent (UNIQUE registry,
    group, source cohort, inputs digest): the same stored inputs never add a row; a cohort whose available inputs changed
    (a candidate forecast made against its line later) gets a new row, earlier rows untouched (triggers). Never raises.

    Registered exact rules (exact-rules-v1) are recorded in the same pass, one row per rule and cohort, only for games that
    start after the rule's registration (`out["exact"]`); the registry is loaded from its file when `reg` is not given
    (the production path), or passed as `exact_registry`."""
    now = now or utc_now()
    out = {"status": "success", "registry": None, "created": 0, "already_recorded": 0, "cohorts": 0, "errors": {},
           "exact": {"status": "none", "rules": 0, "rows": 0, "created": 0, "already_recorded": 0, "before_registration": 0}}
    try:
        load_exact = (reg is None) if load_exact is None else load_exact
        reg = reg or load_registry()
        out["registry"] = reg["sha256"][:16]
        if not _has(conn, "agreement_observations") or "inputs_digest" not in \
                {r[1] for r in conn.execute("PRAGMA table_info(agreement_observations)")}:
            return {**out, "status": "skipped", "reason": "migrations 0021/0022 not applied to this store"}
        sport = conn.execute("SELECT sport FROM store_identity").fetchone()[0]
        exact, exact_error = _exact_registry(exact_registry, load_exact)
        if exact_error:                 # a broken exact-rule registry is reported and never stops the sealed groups
            out["exact"].update(status="failed", error=exact_error)
            out["errors"]["exact_rules"] = exact_error
        ex_all = [r for r in (exact["rules"] if exact else []) if r["sport"] == sport]
        if ex_all:
            out["exact"].update(status="recording", rules=len(ex_all), registry_version=exact["version"])
            if not _has_0028(conn):       # visible, never a constraint error per row, never blocks the sealed groups
                out["exact"].update(status="skipped", reason="migration 0028 not applied to this store: exact rules not recorded")
                ex_all = []
        rows = []
        for core, exp in _pairs(conn, game_ids, now):
            m = reg["markets"].get(f"{sport}:{core['market']}")
            ex_rules = [r for r in ex_all if r["market"] == core["market"]]
            if m is None and not ex_rules:
                continue
            out["cohorts"] += 1
            try:
                vectors, info, latest = _inputs(conn, core, exp, now)
                if latest >= core["tipoff_at"]:
                    out["errors"][f"{core['game_id']}:{core['market']}"] = "an input is not earlier than the start"
                    continue
                for g in (m["groups"] if m else []):
                    try:
                        src = exp if g["cohort"] == "expanded" else core
                        if src is None:
                            res, src = dict(_NO_EXPANDED), core
                        else:
                            res = classify(g, vectors, core["market"], m.get("pool_stacker"))
                        names = [x for x in g["members"] if x in info] if g["kind"] != "stack" else []
                        mem = [info[x] for x in names]
                        sig = _key_digest(sorted((x["name"], x["version"]) for x in mem), g["kind"],
                                          src["definition"] if g["kind"] == "stack" else None)
                        inputs = _key_digest(sorted((x["name"], json.dumps(x["ref"], sort_keys=True)) for x in mem),
                                             sorted(res["missing"]))
                        rows.append(_observation_row(reg["sha256"], g["id"], sport, core, src, g["kind"], mem, res, sig, inputs, now))
                    except Exception as exc:     # one group never blocks the other groups of its cohort
                        out["errors"][f"{core['game_id']}:{core['market']}:{g['id']}"] = f"{type(exc).__name__}: {exc}"
                for r in ex_rules:
                    try:
                        if core["tipoff_at"] <= r["effective_from"] or now < r["effective_from"]:
                            out["exact"]["before_registration"] += 1      # never backfilled: the game must start after registration
                            continue
                        src = exp if r["cohort"] == "expanded" else core
                        if src is None:
                            res, src = dict(_NO_EXPANDED), core
                        else:
                            res = classify({"kind": EXACT_KIND, "members": r["members"], "thresholds": r["thresholds"]},
                                           vectors, core["market"])
                        mem = [{**info[x], "threshold": float(t), **_fit_provenance(conn, info[x]["ref"])}
                               for x, t in zip(r["members"], r["thresholds"]) if x in info]
                        sig = _key_digest(sorted((x["name"], x["version"]) for x in mem), EXACT_KIND, None)
                        inputs = _key_digest(sorted((x["name"], json.dumps(x["ref"], sort_keys=True)) for x in mem),
                                             sorted(res["missing"]))
                        rows.append(_observation_row(r["rule_sha256"], r["id"], sport, core, src, EXACT_KIND, mem, res, sig, inputs, now))
                        out["exact"]["rows"] += 1
                    except Exception as exc:     # one rule never blocks the sealed groups or the other rules
                        out["errors"][f"{core['game_id']}:{core['market']}:{r['id']}"] = f"{type(exc).__name__}: {exc}"
            except Exception as exc:     # one cohort never blocks the others
                out["errors"][f"{core['game_id']}:{core['market']}"] = f"{type(exc).__name__}: {exc}"
        if write and rows:
            by_cohort = defaultdict(list)
            for r in rows:
                by_cohort[(r[3], r[4], r[7])].append(r)
            for key, batch in by_cohort.items():      # one transaction per cohort: a rejected row never drops the others
                try:
                    with transaction(conn):
                        for r in batch:
                            counter = out["exact"] if r[10] == EXACT_KIND else out
                            if conn.execute("SELECT 1 FROM agreement_observations WHERE registry=? AND group_id=? AND "
                                            "source_cohort_id=? AND inputs_digest=?", (r[0], r[1], r[8], r[14])).fetchone():
                                counter["already_recorded"] += 1
                                continue
                            try:     # a plain insert: a row refused by a constraint is reported, never counted as a retry
                                conn.execute(
                                    "INSERT INTO agreement_observations (registry, group_id, sport, game_id, market, "
                                    "line, line_source, cohort_id, source_cohort_id, definition, kind, members, missing, "
                                    "version_sig, inputs_digest, status, side, statistic, vector, votes, information_at, "
                                    "tipoff_at, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
                                counter["created"] += 1
                            except sqlite3.IntegrityError as exc:
                                out["errors"][f"{key[0]}:{key[1]}:{r[1]}"] = f"{type(exc).__name__}: {exc}"
                except sqlite3.DatabaseError as exc:
                    out["errors"][f"{key[0]}:{key[1]}"] = f"{type(exc).__name__}: {exc}"
        elif not write:
            out["would_write"] = len(rows)
        if out["errors"]:
            out["status"] = "partial"
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        out.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    return out


# --- Summaries (shared by prospective and historical) ---------------------------------------------------------------
def _scores(vec, realised):
    return combine.brier(vec, realised), combine.log_loss(vec, realised)


_FIELDS = ("wins", "losses", "pushes", "voids", "pending", "unscorable", "brier_n", "brier", "log_loss", "conf_n", "conf")


class _Picks:
    """A group's picks sorted by statistic with suffix sums, so every cumulative threshold and band is O(log n)."""

    def __init__(self, items: list, market: str, kind: str):
        rows = []
        for i in items:
            if i["side"] is None:
                continue
            r = dict.fromkeys(_FIELDS, 0.0)
            if i["state"] == "void":
                r["voids"] = 1
            elif i["state"] != "settled":
                r["pending"] = 1
            elif i["realised"] is None:
                r["unscorable"] = 1
            else:
                o = selector.outcome(market, i["side"], i["realised"])
                r[{"win": "wins", "loss": "losses", "push": "pushes"}[o]] = 1
                if o != "push":
                    r["conf_n"], r["conf"] = 1, i["statistic"]
                if kind in PROBABILISTIC and i.get("vector"):
                    b, ll = _scores(i["vector"], i["realised"])
                    r["brier_n"], r["brier"], r["log_loss"] = 1, b, ll
            rows.append((i["statistic"], r))
        rows.sort(key=lambda x: x[0])
        self.stats = [x[0] for x in rows]
        self.suffix = [dict.fromkeys(_FIELDS, 0.0) for _ in range(len(rows) + 1)]
        for k in range(len(rows) - 1, -1, -1):
            nxt, cur = self.suffix[k + 1], rows[k][1]
            self.suffix[k] = {f: nxt[f] + cur[f] for f in _FIELDS}

    def _from(self, x: float) -> int:
        return bisect_left(self.stats, x)

    def cell(self, lo: float, hi: Optional[float], eligible: int) -> dict:
        """Picks with lo ≤ statistic (< hi when given); a 1e-9 tolerance keeps 0.60 inside the 0.60 threshold."""
        i = self._from(lo - 1e-9)
        j = len(self.stats) if hi is None else self._from(hi - 1e-9)
        a, b = self.suffix[i], self.suffix[j]
        c = {f: a[f] - b[f] for f in _FIELDS}
        wins, losses, pushes = (int(round(c[k])) for k in ("wins", "losses", "pushes"))
        n = wins + losses
        has_conf, has_prob = c["conf_n"] >= 0.5, c["brier_n"] >= 0.5
        return {"selected": j - i, "settled": n + pushes, "pending": int(round(c["pending"])),
                "wins": wins, "losses": losses, "pushes": pushes, "voids": int(round(c["voids"])),
                "unscorable": int(round(c["unscorable"])), "coverage": (j - i) / eligible if eligible else None, "decided": n,
                "win_rate": wins / n if n else None, "ci95": nhl_eval._wilson(wins, n),
                "mean_confidence": c["conf"] / c["conf_n"] if has_conf else None,
                "brier": c["brier"] / c["brier_n"] if has_prob else None,
                "log_loss": c["log_loss"] / c["brier_n"] if has_prob else None,
                "evidence": "sufficient" if n >= MIN_EVIDENCE else "insufficient evidence"}


class _Ranked:
    """Per single model: its settled, decided picks ranked by confidence (sorted once per report)."""

    def __init__(self, singles: dict, market: str):
        self.market, self.singles, self.cache = market, singles, {}

    def matched(self, name: str, decided: int, games: set) -> dict:
        """The single model's `decided` most confident settled, decided games among the same eligible games."""
        if name not in self.cache:
            ranked = []
            for gid, i in self.singles.get(name, {}).items():
                if i["side"] is None or i["state"] != "settled" or i["realised"] is None:
                    continue
                o = selector.outcome(self.market, i["side"], i["realised"])
                if o != "push":
                    ranked.append((-i["statistic"], gid, o == "win"))
            self.cache[name] = sorted(ranked)
        flags = []
        for _, gid, win in self.cache[name]:
            if gid in games:
                flags.append(win)
                if len(flags) >= decided:
                    break
        return {"model": name, "games": len(flags), "win_rate": sum(flags) / len(flags) if flags else None}


def summarise(group: dict, items: list, ranked: "_Ranked", market: str, anchor: str) -> dict:
    """Per group: status counts, missing reasons, cumulative thresholds, bands, probabilistic scores, matched singles."""
    eligible = len(items)
    st = Counter(i["status"] for i in items)
    miss = Counter(k for i in items for k in (i.get("missing") or {}))
    games = {i["game_id"] for i in items}
    comparators = list(dict.fromkeys(([anchor] if anchor else []) + [m for m in group["members"] if m in ranked.singles]))
    picks = _Picks(items, market, group["kind"])
    cum = []
    sealed = set(THRESHOLDS[kind_of(market)])
    for t in (None, *report_thresholds(market)):
        cell = picks.cell(-1.0 if t is None else t, None, eligible)
        cell["threshold"] = t
        cell["sealed"] = t is None or t in sealed
        cell["matched"] = [ranked.matched(x, cell["decided"], games) for x in comparators] if cell["decided"] else []
        cum.append(cell)
    bnd = []
    for lo, hi in report_bands(market):
        cell = picks.cell(-1.0 if lo == 0.0 else lo, None if hi > 1.0 else hi, eligible)
        cell["band"], cell["label"] = [lo, min(hi, 1.0)], band_label(market, lo, hi)
        bnd.append(cell)
    prob = None
    if group["kind"] in PROBABILISTIC:
        sc = [_scores(i["vector"], i["realised"]) for i in items
              if i["state"] == "settled" and i["realised"] is not None and i.get("vector")]
        prob = {"games": len(sc), "brier": sum(b for b, _ in sc) / len(sc) if sc else None,
                "log_loss": sum(l for _, l in sc) / len(sc) if sc else None}
    return {"id": group["id"], "kind": group["kind"], "members": group["members"], "cohort": group["cohort"],
            "priority": group["priority"], "eligible": eligible, "status_counts": dict(st), "missing_reasons": dict(miss),
            "complete": eligible - st.get("incomplete", 0), "all_picks": cum[0], "thresholds": cum[1:], "bands": bnd,
            "probabilistic": prob, "versions": dict(Counter(i.get("version_sig") for i in items if i.get("version_sig")))}


def leaderboard(groups: list, market: str, searched: int, exclude=frozenset()) -> list:
    """Exploratory ranking of (group, threshold) configurations with ≥ MIN_EVIDENCE decided picks and ≥ 5% coverage by the
    Bonferroni lower bound (over every configuration searched in the market) of the win-rate difference against the
    market's anchor model at matched coverage. Never a recommendation."""
    from statistics import NormalDist
    z = NormalDist().inv_cdf(1 - 0.05 / max(1, searched))
    out = []
    for g in groups:
        if g["id"] in exclude:
            continue
        for cell in g["thresholds"]:
            if not cell.get("sealed", True):        # descriptive grid cells never enter the search count or ranking
                continue
            n = cell["decided"]
            if n < MIN_EVIDENCE or (cell["coverage"] or 0) < MIN_COVERAGE or not cell["matched"]:
                continue
            ref = cell["matched"][0]
            if ref["win_rate"] is None or not ref["games"]:
                continue
            p, q = cell["win_rate"], ref["win_rate"]
            se = math.sqrt(p * (1 - p) / n + q * (1 - q) / ref["games"]) or 1e-9
            out.append({"id": g["id"], "kind": g["kind"], "members": g["members"], "threshold": cell["threshold"],
                        "decided": n, "coverage": cell["coverage"], "win_rate": p, "ci95": cell["ci95"],
                        "anchor": ref["model"], "anchor_win_rate": q, "lift": p - q, "lower_adjusted": p - q - z * se})
    return sorted(out, key=lambda r: (-r["lower_adjusted"], -r["decided"], r["id"]))[:25]


def day_bounds(date_from=None, date_to=None) -> tuple[Optional[str], Optional[str]]:
    """US Eastern calendar days (the app's game dates) as UTC [start, end) bounds."""
    from datetime import date as _date, datetime as _dt, time as _time, timedelta as _td
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")

    def start(d):
        d = d if isinstance(d, _date) else _date.fromisoformat(str(d))
        return _dt.combine(d, _time(0), et).astimezone(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lo = start(date_from) if date_from else None
    hi = None
    if date_to:
        d = date_to if isinstance(date_to, _date) else _date.fromisoformat(str(date_to))
        hi = start(d + _td(days=1))
    return lo, hi


def _canonical_cohorts(conn, market: str, version=None) -> dict:
    """game_id -> its latest core cohort made before the start it was made for. With a `version` naming a research
    definition, the latest cohort of that definition (a version that names only member versions does not narrow it)."""
    sql = ("SELECT game_id, cohort_id, definition FROM research_cohorts WHERE market=? AND definition NOT LIKE '%:expanded-v1' "
           "AND generated_at < tipoff_at ORDER BY generated_at, cohort_id")
    rows = conn.execute(sql, (market,)).fetchall()
    if version and any(r["definition"].startswith(version) for r in rows):
        rows = [r for r in rows if r["definition"].startswith(version)]
    return {r["game_id"]: r["cohort_id"] for r in rows}


def _optimistic(m: dict) -> set:
    """Groups whose HISTORICAL inputs include a goalie candidate (the saved tables gave it the actual starter, a postgame
    fact): shown with their note, never ranked in the historical leaderboard or chosen by the chronological check."""
    goalie = any("_goalie" in c for c in m.get("candidates", []))
    return {g["id"] for g in m["groups"] if any("_goalie" in x for x in g["members"]) or (goalie and g["kind"] == "stack_pool")}


# Identity of how the historical artifacts are calculated (stored statistic, leaderboard membership). Tables, summaries and
# chronological checks carry it; an artifact built under another calculation is rejected, never mixed with this one.
# calc-2 (2026-10-03): statistics rounded down (calc-1 rounded to nearest and could lift a pick across a threshold); goalie
# groups and the pool stacker excluded from the historical leaderboard and chronological check.
CALC = "calc-2"


def _stored(statistic):
    """A statistic as kept in the historical tables: 5 decimals, rounded DOWN (after the 1e-9 boundary tolerance of
    `_Picks.cell`), so a stored value never moves a pick up across a threshold or into a higher band."""
    import math
    if statistic is None:
        return None
    kept = math.floor((statistic + 1e-9) * 1e5) / 1e5
    return 0.33334 if kept < 1 / 3 <= statistic + 1e-9 else kept      # 1/3 (the three-way floor) is not on the 1e-5 grid


# --- Prospective report ---------------------------------------------------------------------------------------------
def _state(row) -> str:
    if row["status"] in ("postponed", "canceled") or row["game_tipoff_at"] != row["tipoff_at"]:
        return "void"
    if row["status"] != "final" or not row["result_available_at"]:
        return "pending"
    return "settled"


def prospective_items(conn, reg: dict, sport: str, market: str, date_from=None, date_to=None, version=None,
                      registries: Optional[list] = None) -> dict:
    """group_id -> {game_id: item} from the canonical observation per game (latest pregame source cohort). `registries`
    (default: the sealed registry's sha256) are the `registry` values read, e.g. registered exact rules' own digests."""
    from . import markets as mk
    if not _has(conn, "agreement_observations"):
        return {}
    regs = list(registries) if registries else [reg["sha256"]]
    sql = ("SELECT o.*, g.status AS g_status, g.home_score, g.away_score, g.result_available_at, g.tipoff_at AS game_tipoff_at, "
           "d.decision, c.generated_at AS cohort_generated_at FROM agreement_observations o JOIN games g ON g.game_id=o.game_id "
           "JOIN research_cohorts c ON c.cohort_id=o.source_cohort_id LEFT JOIN game_decisions d ON d.game_id=o.game_id "
           f"WHERE o.registry IN ({','.join('?' * len(regs))}) AND o.market=?")
    params = [*regs, market]
    lo, hi = day_bounds(date_from, date_to)
    if lo:
        sql += " AND o.tipoff_at >= ?"
        params.append(lo)
    if hi:
        sql += " AND o.tipoff_at < ?"
        params.append(hi)
    if version:
        sql += " AND (o.definition LIKE ? OR o.version_sig = ?)"
        params += [f"{version}%", version]
    out: dict = defaultdict(dict)
    rows = conn.execute(sql + " ORDER BY c.generated_at, o.source_cohort_id, o.observation_id", params).fetchall()
    # Canonical cohort per game: its latest pregame core cohort (independent of which observations exist). A group without
    # an observation at that cohort has none for the game; it is never scored from an older cohort (another line or inputs).
    canonical = _canonical_cohorts(conn, market, version)
    for r in rows:
        if r["cohort_id"] != canonical.get(r["game_id"]):
            continue
        row = {"status": r["g_status"], "game_tipoff_at": r["game_tipoff_at"], "tipoff_at": r["tipoff_at"],
               "result_available_at": r["result_available_at"]}
        state = _state(row)
        realised = None
        if state == "settled":
            s = mk.settle(market, r["line"], status=r["g_status"], home=r["home_score"], away=r["away_score"], decision=r["decision"])
            realised = {"a": 0, "mid": 1, "b": 2}.get(s)
        vec = json.loads(r["vector"])
        out[r["group_id"]][r["game_id"]] = {      # later rows overwrite earlier ones: the latest pregame cohort and inputs
            "game_id": r["game_id"], "status": r["status"], "side": None if r["side"] is None else SIDE_NAMES.index(r["side"]),
            "statistic": r["statistic"], "vector": vec, "missing": json.loads(r["missing"]), "state": state, "realised": realised,
            "line": r["line"], "tipoff_at": r["tipoff_at"], "version_sig": r["version_sig"], "definition": r["definition"],
            "recorded_at": r["recorded_at"], "votes": json.loads(r["votes"])}
    return out


def _filter_groups(groups: list, kind=None, member=None, group_id=None) -> list:
    out = groups
    if kind:
        out = [g for g in out if g["kind"] == kind]
    if member:
        want = set(member.split(","))
        out = [g for g in out if want <= set(g["members"])]
    if group_id:
        out = [g for g in out if g["id"] == group_id]
    return out


HISTORICAL_NOTE = ("Historical exploratory results: night-before out-of-sample forecasts on reused seasons (candidate pool "
                   "stages 1–2), at the primary's model-only line. Goalie candidates used the actual starter, known only after the "
                   "start: groups containing them (and the pool stacker) are shown but excluded from the leaderboard. "
                   "These were never recorded live forecasts.")
PROSPECTIVE_NOTE = ("Prospective research: one canonical observation per game (latest cohort made within two hours before the "
                    "start). Research only — nothing here is a recommendation.")
LEADERBOARD_NOTE = ("Exploratory leaderboard: ranked by the multiplicity-adjusted lower bound of the win-rate difference against "
                    "the anchor model at matched coverage (≥ 100 decided picks, ≥ 5% coverage). It is not a recommendation; "
                    "active recommendations come only from the weekly selectors (Selection status).")


def _full(per: dict, m: dict, market: str, historical: bool = False) -> dict:
    """Every registered group's summary and the leaderboard for one set of items."""
    ranked = _Ranked({g["members"][0]: per.get(g["id"], {}) for g in m["groups"] if g["kind"] == "single"}, market)
    summaries = [summarise(g, list(per.get(g["id"], {}).values()), ranked, market, m["anchor"]) for g in m["groups"]]
    return {"summaries": summaries,
            "leaderboard": leaderboard(summaries, market, m["configurations"], _optimistic(m) if historical else frozenset()),
            "games": len({gid for items in per.values() for gid in items}),
            "lines": sorted({v.get("line") for items in per.values() for v in items.values() if v.get("line") is not None})}


def _by_line(per: dict, line) -> dict:
    return per if line is None else {gid: {k: v for k, v in items.items() if v.get("line") == line} for gid, items in per.items()}


@lru_cache(maxsize=32)
def _historical_full(sport: str, market: str, date_from, date_to, line, sha: str, mtime: float) -> dict:
    reg = load_registry()
    per = _by_line(historical_items(sport, market, reg, date_from=date_from, date_to=date_to), line)
    return _full(per, reg["markets"][f"{sport}:{market}"], market, historical=True)


EXACT_NOTE = ("Registered exact rules (exact-rules-v1): chosen in the threshold explorer, registered by the CLI, tracked here "
              "prospectively only: one observation per registered rule and canonical pregame cohort, for games starting after "
              "the rule's registration. The statistic is the minimum member confidence for the agreed side (descriptive, not "
              "an ensemble probability; no Brier or log loss). The explorer's frozen later record is historical and exploratory "
              "and is never merged with this record. Nothing here selects, recommends or promotes.")
EXACT_HISTORICAL_NOTE = ("Registered exact rules have no historical record here: their historical evidence is the explorer's "
                         "frozen later record (exploratory, reused seasons), kept separate from the prospective record.")
EXACT_REGISTRATION = "CLI only (`python -m app.cli exact-rules-register`); the app has no Save or Track control in this milestone"
EXACT_VERSIONS_NOTE = ("`versions` counts model-definition versions (each member's `model_version`: code and configuration). "
                       "A parameter refit with the same configuration keeps its version; the parameter fit is recorded per "
                       "member in the observation only where the forecast row carries one (candidate forecasts: fit id, "
                       "artifact digest, training cutoff); cohort members' prediction rows carry no fit reference, which the "
                       "observation states instead of inventing one.")


def exact_report(conn, sport: str, market: str, *, date_from=None, date_to=None, version=None, line=None,
                 reg: Optional[dict] = None, exact_registry: Optional[dict] = None, load_exact: Optional[bool] = None) -> dict:
    """Prospective record of every registered exact rule of one market: one canonical observation per game (as the sealed
    groups), summarised by the same cells; comparators at matched coverage come from the sealed registry's single groups."""
    from . import exact_rules as er
    load_exact = (reg is None) if load_exact is None else load_exact
    ex, error = _exact_registry(exact_registry, load_exact)
    rules = er.rules_for(ex, sport, market)
    base = {"study": er.STUDY, "registry_version": ex["version"] if ex else 0, "registry_sha256": ex["sha256"] if ex else None,
            "statistic": er.STATISTIC, "kinds": EXACT_SEMANTICS, "registration": EXACT_REGISTRATION, "note": EXACT_NOTE,
            "versions_note": EXACT_VERSIONS_NOTE}
    if error:
        return {**base, "status": "failed", "error": error, "games": 0, "rules": [],
                "reason": "the exact-rule registry file cannot be loaded; registered rules are unknown until it is repaired"}
    if not rules:
        return {**base, "status": "no_registered_rules", "games": 0, "rules": []}
    if not _has_0028(conn):
        return {**base, "status": "schema_required", "games": 0, "rules": [], "registered_rules": [r["id"] for r in rules],
                "reason": "migration 0028 is not applied to this store: registered exact rules are not recorded or reported; "
                          "the store is migrated by the scheduled store commands, never by the API"}
    reg = reg or load_registry()
    m = reg["markets"].get(f"{sport}:{market}") or {"groups": [], "anchor": None}
    per = _by_line(prospective_items(conn, reg, sport, market, date_from, date_to, version,
                                     registries=[r["rule_sha256"] for r in rules]), line)
    singles = [g for g in m["groups"] if g["kind"] == "single"]
    sealed = _by_line(prospective_items(conn, reg, sport, market, date_from, date_to, version), line) if singles else {}
    ranked = _Ranked({g["members"][0]: sealed.get(g["id"], {}) for g in singles}, market)
    out = []
    for r in rules:
        group = {"id": r["id"], "kind": EXACT_KIND, "members": r["members"], "thresholds": r["thresholds"],
                 "cohort": r["cohort"], "priority": "registered exact rule"}
        items = list(per.get(r["id"], {}).values())
        s = summarise(group, items, ranked, market, m.get("anchor"))
        tips = sorted(i["tipoff_at"] for i in items)
        out.append({**s, "member_thresholds": [float(t) for t in r["thresholds"]], "effective_from": r["effective_from"],
                    "registered_at": r["registered_at"], "registered_version": r["registered_version"],
                    "rule_sha256": r["rule_sha256"], "provenance": r.get("provenance"),
                    "first_observed": tips[0] if tips else None, "last_observed": tips[-1] if tips else None})
    games = len({gid for items in per.values() for gid in items})
    return {**base, "status": "recording" if games else "no_observations_yet", "games": games, "rules": out}


def report(conn, sport: str, market: str, *, evidence: str = "prospective", kind=None, member=None, group_id=None,
           date_from=None, date_to=None, version=None, line=None, reg: Optional[dict] = None,
           exact_registry: Optional[dict] = None, load_exact: Optional[bool] = None) -> dict:
    load_exact = (reg is None) if load_exact is None else load_exact
    reg = reg or load_registry()
    m = reg["markets"].get(f"{sport}:{market}")
    from . import exact_rules as er
    exact = ({"study": er.STUDY, "status": "not_applicable", "games": 0, "rules": [], "note": EXACT_HISTORICAL_NOTE,
              "registration": EXACT_REGISTRATION} if evidence == "historical"
             else exact_report(conn, sport, market, date_from=date_from, date_to=date_to, version=version, line=line,
                               reg=reg, exact_registry=exact_registry, load_exact=load_exact))
    if m is None:
        return {"sport": sport, "market": market, "status": "no_registered_groups", "groups": [], "exact_rules": exact}
    if evidence == "historical":
        p = historical_path(sport, market)
        pre = _precomputed(sport, reg["sha256"]) if not (date_from or date_to or line is not None) else None
        full = pre[market] if pre and market in pre else _historical_full(sport, market, date_from, date_to, line, reg["sha256"], p.stat().st_mtime if p.is_file() else 0.0) \
            if reg is load_registry() else _full(_by_line(historical_items(sport, market, reg, date_from, date_to), line), m, market, historical=True)
        status = "available" if full["games"] else "historical_table_outdated" if _outdated(p, reg) else "no_historical_table"
    else:
        full = _full(_by_line(prospective_items(conn, reg, sport, market, date_from, date_to, version), line), m, market)
        status = "recording" if full["games"] else "no_observations_yet"
    keep = {g["id"] for g in _filter_groups(m["groups"], kind, member, group_id)}
    return {"sport": sport, "market": market, "evidence": evidence, "status": status, "study": reg["study"],
            "registry_sha256": reg["sha256"], "effective_from": reg["effective_from"], "anchor": m["anchor"],
            "thresholds": list(report_thresholds(market)), "sealed_thresholds": m["thresholds"], "grid": GRID,
            "bands": [{"band": [lo, min(hi, 1.0)], "label": band_label(market, lo, hi)} for lo, hi in report_bands(market)],
            "registered_groups": len(m["groups"]), "configurations_searched": m["configurations"],
            "skipped_allocation": len(m["skipped"]), "games": full["games"], "lines": full["lines"], "kinds": reg["kinds"],
            "min_evidence": MIN_EVIDENCE, "groups": [g for g in full["summaries"] if g["id"] in keep],
            "leaderboard": full["leaderboard"], "note": HISTORICAL_NOTE if evidence == "historical" else PROSPECTIVE_NOTE,
            "leaderboard_note": LEADERBOARD_NOTE, "exact_rules": exact}


# --- Historical (exploratory) ---------------------------------------------------------------------------------------
def historical_path(sport: str, market: Optional[str] = None) -> Path:
    """One file per market (loading a market never parses the others' tables)."""
    d = config.data_dir() / "research" / "tracking"
    return d / (f"historical-{sport}-{market}.json.gz" if market else f"historical-{sport}.json.gz")


def _walk_stack(rows: list, members: list, market: str, key: str) -> dict:
    """Season-start chronological stacker on earlier seasons' out-of-sample rows (≥ 300 games)."""
    fits = {}
    for s in sorted({r["season"] for r in rows}):
        train = [r for r in rows if r["season"] < s and r["realised"] is not None and all(m in r["vectors"] for m in members)]
        if len(train) < STACK_MIN_TRAIN:
            fits[s] = {"fitted": False, "training_games": len(train)}
            continue
        model = combine.fit_stack([([r["vectors"][m] for m in members], r["realised"]) for r in train], market)
        for r in rows:
            if r["season"] == s and all(m in r["vectors"] for m in members):
                r["vectors"][key] = combine.stack(model, [r["vectors"][m] for m in members])
        fits[s] = {"fitted": True, "training_games": model["n"]}
    return fits


def build_historical(sport: str, reg: Optional[dict] = None, log=print) -> Path:
    """Classify every registered group on the saved out-of-sample tables (no refit of any member); exploratory."""
    reg = reg or load_registry()
    oof = _oof(sport)
    payload = {"study": STUDY, "registry_sha256": reg["sha256"], "sport": sport, "built_at": utc_now(), "markets": {}}
    built = []
    for market, rows in oof.items():
        m = reg["markets"].get(f"{sport}:{market}")
        if m is None:
            continue
        rows = [dict(r, vectors=dict(r["vectors"])) for r in rows]
        fits = {"core": _walk_stack(rows, m["stack_inputs"], market, "stack:core")}
        if m["shadow"]:
            fits["expanded"] = _walk_stack(rows, m["stack_inputs"] + m["shadow"], market, "stack:expanded")
        pool = None
        if m.get("pool_stacker"):
            pool = m["pool_stacker"]["members"]
            fits["pool"] = _walk_stack(rows, pool, market, "stack_pool")
        games = [{"game_id": r["game_id"], "season": r["season"], "tipoff_at": r["tipoff_at"], "line": r["line"],
                  "realised": r["realised"]} for r in rows]
        groups = {}
        for g in m["groups"]:
            cls = []
            for r in rows:
                if g["kind"] == "stack_pool":
                    v = r["vectors"].get("stack_pool")
                    res = classify({"kind": "single", "members": ["stack_pool"]}, {"stack_pool": v} if v else {}, market)
                    if v is None:
                        res["missing"] = {"pool_stacker": "no chronological fit for this season"}
                else:
                    res = classify(g, r["vectors"], market)
                    if res["status"] == "incomplete":
                        res["missing"] = {k: ("no historical inputs (prospective-only member)" if k in m["core"] + m["candidates"]
                                              + m["shadow"] else "no chronological fit for this season")
                                          for k in res["missing"]}
                vec = res["vector"]
                cls.append([res["status"][0] if res["status"] != "no_majority" else "n", res["side"],
                            _stored(res["statistic"]),
                            None if vec is None else [round(x, 5) for x in vec], sorted(res["missing"]) or None])
            groups[g["id"]] = cls
        mp = historical_path(sport, market)
        mp.parent.mkdir(parents=True, exist_ok=True)
        body = {"study": STUDY, "registry_sha256": reg["sha256"], "calc": CALC, "sport": sport, "market": market,
                "built_at": payload["built_at"], "games": games, "groups": groups, "stack_fits": fits}
        mp.with_suffix(".tmp").write_bytes(gzip.compress(json.dumps(body, separators=(",", ":")).encode(), mtime=0))
        mp.with_suffix(".tmp").replace(mp)
        built.append(market)
        log(f"{sport}:{market}: {len(games)} games, {len(groups)} groups")
    _historical.cache_clear()
    summaries = {"registry_sha256": reg["sha256"], "calc": CALC, "grid": GRID, "markets": {}, "checks": {}}
    for market in built:     # unfiltered summaries and the chronological check, so the API needs no recomputation
        summaries["markets"][market] = _full(historical_items(sport, market, reg), reg["markets"][f"{sport}:{market}"], market, historical=True)
        summaries["checks"][market] = chronological_check(sport, market, reg)
    _historical.cache_clear()
    path = historical_path(sport, built[0]) if built else historical_path(sport)
    sp = summaries_path(sport)
    sp.with_suffix(".tmp").write_bytes(gzip.compress(json.dumps(summaries, separators=(",", ":")).encode(), mtime=0))
    sp.with_suffix(".tmp").replace(sp)
    return path


def summaries_path(sport: str) -> Path:
    return config.data_dir() / "research" / "tracking" / f"summaries-{sport}.json.gz"


@lru_cache(maxsize=2)
def _summaries(path: str, mtime: float) -> dict:
    return json.loads(gzip.decompress(Path(path).read_bytes()))


def _precomputed(sport: str, sha: str, part: str = "markets") -> Optional[dict]:
    p = summaries_path(sport)
    if not p.is_file():
        return None
    d = _summaries(str(p), p.stat().st_mtime)
    if d.get("registry_sha256") != sha or d.get("calc") != CALC:      # another registry or an earlier calculation: stale
        return None
    # Summaries depend on the reporting grid; the chronological check uses sealed thresholds only and stays valid.
    return d.get(part) if part == "checks" or d.get("grid") == GRID else None


def reserved_seasons(sport: str) -> set:
    """The sport's reserved final-test seasons. A research fold in one of them reuses those seasons: it is exploratory and
    never a second final test (the frozen primary model's one-time test is separate and unaffected)."""
    if sport == "nhl":
        return set(nhl_eval.PERIODS["test"])
    from . import backtest
    return set(backtest.PERIODS["test"])


def _flag_reserved(folds: list, sport: str) -> list:
    reserved = reserved_seasons(sport)
    return [{**f, "reserved_test_season": f.get("season") in reserved} for f in folds]


def precomputed_check(sport: str, market: str) -> dict:
    """The chronological check saved when the historical tables were built (computing it needs the whole table)."""
    reg = load_registry()
    checks = _precomputed(sport, reg["sha256"], "checks") or {}
    out = checks.get(market) or {"status": _missing_status(sport, market, reg), "folds": []}
    return {**out, "folds": _flag_reserved(out.get("folds", []), sport)}


def _missing_status(sport: str, market: str, reg: dict) -> str:
    """Why no historical result is served: no table at all, or one that is outdated (visible, never an empty result)."""
    return "historical_table_outdated" if _outdated(historical_path(sport, market), reg) else "no_historical_table"


def _outdated(p: Path, reg: dict) -> bool:
    """A table file exists but was built for another registry or under an earlier calculation: rebuild it
    (`tracking-historical`); it is never read."""
    if not p.is_file():
        return False
    mk = _historical(str(p), p.stat().st_mtime)
    return mk.get("registry_sha256") != reg["sha256"] or mk.get("calc") != CALC


@lru_cache(maxsize=1)
def _historical(path: str, mtime: float) -> dict:
    return json.loads(gzip.decompress(Path(path).read_bytes()))


STATUS_CODE = {"p": "pick", "d": "disagree", "t": "tie", "n": "no_majority", "i": "incomplete"}


def historical_items(sport: str, market: str, reg: dict, date_from=None, date_to=None) -> dict:
    p = historical_path(sport, market)
    if not p.is_file():
        return {}
    mk = _historical(str(p), p.stat().st_mtime)
    if mk.get("registry_sha256") != reg["sha256"] or mk.get("calc") != CALC:
        return {}
    lo, hi = day_bounds(date_from, date_to)
    keep = [i for i, g in enumerate(mk["games"]) if (lo is None or g["tipoff_at"] >= lo) and (hi is None or g["tipoff_at"] < hi)]
    out = {}
    for gid, cls in mk["groups"].items():
        items = {}
        for i in keep:
            g, c = mk["games"][i], cls[i]
            items[g["game_id"]] = {"game_id": g["game_id"], "status": STATUS_CODE[c[0]], "side": c[1], "statistic": c[2],
                                   "vector": c[3], "missing": {k: True for k in (c[4] or [])}, "state": "settled",
                                   "realised": g["realised"], "line": g["line"], "tipoff_at": g["tipoff_at"],
                                   "season": g["season"]}
        out[gid] = items
    return out


def chronological_check(sport: str, market: str, reg: Optional[dict] = None) -> dict:
    """Historical, exploratory: for each later season, the configuration ranked first on earlier seasons only (leaderboard
    rule) and its result on that season — selection on earlier games, evaluation on later games."""
    reg = reg or load_registry()
    m = reg["markets"].get(f"{sport}:{market}")
    per = historical_items(sport, market, reg)
    if not m or not per:
        return {"status": _missing_status(sport, market, reg), "folds": []}
    seasons = sorted({i["season"] for items in per.values() for i in items.values()})
    folds = []
    for s in seasons[2:]:
        earlier = {gid: {k: v for k, v in items.items() if v["season"] < s} for gid, items in per.items()}
        later = {gid: {k: v for k, v in items.items() if v["season"] == s} for gid, items in per.items()}
        singles_e = _Ranked({g["members"][0]: earlier.get(g["id"], {}) for g in m["groups"] if g["kind"] == "single"}, market)
        board = leaderboard([summarise(g, list(earlier.get(g["id"], {}).values()), singles_e, market, m["anchor"])
                             for g in m["groups"]], market, m["configurations"], _optimistic(m))
        if not board:
            folds.append({"season": s, "chosen": None, "reason": "no configuration with enough earlier evidence"})
            continue
        top = board[0]
        g = next(x for x in m["groups"] if x["id"] == top["id"])
        singles_l = _Ranked({x["members"][0]: later.get(x["id"], {}) for x in m["groups"] if x["kind"] == "single"}, market)
        res = summarise(g, list(later.get(g["id"], {}).values()), singles_l, market, m["anchor"])
        cell = next(c for c in res["thresholds"] if c["threshold"] == top["threshold"])
        folds.append({"season": s, "chosen": top["id"], "threshold": top["threshold"],
                      "earlier": {k: top[k] for k in ("decided", "coverage", "win_rate", "lift", "lower_adjusted")},
                      "later": {"decided": cell["decided"], "coverage": cell["coverage"], "win_rate": cell["win_rate"],
                                "ci95": cell["ci95"], "matched": cell["matched"][:1]}})
    return {"status": "ok", "folds": folds,
            "note": "Exploratory (reused seasons). Shows whether a configuration chosen on earlier seasons held up later."}


def game_observations(conn, game_id: str, reg: Optional[dict] = None, exact_registry: Optional[dict] = None,
                      load_exact: Optional[bool] = None) -> dict:
    """The canonical (latest pregame cohort) tracking-v2 observations of one game, per market: how many groups pick each
    side, abstain or are incomplete, and the groups' picks with their rule statistic (registered exact rules included,
    under their own digests). Research only."""
    load_exact = (reg is None) if load_exact is None else load_exact
    reg = reg or load_registry()
    if not _has(conn, "agreement_observations"):
        return {"game_id": game_id, "status": "migration 0021 not applied", "markets": []}
    ex, exact_error = _exact_registry(exact_registry, load_exact)       # a broken file: sealed groups only, error named
    regs = [reg["sha256"], *[r["rule_sha256"] for r in (ex["rules"] if ex else [])]]
    rows = conn.execute(
        "SELECT o.* FROM agreement_observations o JOIN research_cohorts c ON c.cohort_id=o.source_cohort_id "
        f"WHERE o.registry IN ({','.join('?' * len(regs))}) AND o.game_id=? ORDER BY c.generated_at, o.source_cohort_id, o.observation_id",
        (*regs, game_id)).fetchall()
    # One cohort per market: the market's latest core cohort with observations, so counts never mix cohorts or lines.
    newest = {}
    for r in rows:
        newest[r["market"]] = r["cohort_id"]
    latest: dict = {}
    for r in rows:
        if r["cohort_id"] == newest[r["market"]]:
            latest[(r["market"], r["group_id"])] = r
    markets = defaultdict(list)
    for (market, _), r in latest.items():
        markets[market].append(r)
    out = []
    for market, rs in markets.items():
        counts = Counter(r["side"] if r["status"] == "pick" else r["status"] for r in rs)
        picks = sorted((r for r in rs if r["status"] == "pick"), key=lambda r: -r["statistic"])
        out.append({"market": market, "line": rs[0]["line"], "groups": len(rs), "counts": dict(counts),
                    "recorded_at": max(r["recorded_at"] for r in rs), "definition": rs[0]["definition"],
                    "picks": [{"group": r["group_id"], "kind": r["kind"], "side": r["side"], "statistic": r["statistic"],
                               "votes": json.loads(r["votes"])} for r in picks[:12]],
                    "incomplete": [{"group": r["group_id"], "missing": json.loads(r["missing"])}
                                   for r in rs if r["status"] == "incomplete"][:12]})
    order = {m: i for i, m in enumerate(sports.get(reg_sport(conn)).markets)}
    return {"game_id": game_id, "registry_sha256": reg["sha256"], "status": "recorded" if out else "no_observations",
            "markets": sorted(out, key=lambda x: order.get(x["market"], 99)), "exact_rules_error": exact_error,
            "note": "Research classifications from member forecasts stored before the start; not recommendations."}


def reg_sport(conn) -> str:
    return conn.execute("SELECT sport FROM store_identity").fetchone()[0]


# Joint exploration reuses original vectors; old sealed classifications and decisions are unchanged.
JOINT_CALC = "joint-1"


def joint_classify(vectors, members, market, confidence="supporters_mean"):
    missing = [m for m in members if m not in vectors]
    out = {"status": "incomplete", "side": None, "statistic": None, "vector": None,
           "agreement": None, "votes": {}, "missing": {m: "member forecast missing" for m in missing}}
    if missing:
        return out
    vecs = [vectors[m] for m in members]
    if any(len(v) != 3 or any(not math.isfinite(x) or x < 0 or x > 1 for x in v)
           or abs(sum(v) - 1) > 1e-5 for v in vecs):
        return {**out, "missing": {"probabilities": "invalid probability vector"}}
    picks = [combine.pick(v, market) for v in vecs]
    counts = {s: picks.count(s) for s in combine.sides(market)}
    top = max(counts.values())
    leaders = [s for s, n in counts.items() if n == top]
    out["votes"] = {SIDE_NAMES[s]: n for s, n in counts.items()} | {"n": len(members), "abstain": picks.count(None)}
    out["agreement"] = top / len(members)
    if len(leaders) != 1 or top <= len(members) / 2:
        return {**out, "status": "no_majority"}
    side = leaders[0]
    support = [v for v, pick in zip(vecs, picks) if pick == side]
    values = [v[side] for v in (vecs if confidence == "all_mean" else support)]
    stat = min(values) if confidence == "supporters_min" else sum(values) / len(values)
    return {**out, "status": "pick", "side": side, "statistic": stat,
            "vector": combine.average(vecs), "missing": {}}


def joint_rows(conn, sport, market, evidence, version=None, line=None, date_from=None, date_to=None):
    """Original OOF vectors or latest per-game core cohort with its exact paired inputs. No writes."""
    lo, hi = day_bounds(date_from, date_to)
    if evidence == "historical":
        rows = [{**r, "state": "settled", "definition": "historical-oof"} for r in _oof(sport).get(market, [])]
    else:
        rows = []
        if not _has(conn, "research_cohorts"):
            return rows
        canonical = _canonical_cohorts(conn, market, version)
        for gid, cid in canonical.items():
            c = conn.execute("SELECT * FROM research_cohorts WHERE cohort_id=?", (cid,)).fetchone()
            if version and not c["definition"].startswith(version):
                continue
            if not 0 < (_parse_joint(c["tipoff_at"]) - _parse_joint(c["generated_at"])).total_seconds() <= 7200:
                continue
            e = conn.execute("SELECT * FROM research_cohorts WHERE game_id=? AND market=? AND line IS ? "
                             "AND information_at=? AND definition=? AND tipoff_at=? AND generated_at < tipoff_at "
                             "ORDER BY generated_at DESC,cohort_id DESC LIMIT 1",
                             (gid, market, c["line"], c["information_at"], c["definition"] + ":expanded-v1", c["tipoff_at"])).fetchone()
            vectors, info, _ = _inputs(conn, c, e, utc_now())
            game = conn.execute("SELECT * FROM games WHERE game_id=?", (gid,)).fetchone()
            state = _state({"status": game["status"], "game_tipoff_at": game["tipoff_at"],
                            "tipoff_at": c["tipoff_at"], "result_available_at": game["result_available_at"]})
            realised = None
            if state == "settled":
                from . import markets as mk
                d = conn.execute("SELECT decision FROM game_decisions WHERE game_id=?", (gid,)).fetchone()
                result = mk.settle(market, c["line"], status=game["status"], home=game["home_score"],
                                   away=game["away_score"], decision=d[0] if d else None)
                realised = {"a": 0, "mid": 1, "b": 2}.get(result)
            rows.append({"game_id": gid, "tipoff_at": c["tipoff_at"], "season": game["season"], "line": c["line"],
                         "vectors": vectors, "state": state, "realised": realised, "definition": c["definition"],
                         "versions": {m: x["version"] for m, x in info.items()}, "cohort_id": cid})
    return [r for r in rows if (line is None or r["line"] == line)
            and (lo is None or r["tipoff_at"] >= lo) and (hi is None or r["tipoff_at"] < hi)]


def _parse_joint(value):
    from datetime import datetime
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def joint_cell(items, market, agreement_min, agreement_max, confidence_min, confidence_max):
    filtered = [i for i in items if i["agreement"] is not None and i["agreement"] + 1e-9 >= agreement_min
                and (agreement_max is None or i["agreement"] < agreement_max - 1e-9)]
    return _Picks(filtered, market, "majority").cell(confidence_min, confidence_max, len(items))


def joint_walk(items, rows, members, market):
    """Freeze both comparator and joint rule on earlier seasons; evaluate the unchanged rule later."""
    seasons = sorted({r["season"] for r in rows})
    fractions = [k / len(members) for k in range(len(members) // 2 + 1, len(members) + 1)]
    agreement_ranges = [(lo, hi) for i, lo in enumerate(fractions) for hi in [*fractions[i + 1:], None]]
    rules = [(a, b, lo, None if hi is not None and hi > 1 else hi) for a, b in agreement_ranges
             for lo, hi in [(t, None) for t in report_thresholds(market)] + report_bands(market)]
    singles = {m: [{**r, **classify({"kind": "single", "members": [m]}, r["vectors"], market)}
                   for r in rows] for m in members}
    folds = []
    for season in seasons[2:]:
        earlier = [i for i in items if i["season"] < season]
        later = [i for i in items if i["season"] == season]
        # Compare on the universe where all requested members were available, including disagreement games.
        eligible = {i["game_id"] for i in earlier if i["status"] != "incomplete"}
        ranks = _Ranked({m: {i["game_id"]: i for i in rs if i["season"] < season and i["game_id"] in eligible}
                         for m, rs in singles.items()}, market)
        refs = [(ranks.matched(m, len(eligible), eligible), m) for m in members]
        valid = [(r, m) for r, m in refs if r["games"]]
        if not valid:
            folds.append({"season": season, "chosen": None, "reason": "no earlier complete evidence"})
            continue
        _, best = max(valid, key=lambda x: (x[0]["win_rate"], x[1]))
        choices = []
        # Cache each vote band once; confidence thresholds then use the existing suffix-sum index.
        indexed = {(a, b): _Picks([i for i in earlier if i["agreement"] is not None and
                    i["agreement"] + 1e-9 >= a and (b is None or i["agreement"] < b - 1e-9)], market, "majority")
                   for a, b in agreement_ranges}
        for a, b, lo, hi in rules:
            cell = indexed[a, b].cell(lo, hi, len(earlier))
            ref = ranks.matched(best, cell["decided"], eligible) if cell["decided"] else None
            if cell["decided"] >= MIN_EVIDENCE and (cell["coverage"] or 0) >= MIN_COVERAGE and ref and ref["games"] == cell["decided"]:
                choices.append((cell["win_rate"] - ref["win_rate"], cell["decided"], a, b, lo, hi, cell))
        if not choices:
            folds.append({"season": season, "chosen": None, "reason": "insufficient earlier evidence"})
            continue
        _, _, a, b, lo, hi, train = max(choices, key=lambda x: (*x[:3], x[3] or 2, x[4], x[5] or 2))
        test = joint_cell(later, market, a, b, lo, hi)
        games = {i["game_id"] for i in later if i["status"] != "incomplete"}
        rank = _Ranked({best: {i["game_id"]: i for i in singles[best] if i["season"] == season}}, market)
        ref = rank.matched(best, test["decided"], games) if test["decided"] else {"model": best, "games": 0, "win_rate": None}
        folds.append({"season": season, "chosen": {"agreement_min": a, "agreement_max": b, "confidence_min": lo,
                      "confidence_max": hi, "comparator": best}, "earlier": train, "later": test, "matched": ref})
    return {"folds": folds, "configurations": len(rules), "note": "Exploratory reused seasons; rules and comparator chosen on earlier seasons. Not a live recommendation."}


def joint_report(conn, sport, market, *, evidence="historical", members=None, confidence="supporters_mean",
                 agreement_min=0.5, agreement_max=None, confidence_min=0.5, confidence_max=None,
                 version=None, line=None, date_from=None, date_to=None, evaluate=False):
    if confidence not in ("supporters_mean", "all_mean", "supporters_min"):
        raise InvalidRequest("Unknown confidence definition")
    rows = joint_rows(conn, sport, market, evidence, version, line, date_from, date_to)
    options = sorted({m for r in rows for m in r["vectors"] if not m.startswith("stack")})
    reg = load_registry()["markets"].get(f"{sport}:{market}", {})
    options = sorted(set(options) | set(reg.get("core", [])) | set(reg.get("shadow", [])) | set(reg.get("candidates", [])))
    members = list(dict.fromkeys(members or []))
    base = {"calculation": JOINT_CALC, "sport": sport, "market": market, "evidence": evidence, "members": members,
            "options": options, "lines": sorted({r["line"] for r in rows if r["line"] is not None}),
            "definitions": sorted({r["definition"] for r in rows}), "confidence": confidence,
            "agreement_min": agreement_min, "agreement_max": agreement_max, "confidence_min": confidence_min,
            "confidence_max": confidence_max, "games": len(rows), "recommendation": False,
            "note": "Agreement counts votes; confidence is a separate probability filter. Derived from original forecasts; no new predictions. " +
                    ("Historical OOF results are exploratory on reused seasons and model-defined lines, not bookmaker lines." if evidence == "historical" else
                     "Latest pregame cohort per game. Research classifications, not pre-registered recommendations. Definitions remain separate below.")}
    if len(members) < 2:
        return {**base, "status": "choose_models", "by_definition": [], "recent": []}
    if not 2 <= len(members) <= 8 or any(m not in options for m in members):
        raise InvalidRequest("Choose between 2 and 8 available models")
    if evidence == "historical" and any("goalie" in m for m in members):
        return {**base, "status": "invalid_historical_inputs", "by_definition": [], "recent": [],
                "note": "This subset uses actual-starter historical goalie inputs. Historical evaluation is refused; prospective tracking remains available."}
    items = [{**r, **joint_classify(r["vectors"], members, market, confidence)} for r in rows]
    # Never pool distinct live method definitions or member versions into one win-rate.
    for i in items:
        i["series"] = i["definition"] if evidence == "historical" else i["definition"] + ":" + _key_digest(
            [(m, i.get("versions", {}).get(m)) for m in members])[:12]
    groups = []
    for definition in sorted({i["series"] for i in items}):
        part = [i for i in items if i["series"] == definition]
        groups.append({"definition": definition, "games": len(part), "status_counts": dict(Counter(i["status"] for i in part)),
                       "cell": joint_cell(part, market, agreement_min, agreement_max, confidence_min, confidence_max)})
    recent = [{**{k: i.get(k) for k in ("game_id", "tipoff_at", "line", "state", "status", "side", "agreement", "statistic", "votes", "missing", "series")},
               "matches_filter": bool(joint_cell([i], market, agreement_min, agreement_max, confidence_min, confidence_max)["selected"])}
              for i in sorted(items, key=lambda i: (i["tipoff_at"], i["game_id"]))[-20:]]
    walk = joint_walk(items, rows, members, market) if evaluate and evidence == "historical" else None
    if walk:
        walk = {**walk, "folds": _flag_reserved(walk["folds"], sport)}
    return {**base, "status": "available" if items else "no_records", "by_definition": groups, "recent": recent, "walk": walk}
