# Learned stacking research

Research requested by the user after agreement and equal-weight averaging experiments. No models trained or live definitions changed in this task.

## Findings

- Learned stacking is distinct from voting and averaging. It learns a second model from base-model predictions on held-out observations. Scikit-learn explicitly warns that fitting the stacker on predictions of models trained on those same observations has a high overfitting risk. Its default classification folds are not a chronological forecasting protocol: implement explicit forward prediction generation instead.
  Source: https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.StackingClassifier.html
- Conditional weighting is a legitimate research direction. Yao et al., *Bayesian hierarchical stacking: Some models are (somewhere) useful*, show advantages when model performance differs across input regions. This supports testing matchup-dependent combinations; it does not establish an NBA accuracy gain or require implementing their full Bayesian method.
  Source: https://arxiv.org/abs/2101.08954
- Selecting only some games is selective classification. Compare error/accuracy against coverage, rather than comparing a filtered strategy with a baseline on every game. SelectiveNet demonstrates learned prediction/selection jointly on other datasets, not NBA data. It motivates a comparison against a simple single-model confidence filter.
  Source: https://proceedings.mlr.press/v97/geifman19a.html
- The NBA-specific paper *Stacked ensemble model for NBA game outcome prediction analysis* reports 83.27% in its results table but uses an 80/20 random split and five-fold cross-validation. It describes box-score features; verified pregame feature availability would require a separate code/data audit. Its headline accuracy is not evidence that our chronological pregame system can reach that level. No implementation copied.
  Source: https://www.nature.com/articles/s41598-025-13657-1

## Recommended bounded experiment (proposal, not registered or run)

1. Retain all five existing research base models: Elo, efficiency margin, logistic, logistic_v2, gbt_v1. A model performing worse overall can still be useful conditionally; let regularization limit its influence. Do not assert that GBT will help.
2. Compare two predeclared candidates: (A) a regularized logistic stacker using clipped base probability logits; (B) a small regularized conditional stacker adding disagreement and a fixed, limited set of probability-by-context interactions, initially rest difference and efficiency gap. Specify clipping, scaling, penalty and interaction list before results. No exhaustive subset/threshold search.
3. Generate base predictions strictly forward: each game’s training data, preprocessing and labels must precede its cutoff. Train the stacker only on earlier, already-scored base forecasts; evaluate on the next time block. Fit any penalty or selection threshold inside earlier training blocks. Drop warm-up rows with no genuine out-of-sample base predictions. Default StackingClassifier cross-validation is not sufficient.
4. Existing saved development predictions can support a cheap exploratory analysis, but Elo's settings and other definitions were chosen using those same development seasons. Even a later chronological meta split does not erase that selection reuse. Report this explicitly; independent evidence needs a separately registered forward evaluation. Do not reuse spent 2024–26 test seasons for selection.
5. Report all-game accuracy, Brier, log loss and calibration versus logistic_v2, Elo and equal averaging. Also show accuracy-versus-coverage at a small predeclared set (for example 50/70/85/100%), with uncertainty and per-season results. Compare selection methods at matched coverage, and report each base model on the stacker's exact selected games. Operational thresholds must be fixed using earlier data, not outcomes of the scored block.
6. Keep current live Elo and the sealed challenger unchanged. A useful research result becomes a separately versioned prospective experiment; it is not automatic promotion.

## Interpretation

High aggregate loss correlation does not prove no conditional information remains. Neither failed averaging nor one failed tree candidate rules out learned stacking. Conversely, these papers do not show that a perfect combination exists for this dataset. The proposed experiment tests incremental predictive value and selection quality directly.
