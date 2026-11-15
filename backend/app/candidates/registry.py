"""Candidate registry (docs/CANDIDATE_POOL.md). Adding a candidate = one `Spec` line; the walk, fitting, study, live
recording and roster read this list. Nothing here is a mandatory voter. The pool selector does not read this list directly:
it reads the sealed pool registry (candidates/selection.py, pool_rules.json) frozen from the held-out evidence, in which a
candidate is a source alone (standalone route), only inside a registered average (combination route), or not at all
(research only). The selector then picks a source, a registered combination or no pick, using only earlier evidence.
"""
import hashlib
import json
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Optional

from . import learners

POOL = "pool-v1"
HEAD_MARKETS = {
    "binary": ("winner",),
    "normal": ("winner", "spread", "total", "team_total_home", "team_total_away"),
    "reg3": ("winner", "reg3"),
    "goals": ("winner", "reg3", "spread", "total", "team_total_home", "team_total_away"),
}
MIN_TRAIN = {"nba": 200, "nhl": 500}


@dataclass(frozen=True)
class Spec:
    name: str
    sport: str
    family: str          # learners.SETTINGS key
    head: str            # binary | normal | reg3 | goals
    features: str        # features.FEATURE_SETS key
    role: str            # "reference" (re-expresses an existing member on the shared walk) | "candidate"
    why: str
    refit: str = "season"
    extra: Optional[str] = None     # optional dependency group, e.g. "research" (ngboost)

    @property
    def markets(self) -> tuple:
        return HEAD_MARKETS[self.head]


