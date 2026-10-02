"""Research candidate `gbt_v1`: gradient-boosted trees on the existing pregame features
(docs/EVALUATION_PLAN.md "Research round 2"). Also a member of the two-hour winner research cohort (app/agreement.py).

Same walk-forward discipline as `logistic`: features are captured at predict time, the label joins the training set
only when the result is delivered, and the model is refit at each season start on earlier seasons only. Before
the first fit it returns the frozen Elo probability. scikit-learn is imported lazily at the first fit.
"""
from typing import Optional

from .base import PregameGame, Result
from .efficiency import BoxEvent, EfficiencyRatings
from .elo import EloConfig
from .logistic import TeamFeatures

FEATURES = ("elo_diff", "mov10_diff", "rest_diff", "b2b_home", "b2b_away", "neutral",
            "net_diff", "net_home", "net_away", "pace", "games_home", "games_away")
MONOTONIC_INCREASING = ("elo_diff", "net_diff", "mov10_diff")
SETTINGS = {"loss": "log_loss", "learning_rate": 0.05, "max_iter": 200, "max_depth": 3, "max_leaf_nodes": 8,
            "min_samples_leaf": 50, "l2_regularization": 1.0, "max_bins": 255, "early_stopping": False,
            "random_state": 20260930}


class GradientBoostedTrees:
    name = "gbt_v1"
    feature_names = FEATURES
    monotonic = MONOTONIC_INCREASING

    def __init__(self, elo_cfg: EloConfig):
        self.elo_cfg = elo_cfg
        self.team = TeamFeatures(elo_cfg)
        self.eff = EfficiencyRatings()
        self.pending: dict[str, tuple[int, list[float]]] = {}
        self.examples: list[tuple[int, list[float], bool]] = []   # (season, pregame feature vector, home won)
        self.season: Optional[int] = None
        self.model = None
        self.first_fit_season: Optional[int] = None
        self.fits: list[dict] = []   # one record per refit: season and training size

    def _vector(self, g: PregameGame) -> list[float]:
        feats = {**self.team.compute(g), **self.eff.features(g)}
        return [float(feats[f]) for f in self.feature_names]

    def _refit(self, season: int) -> None:
        train = [(x, y) for s, x, y in self.examples if s < season]
        if not train or len({y for _, y in train}) < 2:
            return
        from sklearn.ensemble import HistGradientBoostingClassifier
        self.model = HistGradientBoostingClassifier(
            **SETTINGS, monotonic_cst=[1 if f in self.monotonic else 0 for f in self.feature_names])
        self.model.fit([x for x, _ in train], [int(y) for _, y in train])
        self.fits.append({"season": season, "training_games": len(train),
                          "training_seasons": sorted({s for s, _, _ in self.examples if s < season})})
        if self.first_fit_season is None:
            self.first_fit_season = season

    def predict(self, g: PregameGame) -> float:
        if self.season is None or g.season > self.season:
            self.season = g.season
            self._refit(g.season)
        x = self._vector(g)
        self.pending[g.game_id] = (g.season, x)
        if self.model is None:   # warm-up before the first fit
            return self.team.elo.predict(g)
        return float(self.model.predict_proba([x])[0][1])

    def update(self, r: Result) -> None:
        self.team.update(r)
        season, x = self.pending.pop(r.game.game_id)
        self.examples.append((season, x, r.home_won))

    def update_box(self, e: BoxEvent) -> None:
        self.eff.update_box(e)

    def config(self) -> dict:
        return {"features": "+".join(self.feature_names), "monotonic": "+".join(self.monotonic),
                **{k: v for k, v in SETTINGS.items()},
                "elo": "k{k}-h{home_advantage}-c{carry}-mov{mov}".format(**self.elo_cfg.__dict__)}

    def oos_from_season(self) -> Optional[int]:
        return self.first_fit_season
