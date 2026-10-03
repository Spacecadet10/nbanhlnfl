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
from collections import Counter, defaultdict
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from typing import Optional

from . import combine, config, nhl_eval, selector, sports
from .db import transaction, utc_now

STUDY = "tracking-v2"
REGISTRY_PATH = Path(__file__).parent / "models" / "tracking_groups.json"
CAP = 500                                   # new rule configurations (groups × thresholds) per sport and market
THRESHOLDS = {"winner": (0.55, 0.60, 0.65, 0.70, 0.75, 0.80), "reg3": (0.40, 0.45, 0.50, 0.55, 0.60),
              "line": (0.55, 0.60, 0.65, 0.70)}
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


EXTRA_MODULES = {"research": "ngboost"}      # optional dependency group -> importable module (candidates/registry.py)


class RegistryError(ValueError):
    pass


def kind_of(market: str) -> str:
    return "winner" if market == "winner" else "reg3" if market == "reg3" else "line"


def bands(market: str) -> list[list[float]]:
    """Non-overlapping confidence bands [lo, hi): below the first threshold, between thresholds, at or above the last."""
    t = THRESHOLDS[kind_of(market)]
    edges = [0.0, *t, 1.0 + 1e-9]
    return [[edges[i], edges[i + 1]] for i in range(len(edges) - 1)]


def band_label(market: str, lo: float, hi: float) -> str:
    floor = 1 / 3 if market == "reg3" else 0.5
    left = f"{floor:.2f}" if lo == 0.0 else f"{lo:.2f}"
    return f"{left}–{hi:.2f}" if hi <= 1.0 else f"≥ {lo:.2f}"


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
        if live_ok and s.extra and importlib.util.find_spec(EXTRA_MODULES.get(s.extra, s.extra)) is None:
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
        if len(leaders) > 1:
            out["status"] = "tie"
        elif top <= len(picks) / 2:
            out["status"] = "no_majority"
        else:
            s = leaders[0]
            out["side"], out["statistic"] = s, sum(v[s] for v in vecs) / len(vecs)
        return out
    raise ValueError(kind)


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


def _inputs(conn, core_row, expanded_row) -> tuple[dict, dict, str]:
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
                "AND f.tipoff_at=? AND f.generated_at < f.tipoff_at AND f.forecast_id = (SELECT x.forecast_id FROM "
                "candidate_forecasts x WHERE x.game_id=f.game_id AND x.market=f.market AND x.line_ref=f.line_ref "
                "AND x.candidate=f.candidate AND x.generated_at < x.tipoff_at ORDER BY x.generated_at DESC, x.forecast_id DESC LIMIT 1)",
                (core_row["game_id"], core_row["market"], f"research_cohorts:{core_row['cohort_id']}", core_row["line"],
                 core_row["tipoff_at"])):
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


