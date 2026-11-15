"""Property tests (Hypothesis) of how the adaptive system takes its decisions, docs/ADAPTIVE_SYSTEM.md:

- the pick rule per market (selector.decide, as adaptive._decide calls it): the rule in force is exactly the one its
  documented criterion names, recomputed here from the definition (minimum picks / coverage / weeks, Bonferroni lower
  bound of the lift over the bookmaker's de-vigged probability, or the base rate for reg3; best lower bound, then
  coverage, then registry order; hysteresis for the incumbent); a higher threshold never adds picks; applying a rule
  never reads the outcome; the order of the settled items does not matter;
- the combination (adaptive.fit_combiner): admission follows its coverage rule exactly (most covered first, a member is
  admitted only while the common training set keeps `need` rows), the L2 penalty is the one with the lowest loss on the
  newest VALIDATION_SHARE of the rows (ties: the largest), and the fitted stack is never worse on its own penalised
  objective than plain equal-weight pooling (the point it starts from).
More examples: NBA_PROPERTY_EXAMPLES=200."""
import math
import os
import random
from datetime import datetime, timedelta, timezone
from statistics import NormalDist

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app import adaptive, combine, selector

EXAMPLES = int(os.environ.get("NBA_PROPERTY_EXAMPLES", "25"))
SETTINGS = settings(max_examples=EXAMPLES * 2, deadline=None, suppress_health_check=[HealthCheck.too_slow])
MARKETS = ("winner", "spread", "total", "reg3")
# The registered decision constants (docs/ADAPTIVE_SYSTEM.md, selector.py, adaptive.py), pinned so a silent change fails.
GRID = {"reg3": (0.40, 0.45, 0.50, 0.55, 0.60), "default": (0.50, 0.55, 0.60, 0.65, 0.70, 0.75)}
MIN_PICKS, MIN_COVERAGE, MIN_WEEKS, ALPHA, HYSTERESIS = 100, 0.10, 3, 0.05, 0.01
MIN_COVERAGE_SHARE, VALIDATION_SHARE, L2_GRID = 0.5, 0.25, (1.0, 10.0, 100.0, 1000.0)


def _vector(rng, market):
    if market == "reg3":
        a, d = rng.uniform(0.05, 0.8), rng.uniform(0.05, 0.4)
        b = max(0.02, 1 - a - d)
        s = a + d + b
        return (a / s, d / s, b / s)
    push = rng.uniform(0.0, 0.08) if market in ("spread", "total") else 0.0
    p = rng.uniform(0.2, 0.85)
    return (p * (1 - push), push, (1 - p) * (1 - push))


@st.composite
def settled(draw):
    """Settled prospective items as adaptive._decide builds them, with a model edge of a drawn size over the bookmaker."""
    market = draw(st.sampled_from(MARKETS))
    rng = random.Random(draw(st.integers(0, 2 ** 32 - 1)))
    skill, n = draw(st.floats(0.0, 1.0)), draw(st.integers(0, 700))
    quoted = draw(st.floats(0.5, 1.0))
    items = []
    for i in range(n):
        model = _vector(rng, market)
        book = _vector(rng, market) if rng.random() < quoted else None
        truth = model if rng.random() < skill else (book or _vector(rng, market))       # outcomes follow the model by `skill`
        realised = rng.choices((0, 1, 2), weights=truth)[0]
        items.append({"game_id": f"g{i:04d}", "week": f"2026-W{40 + i * 12 // max(1, n):02d}", "tipoff_at": "",
                      "line": None, "vectors": {"adaptive": model} if rng.random() < 0.95 else {}, "agreement": {},
                      "bookmaker": book, "realised": realised})
    incumbent = draw(st.none() | st.sampled_from(GRID.get(market, GRID["default"])).map(
        lambda t: {"source": "adaptive", "filter": "none", "threshold": t}))
    return market, items, incumbent