SPECS = (
    # NBA winner
    Spec("logit_l2:nba_base", "nba", "logit_l2", "binary", "nba_base", "reference",
         "Linear baseline on the shared walk; separates learner effects from feature effects (cf. logistic_v2)."),
    Spec("extra_trees:nba_base", "nba", "extra_trees", "binary", "nba_base", "candidate",
         "Bagged randomised trees: nonlinear interactions with errors less correlated than boosting (gbt_v1)."),
    Spec("random_forest:nba_base", "nba", "random_forest", "binary", "nba_base", "candidate",
         "Bagged trees with best splits; screened against extra trees, the closer relative."),
    Spec("mlp:nba_base", "nba", "mlp", "binary", "nba_base", "candidate",
         "Small neural network (16 units, strong L2, 5 seeds averaged); ~5–7k training games per season."),
    # NBA margin/total (spread, total, team totals)
    Spec("ridge:nba_base", "nba", "ridge", "normal", "nba_base", "reference",
         "Re-expresses ridge_points on the shared walk (reference for ablations)."),
    Spec("elastic_net:nba_base", "nba", "elastic_net", "normal", "nba_base", "candidate",
         "Tests whether L1 sparsity changes anything over ridge on 12 correlated features (expected: no)."),
    Spec("extra_trees:nba_base:normal", "nba", "extra_trees", "normal", "nba_base", "candidate",
         "Nonlinear margin/total means from bagged trees; complementary to linear members."),
    Spec("mlp:nba_base:normal", "nba", "mlp", "normal", "nba_base", "candidate",
         "Small neural network for margin/total means (5 seeds averaged)."),
    Spec("ngboost:nba_base", "nba", "ngboost", "normal", "nba_base", "candidate",
         "Distributional boosting: per-game margin/total spread (the existing members use one spread for all games).",
         extra="research"),
    Spec("hetero_linear:nba_base", "nba", "hetero_linear", "normal", "nba_base", "candidate",
         "Per-game spread with no new dependency: linear mean and linear log-spread by maximum likelihood."),
    # NHL winner
    Spec("logit_l2:nhl_base", "nhl", "logit_l2", "binary", "nhl_base", "reference",
         "Re-expresses hockey_logistic_xg (plus goal rates) on the shared walk."),
    Spec("extra_trees:nhl_base", "nhl", "extra_trees", "binary", "nhl_base", "candidate",
         "Nonlinear interactions of shot quality, ratings and rest."),
    Spec("mlp:nhl_base", "nhl", "mlp", "binary", "nhl_base", "candidate",
         "Small neural network (5 seeds averaged); ~7–11k training games."),
    Spec("logit_l2:nhl_goalie", "nhl", "logit_l2", "binary", "nhl_goalie", "candidate",
         "New information: starting goalies' goals saved above our xG (live: ESPN probable/confirmed listing)."),
    Spec("extra_trees:nhl_goalie", "nhl", "extra_trees", "binary", "nhl_goalie", "candidate",
         "Goalie inputs with nonlinear interactions."),
    # NHL regulation 3-way and goals (every NHL market)
    Spec("multinomial_l2:nhl_base", "nhl", "multinomial_l2", "reg3", "nhl_base", "candidate",
         "Direct regulation home/draw/away classifier; no Poisson assumption for draws."),
    Spec("multinomial_l2:nhl_goalie", "nhl", "multinomial_l2", "reg3", "nhl_goalie", "candidate",
         "As above with goalie inputs."),
    Spec("poisson_draw:nhl_base", "nhl", "poisson_draw", "goals", "nhl_base", "candidate",
         "Goal-rate GLMs with shot-quality inputs and a fitted draw inflation (joint score); goals_glm lacks xG."),
    Spec("poisson_draw:nhl_goalie", "nhl", "poisson_draw", "goals", "nhl_goalie", "candidate",
         "As above with goalie inputs: the only candidate carrying goalie information to puck lines and totals."),
    # Deep networks (MODEL_SELECTION §22, candidates/deep.py): recorded at the user's request to build a prospective record.
    # deep-v1 found no gain over existing members; research only, in no sealed pool or tracking registry.
    Spec("torch_mlp:nba_base", "nba", "torch_mlp", "binary", "nba_base", "candidate",
         "PyTorch network (64-32 units, dropout, 5 seeds; epochs chosen on the latest earlier season).", extra="deep"),
    Spec("keras_mlp:nba_base", "nba", "keras_mlp", "binary", "nba_base", "candidate",
         "The same network in TensorFlow/Keras: a cross-check between the two libraries.", extra="deep"),
    Spec("torch_mlp:nba_base:normal", "nba", "torch_mlp", "normal", "nba_base", "candidate",
         "PyTorch margin and total networks; in deep-v1 better than gbt_points on spreads, not than the best member.",
         extra="deep"),
    Spec("torch_mlp:nhl_base", "nhl", "torch_mlp", "binary", "nhl_base", "candidate",
         "PyTorch network (64-32 units, dropout, 5 seeds; epochs chosen on the latest earlier season).", extra="deep"),
    Spec("keras_mlp:nhl_base", "nhl", "keras_mlp", "binary", "nhl_base", "candidate",
         "The same network in TensorFlow/Keras: a cross-check between the two libraries.", extra="deep"),
)
BY_NAME = {s.name: s for s in SPECS}