def record(conn, *, game_ids: list, now: Optional[str] = None, write: bool = True, reg: Optional[dict] = None) -> dict:
    """Observations for every registered group and every pregame core cohort of the games not yet observed. Idempotent
    (UNIQUE registry, group, source cohort); immutable (triggers). Never raises: failures are reported."""
    now = now or utc_now()
    out = {"status": "success", "registry": None, "created": 0, "already_recorded": 0, "cohorts": 0, "errors": {}}
    try:
        reg = reg or load_registry()
        out["registry"] = reg["sha256"][:16]
        if not _has(conn, "agreement_observations"):
            return {**out, "status": "skipped", "reason": "migration 0021 not applied"}
        sport = conn.execute("SELECT sport FROM store_identity").fetchone()[0]
        rows = []
        for core, exp in _pairs(conn, game_ids, now):
            m = reg["markets"].get(f"{sport}:{core['market']}")
            if m is None:
                continue
            out["cohorts"] += 1
            try:
                vectors, info, latest = _inputs(conn, core, exp)
                if latest >= core["tipoff_at"]:
                    out["errors"][f"{core['game_id']}:{core['market']}"] = "an input is not earlier than the start"
                    continue
                for g in m["groups"]:
                    src = exp if g["cohort"] == "expanded" else core
                    if src is None:
                        res = {"status": "incomplete", "side": None, "statistic": None, "vector": None, "votes": None,
                               "missing": {"expanded_cohort": "no expanded cohort recorded with this core cohort"}}
                        src = core
                    else:
                        res = classify(g, vectors, core["market"], m.get("pool_stacker"))
                    names = [x for x in g["members"] if x in info] if g["kind"] != "stack" else []
                    mem = [info[x] for x in names]
                    sig = _key_digest(sorted((x["name"], x["version"]) for x in mem), g["kind"],
                                      src["definition"] if g["kind"] == "stack" else None)
                    info_at = max([src["information_at"], core["information_at"]] +
                                  [x["generated_at"] for x in mem if x["ref"] and x["ref"].get("table") == "candidate_forecasts"])
                    rows.append((reg["sha256"], g["id"], sport, core["game_id"], core["market"], core["line"], core["line_source"],
                                 core["cohort_id"], src["cohort_id"], src["definition"], g["kind"], json.dumps(mem),
                                 json.dumps(res["missing"], sort_keys=True), sig, res["status"],
                                 None if res["side"] is None else SIDE_NAMES[res["side"]], res["statistic"],
                                 json.dumps(res["vector"]), json.dumps(res["votes"]), info_at, core["tipoff_at"], now))
            except Exception as exc:     # one cohort never blocks the others
                out["errors"][f"{core['game_id']}:{core['market']}"] = f"{type(exc).__name__}: {exc}"
        if write and rows:
            with transaction(conn):
                for r in rows:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO agreement_observations (registry, group_id, sport, game_id, market, line, "
                        "line_source, cohort_id, source_cohort_id, definition, kind, members, missing, version_sig, status, side, "
                        "statistic, vector, votes, information_at, tipoff_at, recorded_at) VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
                    out["created" if cur.rowcount else "already_recorded"] += 1
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


def _cell(items: list, eligible: int, market: str, kind: str, select) -> dict:
    """items: per game {state, realised, side, statistic, vector}; `select` decides membership (threshold or band)."""
    chosen = [i for i in items if i["side"] is not None and select(i["statistic"])]
    c = Counter()
    briers, lls, confs = [], [], []
    for i in chosen:
        if i["state"] == "void":
            c["voids"] += 1
        elif i["state"] != "settled":
            c["pending"] += 1
        elif i["realised"] is None:
            c["unscorable"] += 1
        else:
            o = selector.outcome(market, i["side"], i["realised"])
            c[{"win": "wins", "loss": "losses", "push": "pushes"}[o]] += 1
            if o != "push":
                confs.append((i["statistic"], o == "win"))
            if kind in PROBABILISTIC and i.get("vector"):
                b, ll = _scores(i["vector"], i["realised"])
                briers.append(b)
                lls.append(ll)
    n = c["wins"] + c["losses"]
    return {"selected": len(chosen), "settled": c["wins"] + c["losses"] + c["pushes"], "pending": c["pending"],
            "wins": c["wins"], "losses": c["losses"], "pushes": c["pushes"], "voids": c["voids"], "unscorable": c["unscorable"],
            "coverage": len(chosen) / eligible if eligible else None, "decided": n,
            "win_rate": c["wins"] / n if n else None, "ci95": nhl_eval._wilson(c["wins"], n),
            "mean_confidence": sum(p for p, _ in confs) / len(confs) if confs else None,
            "brier": sum(briers) / len(briers) if briers else None, "log_loss": sum(lls) / len(lls) if lls else None,
            "evidence": "sufficient" if n >= MIN_EVIDENCE else "insufficient evidence"}


def _matched(singles: dict, name: str, decided: int, market: str, games: set) -> dict:
    """The single model's `decided` most confident settled, decided games among the same eligible games."""
    ranked = sorted((i for gid, i in singles.get(name, {}).items() if gid in games and i["side"] is not None
                     and i["state"] == "settled" and i["realised"] is not None),
                    key=lambda i: (-i["statistic"], i["game_id"]))
    flags = []
    for i in ranked:
        o = selector.outcome(market, i["side"], i["realised"])
        if o == "push":
            continue
        flags.append(o == "win")
        if len(flags) >= decided:
            break
    return {"model": name, "games": len(flags), "win_rate": sum(flags) / len(flags) if flags else None}


def summarise(group: dict, items: list, singles: dict, market: str, anchor: str) -> dict:
    """Per group: status counts, missing reasons, cumulative thresholds, bands, probabilistic scores, matched singles."""
    t_list = THRESHOLDS[kind_of(market)]
    eligible = len(items)
    st = Counter(i["status"] for i in items)
    miss = Counter(k for i in items for k in (i.get("missing") or {}))
    games = {i["game_id"] for i in items}
    comparators = list(dict.fromkeys(([anchor] if anchor else []) + [m for m in group["members"] if m in singles]))
    floor = 1 / 3 if market == "reg3" else 0.5
    cum = []
    for t in (floor, *t_list):
        cell = _cell(items, eligible, market, group["kind"], lambda s, t=t: s + 1e-9 >= t)
        cell["threshold"] = None if t == floor else t
        cell["matched"] = [_matched(singles, x, cell["decided"], market, games) for x in comparators] if cell["decided"] else []
        cum.append(cell)
    bnd = []
    for lo, hi in bands(market):
        cell = _cell(items, eligible, market, group["kind"], lambda s, lo=lo, hi=hi: lo - 1e-9 <= s < hi - 1e-9)
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


def leaderboard(groups: list, market: str, searched: int) -> list:
    """Exploratory ranking of (group, threshold) configurations with ≥ MIN_EVIDENCE decided picks and ≥ 5% coverage by the
    Bonferroni lower bound (over every configuration searched in the market) of the win-rate difference against the
    market's anchor model at matched coverage. Never a recommendation."""
    from statistics import NormalDist
    z = NormalDist().inv_cdf(1 - 0.05 / max(1, searched))
    out = []
    for g in groups:
        for cell in g["thresholds"]:
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


# --- Prospective report ---------------------------------------------------------------------------------------------
def _state(row) -> str:
    if row["status"] in ("postponed", "canceled") or row["game_tipoff_at"] != row["tipoff_at"]:
        return "void"
    if row["status"] != "final" or not row["result_available_at"]:
        return "pending"
    return "settled"


def prospective_items(conn, reg: dict, sport: str, market: str, date_from=None, date_to=None, version=None) -> dict:
    """group_id -> {game_id: item} from the canonical observation per game (latest pregame source cohort)."""
    from . import markets as mk
    if not _has(conn, "agreement_observations"):
        return {}
    sql = ("SELECT o.*, g.status AS g_status, g.home_score, g.away_score, g.result_available_at, g.tipoff_at AS game_tipoff_at, "
           "d.decision, c.generated_at AS cohort_generated_at FROM agreement_observations o JOIN games g ON g.game_id=o.game_id "
           "JOIN research_cohorts c ON c.cohort_id=o.source_cohort_id LEFT JOIN game_decisions d ON d.game_id=o.game_id "
           "WHERE o.registry=? AND o.market=?")
    params = [reg["sha256"], market]
    if date_from:
        sql += " AND o.tipoff_at >= ?"
        params.append(f"{date_from}T00:00:00Z")
    if date_to:
        sql += " AND o.tipoff_at <= ?"
        params.append(f"{date_to}T23:59:59Z")
    if version:
        sql += " AND (o.definition LIKE ? OR o.version_sig = ?)"
        params += [f"{version}%", version]
    out: dict = defaultdict(dict)
    for r in conn.execute(sql + " ORDER BY c.generated_at, o.source_cohort_id", params):
        row = {"status": r["g_status"], "game_tipoff_at": r["game_tipoff_at"], "tipoff_at": r["tipoff_at"],
               "result_available_at": r["result_available_at"]}
        state = _state(row)
        realised = None
        if state == "settled":
            s = mk.settle(market, r["line"], status=r["g_status"], home=r["home_score"], away=r["away_score"], decision=r["decision"])
            realised = {"a": 0, "mid": 1, "b": 2}.get(s)
        vec = json.loads(r["vector"])
        out[r["group_id"]][r["game_id"]] = {      # later cohorts overwrite earlier ones: the canonical latest pregame cohort
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


def report(conn, sport: str, market: str, *, evidence: str = "prospective", kind=None, member=None, group_id=None,
           date_from=None, date_to=None, version=None, line=None, reg: Optional[dict] = None) -> dict:
    reg = reg or load_registry()
    key = f"{sport}:{market}"
    m = reg["markets"].get(key)
    if m is None:
        return {"sport": sport, "market": market, "status": "no_registered_groups", "groups": []}
    if evidence == "historical":
        per = historical_items(sport, market, reg, date_from=date_from, date_to=date_to)
        note = ("Historical exploratory results: night-before out-of-sample forecasts on reused seasons (candidate pool "
                "stages 1–2), at the primary's model-only line; goalie candidates used the actual starter (optimistic). "
                "These were never recorded live forecasts.")
    else:
        per = prospective_items(conn, reg, sport, market, date_from, date_to, version)
        note = ("Prospective research: one canonical observation per game (latest cohort made within two hours before the "
                "start). Research only — nothing here is a recommendation.")
    if line is not None:
        per = {gid: {k: v for k, v in items.items() if v.get("line") == line} for gid, items in per.items()}
    lines = sorted({v.get("line") for items in per.values() for v in items.values() if v.get("line") is not None})
    singles = {g["members"][0]: per.get(g["id"], {}) for g in m["groups"] if g["kind"] == "single"}
    selected = _filter_groups(m["groups"], kind, member, group_id)
    groups = [summarise(g, list(per.get(g["id"], {}).values()), singles, market, m["anchor"]) for g in selected]
    searched = m["configurations"]
    games = len({gid for items in per.values() for gid in items})
    return {"sport": sport, "market": market, "evidence": evidence, "study": reg["study"], "registry_sha256": reg["sha256"],
            "effective_from": reg["effective_from"], "anchor": m["anchor"], "thresholds": m["thresholds"],
            "bands": [{"band": [lo, min(hi, 1.0)], "label": band_label(market, lo, hi)} for lo, hi in m["bands"]],
            "registered_groups": len(m["groups"]), "configurations_searched": searched, "skipped_allocation": len(m["skipped"]),
            "games": games, "lines": lines, "kinds": reg["kinds"], "min_evidence": MIN_EVIDENCE,
            "groups": groups, "leaderboard": leaderboard(groups if not (kind or member or group_id) else
                                                         [summarise(g, list(per.get(g["id"], {}).values()), singles, market, m["anchor"])
                                                          for g in m["groups"]], market, searched),
            "note": note,
            "leaderboard_note": ("Exploratory leaderboard: ranked by the multiplicity-adjusted lower bound of the win-rate "
                                 "difference against the anchor model at matched coverage (≥ 100 decided picks, ≥ 5% "
                                 "coverage). It is not a recommendation; active recommendations come only from the weekly "
                                 "selectors (Selection status).")}


# --- Historical (exploratory) ---------------------------------------------------------------------------------------
def historical_path(sport: str) -> Path:
    return config.data_dir() / "research" / "tracking" / f"historical-{sport}.json.gz"


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
                            None if res["statistic"] is None else round(res["statistic"], 5),
                            None if vec is None else [round(x, 5) for x in vec], sorted(res["missing"]) or None])
            groups[g["id"]] = cls
        payload["markets"][market] = {"games": games, "groups": groups, "stack_fits": fits}
        log(f"{sport}:{market}: {len(games)} games, {len(groups)} groups")
    path = historical_path(sport)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), mtime=0))
    tmp.replace(path)
    return path