def _oracle(items, market, incumbent):
    """The decision recomputed from the selector's documented rule, without selector.evaluate / decide."""
    allowed = combine.sides(market)
    three = market in combine.THREE_WAY
    plain = [i["realised"] for i in items if i["realised"] in allowed]
    base = {k: plain.count(k) / len(plain) for k in allowed} if plain else {}
    judged = [i for i in items if i["bookmaker"] is not None or three]
    ths = GRID.get(market, GRID["default"])
    z = NormalDist().inv_cdf(1 - ALPHA / len(ths))
    stats = []
    for idx, t in enumerate(ths):
        diffs, pushes, weeks = [], 0, set()
        for it in judged:
            v = it["vectors"].get("adaptive")
            if v is None:
                continue
            best = max(v[k] for k in allowed)
            top = [k for k in allowed if abs(v[k] - best) <= 1e-12]
            if len(top) != 1 or v[top[0]] + 1e-9 < t:
                continue
            side = top[0]
            if it["realised"] == 1 and not three and side != 1:
                pushes += 1
                continue
            book = it["bookmaker"]
            ref = book[side] / sum(book[k] for k in allowed) if book is not None else base[side]
            diffs.append((1.0 if it["realised"] == side else 0.0) - ref)
            weeks.add(it["week"])
        n = len(diffs)
        lift = sum(diffs) / n if n else None
        lower = None
        if n > 1:
            var = max(0.0, sum(d * d for d in diffs) / n - lift ** 2)
            lower = lift - z * math.sqrt(var / n)
        cov = (n + pushes) / len(judged) if judged else None
        ok = n >= MIN_PICKS and (cov or 0) >= MIN_COVERAGE and len(weeks) >= MIN_WEEKS
        stats.append({"threshold": t, "idx": idx, "picks": n, "lower": lower, "coverage": cov, "eligible": ok})
    if not any(s["eligible"] for s in stats):
        return "insufficient_evidence", None, stats
    passing = [s for s in stats if s["eligible"] and s["lower"] is not None and s["lower"] > 0]
    if not passing:
        return "abstain", None, stats
    best = max(passing, key=lambda s: (s["lower"], s["coverage"], -s["idx"]))
    inc = next((s for s in passing if incumbent and s["threshold"] == incumbent["threshold"]), None)
    if inc is not None and best["lower"] < inc["lower"] + HYSTERESIS:
        best = inc
    return "active", best["threshold"], stats


def _near_tie(stats, eps=1e-9):
    """Floating sums in a different order may swap rules whose bounds are equal to rounding: such examples are skipped."""
    vals = sorted(s["lower"] for s in stats if s["lower"] is not None)
    return any(abs(a - b) < eps for a, b in zip(vals, vals[1:])) or any(
        s["lower"] is not None and abs(s["lower"]) < eps for s in stats)


@SETTINGS
@given(h=settled())
def test_the_pick_rule_in_force_is_the_one_its_criterion_names(h):
    market, items, incumbent = h
    d = selector.decide(items, market, ["adaptive"], incumbent, filters=("none",))
    status, threshold, stats = _oracle(items, market, incumbent)
    for got, want in zip(d["stats"], stats):
        assert got["picks"] == want["picks"] and got["eligible"] == want["eligible"], (got, want)
        if want["lower"] is not None:
            assert got["lower"] == pytest.approx(want["lower"], abs=1e-9)
    if _near_tie(stats):
        return
    assert d["status"] == status, (d["reason"], status)
    assert (d["rule"] or {}).get("threshold") == threshold


@SETTINGS
@given(h=settled(), data=st.data())
def test_a_higher_threshold_never_adds_picks_the_order_of_items_does_not_matter_and_applying_reads_no_outcome(h, data):
    market, items, incumbent = h
    d = selector.decide(items, market, ["adaptive"], incumbent, filters=("none",))
    picks = [s["picks"] + s["pushes"] for s in d["stats"]]
    assert picks == sorted(picks, reverse=True)                                  # thresholds ascend: nested pick sets
    shuffled = selector.decide(data.draw(st.permutations(items)), market, ["adaptive"], incumbent, filters=("none",))
    if not _near_tie(_oracle(items, market, incumbent)[2]):
        assert (shuffled["status"], shuffled["rule"]) == (d["status"], d["rule"])
    for it in items[:20]:
        seen = selector.apply(d, it, market)
        for other in (0, 1, 2):
            assert selector.apply(d, {**it, "realised": other}, market) == seen


