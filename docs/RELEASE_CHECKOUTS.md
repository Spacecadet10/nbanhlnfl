# Which checkout runs what (2026-11-25)

| Checkout | Branch (pushed) | Runs |
|---|---|---|
| `~/nba-predictor-live` | `live-release` | NBA and NHL collection, forecasts, daily, injuries, markets, `training-inputs` and `adaptive-train`; API 8100 |
| `~/nba-predictor-delivery` | `delivery-release` | NCAAB, NFL, Soccer, Telegram, scorecard, weekly report, learning guard and autoresearch; API 8101, web 4173 |
| `~/nba-predictor-backup-runner` | `delivery-release` commit | nightly backup, forecast journal |

- The NBA/NHL fits in force carry the LIVE code's definitions (adaptive.definition, research_live.definition). Running those jobs from delivery
  (its `research_live.py`, `market_live.py`, `near_tipoff.py` differ) would change them: that is its own reviewed release, not a configuration fix.
- Release = a reviewed commit on the branch above, pushed; `git checkout --detach <commit>` in the checkout; `app.release pin`; restart the
  API if its code changed. Never commit inside a release checkout: `pin` refuses a commit no branch holds
  (a local branch is enough for `pin`; pushing it is the release step above).
- A pin that differs from the checkout is one log line, never a refusal (owner, Nov 11); it means the last step above was skipped.
- `adaptive-train`, `nhl-daily`, `ncaab-daily` and their commands' exit codes (0/1/3, docs/ADAPTIVE_SYSTEM.md section 14) change together. Install
  and reload the plists FIRST (the new shell text accepts the old codes 0/1), then move the checkout; roll back in the reverse order. The old
  `if training-inputs` text with the new code would skip training on every exit 3. Never reload a job while it runs (bootout kills it).
