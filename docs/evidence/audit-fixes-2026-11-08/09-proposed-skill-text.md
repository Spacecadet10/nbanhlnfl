# Proposed text for the review skill (NOT applied)

`~/.codex/skills/courtside-ticket-review/SKILL.md` and `automation.toml` were not touched. If the helper change is
activated, this paragraph is proposed next to the existing `review.forecasts_reviewed` rule (SKILL.md line 70). It needs
the same coordinated switch as earlier skill changes (the helper refuses a review whose skill hash changed mid-review).

> A forecast listed in `review.locked_games[].blocked_forecasts` needs no entry in `forecasts_reviewed`: the helper
> records the lock itself (which ticket, which original forecast) and archives it under `locks`. Write an entry only for
> a forecast you judged, and write what applies to that forecast: its own game, side, probability, record and price.
> Do not give several forecasts one catch-all sentence that names another game or sport. A shared explanation is fine
> when it is true for each forecast it is attached to.

Why: review 7 (2026-11-09T00:13:50Z) built its plan with a one-off script whose `else:` branch gave one sentence to every
remaining forecast. 19 of 31 entries (NBA 8, NHL 6, NFL 5) carry "NFL unchanged CHI71 is already repeated twice…";
18 of them were lock-blocked forecasts, and the 19th (nfl:71) was the ticket's own forecast, for which the sentence's
"this alternate market/new version is locked out" is not true.