# --- The combination -------------------------------------------------------------------------------------------------
@st.composite
def oos(draw):
    """Out-of-sample member vectors as adaptive.train builds them (some members cover fewer games)."""
    market = draw(st.sampled_from(MARKETS))
    rng = random.Random(draw(st.integers(0, 2 ** 32 - 1)))
    k = draw(st.integers(2, 5))
    names = [f"m{i}" for i in range(k)]
    share = {m: draw(st.floats(0.3, 1.0)) for m in names}
    n = draw(st.integers(40, 160))
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        truth = _vector(rng, market)
        vectors = {}
        for m in names:
            if rng.random() < share[m]:
                noise = [max(1e-3, x + rng.gauss(0, 0.05)) for x in truth]
                if market != "reg3" and truth[1] == 0:
                    noise[1] = 0.0
                s = sum(noise)
                vectors[m] = tuple(x / s for x in noise)
        at = base + timedelta(hours=6 * i)
        rows.append({"game_id": f"g{i:03d}", "season": 2026, "cutoff_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "result_available_at": (at + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "realised": rng.choices((0, 1, 2), weights=truth)[0], "vectors": vectors})
    return market, names, rows, (base + timedelta(hours=6 * n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _objective(model, rows, names, market):
    """fit_stack's penalised objective at `model`: mean log loss over non-push rows + l2/n·(Σ(w−1/k)² + Σb²)."""
    cls = combine._classes(market)
    data = [r for r in rows if r["realised"] in cls]
    n, k = len(data), len(names)
    nll = 0.0
    for r in data:
        f = combine._features([r["vectors"][m] for m in names], market)
        s = [model["bias"][i] + sum(w * x for w, x in zip(model["weights"], f[i])) for i in range(len(cls))]
        top = max(s)
        lse = top + math.log(sum(math.exp(x - top) for x in s))
        nll -= s[cls.index(r["realised"])] - lse
    pen = model["l2"] / n * (sum((w - 1 / k) ** 2 for w in model["weights"]) + sum(b * b for b in model["bias"][:-1]))
    return nll / n + pen


@SETTINGS
@given(h=oos())
def test_the_combination_admits_by_coverage_picks_the_validated_penalty_and_never_loses_to_equal_weights(h):
    market, names, rows, cutoff = h
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(adaptive, "MIN_TRAIN", 30)
        cm = adaptive.fit_combiner(rows, names, market, cutoff)
        cls = combine._classes(market)
        usable = [r for r in rows if r["realised"] in cls]
        cover = {m: sum(m in r["vectors"] for r in usable) for m in names}
        order = sorted(names, key=lambda m: (-cover[m], names.index(m)))
        need = max(adaptive.MIN_TRAIN, int(MIN_COVERAGE_SHARE * cover[order[0]]))
        chosen, common = [], usable
        for m in order:                                                        # the documented admission, recomputed
            c = [r for r in common if m in r["vectors"]]
            if len(c) >= need:
                chosen, common = chosen + [m], c
        if len(chosen) < 2 or len(common) < adaptive.MIN_TRAIN:
            assert cm["stack"] is None and cm["members"] == []
            return
        assert cm["members"] == chosen and set(cm["excluded"]) == set(names) - set(chosen)
        common.sort(key=lambda r: (r["cutoff_at"], r["game_id"]))
        split = max(1, int(len(common) * (1 - VALIDATION_SHARE)))
        early, late = common[:split], common[split:]
        losses = {}
        for l2 in L2_GRID:
            model = combine.fit_stack([([r["vectors"][m] for m in chosen], r["realised"]) for r in early], market, l2)
            losses[l2] = (sum(combine.log_loss(combine.stack(model, [r["vectors"][m] for m in chosen]), r["realised"])
                              for r in late) / len(late)) if late else 0.0
        best = min(losses.values())
        assert cm["l2"] in [l2 for l2, v in losses.items() if v <= best + 1e-9]
        assert cm["l2"] == max(l2 for l2, v in losses.items() if v <= best + 1e-9) or abs(losses[cm["l2"]] - best) < 1e-9
        equal = {"market": market, "weights": [1 / len(chosen)] * len(chosen), "bias": [0.0] * len(cls), "l2": cm["l2"]}
        assert _objective(cm["stack"], common, chosen, market) <= _objective(equal, common, chosen, market) + 1e-9
