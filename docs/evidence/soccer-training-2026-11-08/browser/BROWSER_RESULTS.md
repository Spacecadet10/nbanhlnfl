# Browser verification — soccer research preview (2026-11-08, ~17:10–17:25 UTC)

Preview: `NBA_LIVE_DATA_DIR=~/nba-predictor-research-data/soccer-train-20261108/preview-src NBA_DEV_DATA_DIR=…/preview-app
API_PORT=8851 WEB_PORT=5251 NBA_DEV_PUBLISH=0 sh scripts/dev.sh` (worktree ~/Desktop/nba-predictor-soccer-train).
Data: seasons ≤ 2022 only + ledger of 55,356 backtest forecasts. Chrome (claude-in-chrome). The Chrome window could not be
resized past 500 px (resize calls failed: extension disconnected), so widths were tested in same-origin iframes of exactly
1440 px and 390 px (as in earlier smoke tests); the real window (500 px) was used for interactive clicks and screenshots.

## Page-load flows (iframe; measured scrollWidth vs width; Backtest badges in <main>; the word "Live" outside the
## explicit "never published as live" / "not live predictions" disclaimers)
| # | Flow | 1440 px | 390 px |
|---|---|---|---|
| 1 | Game day, eng.1, default date (last forecast date 2023-05-28) | loaded, sw 1440, no overflow, 12 badges, no Live | loaded, sw 390, no overflow, 12 badges, no Live |
| 2 | Game day eng.1 date 2020-09-12 | ok, 1440, 6 badges | ok, 390, 6 badges |
| 3 | Game day uefa.champions | ok, 1440, 3 badges | ok, 390, 3 badges |
| 4 | Game day uefa.europa | ok, 1440, 4 badges | ok, 390, 4 badges |
| 5 | Performance eng.1 (table + reliability) | ok, 1440, 2 badges | ok, 390, 2 badges |
| 6 | Performance uefa.europa | ok, 1440 | ok, 390 |
| 7 | History ita.1 | ok, 1440 | ok, 390 |
| 8 | History ita.1 season 2021, model elo, offset 100 | ok, 1440 | ok, 390 |
| 9 | Daily learning (soccer): honest "Not available" | ok, 1440 | ok, 390 |
| 10 | Unknown history match id → error state "Couldn't load data. 404: No soccer history match …" | ok, 1440 | ok, 390 |
| 11 | Match page (Napoli 4–2 Ajax, UCL 2022-10-12) deep link with contradictory ?competition=eng.1 | ok, sw 1440, 4 badges; three models + source (commit abfaeddc2ee3, 2022-23/cl.txt) | ok, sw 390 |
No element outside a .table-wrap extended past the frame width in any flow.

## Interactive (real window, 500 px)
- Deep link: layout selected `uefa.champions` and rewrote the URL's competition to the match's own.
- Date step: previous game day May 28 → May 25, 2023 (1 match).
- Competition switch (header select) → ger.1: game day re-rendered for 2023-05-27 (9 cards).
- Card click → research match page with the three stored model forecasts.
- History pager: "Older" → "101–200 of 9180", rows changed.
- UCL final 2023-06-10 Manchester City 1–0 Inter: grading `deciding_match_unverified`, 0 forecasts, reason shown.
- NBA selection in this soccer-only preview: honest 503 from the (empty scaffold) NBA store; no crash. Not a regression
  (NBA code unchanged); the preview is soccer-only.
- Screenshots: ucl-2022-10-12-phone-1.jpg, ucl-2022-10-12-phone-2-card.jpg.
- Not separately captured: the transient loading state (AsyncView shows it; not frozen for a screenshot).

## Re-run after the S6 API/UI review fixes (rebuilt preview `preview2-src` / `preview2-app`, ~18:17–18:35 UTC)
Iframes 1440 / 390 px; columns: loaded, scrollWidth, overflow, Backtest badges, "Live" outside disclaimers.
| Flow | 1440 | 390 |
|---|---|---|
| Game day eng.1 (default date) | ok, 1440, no, 12, no | ok, 390, no, 12, no |
| Game day eng.1 date 2021-01-01 (a real match day; shown directly) | 1440, no, 4 | 390, no, 4 |
| Game day uefa.europa | ok, 1440, no, 4 | ok, 390, no, 4 |
| Performance eng.1 (in-sample note, reliability) | ok, 1440, no, 2 | ok, 390, no, 2 |
| History ita.1 with season "x", offset -5 (ignored / clamped, no error) | ok, 1440, no, 2 | ok, 390, no, 2 |
| History ita.1 offset 999999 ("past the last of N") | ok | ok |
| Unknown of: id → 404 error state | ok, 1440 | first 438 px (long id did not wrap) → fixed (`.state` overflow-wrap) → ok, 390, no overflow |
| Date 2020-08-01 without forecasts → notice "No research forecast … showing <latest>" | — | ok, 390, no overflow, 12 badges |
| Malformed URL `/games/%E0%A4%A` | **blank app** | **blank app** — pre-existing for every league (also `?sport=nfl` and no sport); not soccer code; recorded open (report §11) |
API: impossible date `2022-13-45` → 422; forecasts carry their own label ("Research backtest"), run_type backtest, published_at null.
