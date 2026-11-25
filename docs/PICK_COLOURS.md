# Saved pick colours (what was green before the start)

Code: `backend/app/pick_snapshots.py` (recording, backfill, counting), `backend/app/pick_colours.py` (report), `GET /api/pick-colours`,
`frontend/src/components/PickColours.tsx` (Performance). Store: `<data>/pick_snapshots.sqlite`, append-only by trigger, in the nightly backup.
Colour rules, recommendations, exact-% records, recovered history and Telegram deduplication are unchanged; this only records them.

## What is saved
- The Telegram run (about once a minute) saves every pregame pick of its report, sent or not (`event='seen'`): forecast ID, game, sport,
  market, side, line, model version, exact %, live W/L/pushes/voids and colour, recovered-record W/L/pushes/voids and colour (separately),
  past-season counts, rule version, recommendation, price and times. Version 1 is the original; a changed record or selection adds a
  version, a refresh adds nothing. The live colour and the recovered colour are separate fields and can differ.
- A card whose delivery status is `sent` adds one `event='sent'` row per market. Failed and uncertain sends add none.
  If that write itself fails (the run says `pick_snapshots: failed`), the colour stays unknown until `backfill --apply` reads the saved card.
  Sends once uncertain and later found in the owner's Telegram export count as confirmed and are named in the reconciliation.
- "First displayed" is the recorder's first sight: the web can show a pick up to a run (1-3 minutes) earlier. While Telegram is paused,
  the source is stale or the Mac is off, nothing is saved; those picks are `not_recorded`, never coloured afterwards.

## What counts
- One prediction per game and market: the forecast of record (latest pregame forecast, as on every page) with its own first saved colour.
  If that forecast was never saved, its colour is unknown, even when an earlier forecast's was.
- Recommended: that forecast was the card's recommended pick as the recommendation last stood before the start (at most one per game). Its
  colour is still its first saved one.
- Telegram sends: the last confirmed card before the start, with the colour on that card, graded as that card published it.
- The saved side and line are graded, on read, by the app's own settlement. Win rate = wins / (wins + losses). Pushes, voids, half results
  and unsettled picks are counted apart. A stored forecast that no longer equals the saved pick is unscorable, never a win or loss.
- Returns: 1 unit at the stored sportsbook price of the same side and exact line, 'current', retrieved at or before the observation and at
  most 20 minutes earlier, and only while that book's newest price is still at this line (`line_moved` otherwise). Soccer prices are stored only
  when they change, so only recently moved ones pass the 20-minute test: coverage is low and not a random sample. Derived prices (DC/DNB from 1X2) are never prices. Everything else is unpriced and stays in the counts.
- A recovered record with no rows at this exact % is `no_record`, never 0-0 and never neutral.

## Unknown colours
`before_colour_rule` (before 2026-11-22T17:15:41Z, the first card under bucket-record-v1; earlier green marks used another rule),
`before_tracking`, `later_forecast_not_saved`, `not_recorded`, `no_exact_record_shown`,
`saved_after_the_moved_start`.

## Backfill
`python -m app.pick_snapshots backfill [--apply]` reads confirmed cards. A pick is written (`basis='card_text'`) only when the card's own
text holds that market once, the published percentage, a printed W-L record that gives the printed mark under bucket-record-v1, and, when
any mark is on the card, that rule's legend. Provenance: delivery key, message ID, text SHA-256 and the printed line. Nothing is derived
from today's record. Recovered Nov 2-16 cards stay in their own history, colour unknown, and are counted once in the reconciliation.

No chronological reconstruction is part of this feature. A game's start plus a few hours does not establish when its result was available.