# Families considered and not implemented this round, with the evidence or reason (docs/CANDIDATE_POOL.md §1).
FAMILY_AUDIT = (
    {"family": "Regularised logistic (binary)", "decision": "exists",
     "detail": "logistic, logistic_v2, hockey_logistic, hockey_logistic_xg, availability_two_hour. Re-expressed once as a "
               "reference on the shared walk; no duplicate added."},
    {"family": "Multinomial logistic (NHL regulation 3-way)", "decision": "implemented",
     "detail": "No existing direct 3-way classifier; draws were only modelled through Poisson goals."},
    {"family": "Ridge / Elastic Net (margin, total)", "decision": "implemented (elastic net), ridge exists",
     "detail": "ridge_points exists; elastic net screened to test whether sparsity matters on 12 correlated features."},
    {"family": "Random Forest / Extra Trees", "decision": "implemented",
     "detail": "Bagging is absent from the pool; its errors may differ from boosted trees (gbt_v1, gbt_points)."},
    {"family": "XGBoost / LightGBM", "decision": "rejected (duplicate)",
     "detail": "scikit-learn HistGradientBoosting (gbt_v1, gbt_points, hockey_gbt) is the same histogram-boosting "
               "algorithm as LightGBM; CatBoost was tested and rejected on 2026-10-02 with the same features. A new "
               "booster would duplicate an existing member under a new name."},
    {"family": "CatBoost", "decision": "rejected (tested)",
     "detail": "docs/CHALLENGER_RESULTS.md: worse than the strongest member in every task; uncertainty head poorly "
               "calibrated. Not retested: no new features justify it."},
    {"family": "NGBoost (distributional boosting)", "decision": "implemented (research extra)",
     "detail": "Per-game spread for NBA margin/total is a real gap. Optional dependency (ngboost 0.5.11 pulls lifelines, "
               "pandas, matplotlib), so a dependency-free heteroscedastic linear model is registered beside it."},
    {"family": "Negative binomial goals", "decision": "rejected (evidence)",
     "detail": "NHL regulation goals given goals_poisson rates have Pearson dispersion 1.02 (home) and 0.96 (away), "
               "2018–21, 4,877 games: no overdispersion for a negative binomial to model "
               "(docs/evidence/repo-review/probe_nhl_goal_dependence.py/.out)."},
    {"family": "Bivariate Poisson (shared component)", "decision": "rejected (evidence)",
     "detail": "Residual home/away goal correlation is −0.05 (probe_nhl_goal_dependence); a shared Poisson component "
               "can only add positive correlation."},
    {"family": "Dixon–Coles low-score correction", "decision": "rejected (covered)",
     "detail": "Excess draws appear at every tied score (probe_nhl_goal_dependence: 1-1 3.6% vs 2.7%, 2-2 7.1% vs 5.4%, 3-3 7.0% vs 4.8%), not "
               "only 0-0/1-1; the existing diagonal inflation (goals_draw) and the new poisson_draw cover this."},
    {"family": "NBA player / lineup impact", "decision": "deferred",
     "detail": "Lineups are postgame facts; the timestamped pregame input is the injury report, already used by "
               "availability_two_hour. See the repository review for data-access results."},
    {"family": "Larger neural networks (PyTorch, TensorFlow/Keras)", "decision": "recorded as research (no gain in deep-v1)",
     "detail": "≤ 11k games per sport and 12–16 tabular features. deep-v1 (MODEL_SELECTION §22) fitted 64-32 networks in "
               "both libraries: level with the best existing member on winners, worse on totals. Recorded prospectively at "
               "the user's request; never a voter and in no sealed registry."},
)


def _lib(name: str) -> Optional[str]:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def libraries() -> dict:
    return {k: _lib(k) for k in ("scikit-learn", "numpy", "scipy", "ngboost")}


def definition(spec: Spec) -> str:
    """Candidate identity: its spec, learner settings, the pool code and library versions. Changing any of them gives a
    new identity; earlier forecasts keep theirs."""
    h = hashlib.sha256()
    h.update(json.dumps([asdict(spec), learners.SETTINGS.get(spec.family), libraries() if spec.extra else
                         {k: v for k, v in libraries().items() if k != "ngboost"}], sort_keys=True, default=str).encode())
    root = Path(__file__).parent
    for name in ("features.py", "learners.py", "engine.py", "goalies.py"):
        h.update(name.encode())
        h.update((root / name).read_bytes())
    if spec.extra == "deep":       # deep candidates only: existing candidates' identities are unchanged
        from . import deep
        h.update(deep.identity())
    return h.hexdigest()[:16]


def available(spec: Spec) -> bool:
    """Whether this environment can FIT the candidate. A deep candidate without its library can still be applied from a
    stored artifact (candidates/deep.py); the live loop handles that case."""
    if spec.extra == "deep":
        from . import deep
        return deep.libraries_present(spec)
    return spec.extra is None or _lib("ngboost") is not None


def for_sport(sport: str) -> list:
    return [s for s in SPECS if s.sport == sport]