@lru_cache(maxsize=2)
def _historical(path: str, mtime: float) -> dict:
    return json.loads(gzip.decompress(Path(path).read_bytes()))


STATUS_CODE = {"p": "pick", "d": "disagree", "t": "tie", "n": "no_majority", "i": "incomplete"}


def historical_items(sport: str, market: str, reg: dict, date_from=None, date_to=None) -> dict:
    p = historical_path(sport)
    if not p.is_file():
        return {}
    d = _historical(str(p), p.stat().st_mtime)
    if d.get("registry_sha256") != reg["sha256"]:
        return {}
    mk = d["markets"].get(market)
    if not mk:
        return {}
    keep = [i for i, g in enumerate(mk["games"]) if (not date_from or g["tipoff_at"] >= f"{date_from}T00:00:00Z")
            and (not date_to or g["tipoff_at"] <= f"{date_to}T23:59:59Z")]
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
        return {"status": "no_historical_table", "folds": []}
    seasons = sorted({i["season"] for items in per.values() for i in items.values()})
    folds = []
    for s in seasons[2:]:
        earlier = {gid: {k: v for k, v in items.items() if v["season"] < s} for gid, items in per.items()}
        later = {gid: {k: v for k, v in items.items() if v["season"] == s} for gid, items in per.items()}
        singles_e = {g["members"][0]: earlier.get(g["id"], {}) for g in m["groups"] if g["kind"] == "single"}
        board = leaderboard([summarise(g, list(earlier.get(g["id"], {}).values()), singles_e, market, m["anchor"])
                             for g in m["groups"]], market, m["configurations"])
        if not board:
            folds.append({"season": s, "chosen": None, "reason": "no configuration with enough earlier evidence"})
            continue
        top = board[0]
        g = next(x for x in m["groups"] if x["id"] == top["id"])
        singles_l = {x["members"][0]: later.get(x["id"], {}) for x in m["groups"] if x["kind"] == "single"}
        res = summarise(g, list(later.get(g["id"], {}).values()), singles_l, market, m["anchor"])
        cell = next(c for c in res["thresholds"] if c["threshold"] == top["threshold"])
        folds.append({"season": s, "chosen": top["id"], "threshold": top["threshold"],
                      "earlier": {k: top[k] for k in ("decided", "coverage", "win_rate", "lift", "lower_adjusted")},
                      "later": {"decided": cell["decided"], "coverage": cell["coverage"], "win_rate": cell["win_rate"],
                                "ci95": cell["ci95"], "matched": cell["matched"][:1]}})
    return {"status": "ok", "folds": folds,
            "note": "Exploratory (reused seasons). Shows whether a configuration chosen on earlier seasons held up later."}
