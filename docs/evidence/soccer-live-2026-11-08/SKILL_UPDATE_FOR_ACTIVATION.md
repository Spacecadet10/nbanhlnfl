# Ticket-review skill changes for soccer (apply at activation step 4; not applied)

Installed skill: `~/.codex/skills/courtside-ticket-review/SKILL.md`. Replace line 22's soccer sentence and extend line 41 as below.

**Line 22: replace** "The helper reports soccer explicitly unavailable while there is no soccer forecast model. Never substitute
results-only matches as predictions." **with:**

> The snapshot includes live soccer when the soccer store is active: EPL, La Liga, Bundesliga, Serie A, Ligue 1 and the
> Champions League, market `1x2_regulation` only.
> - Each pick is the sealed Elo's most likely 90-minute outcome with its exact probability. The pick also carries all
>   three probabilities and its generation and cutoff times.
> - The forecast is made before 06:00 UTC on match day. Legs settle on the 90-minute result: extra time and penalties
>   do not count; postponed matches are void.
> - A refused competition appears in `refused` and in alerts.
> - Never treat soccer fixtures without a live forecast as predictions. Never offer totals, BTTS, double chance or any
>   other soccer market.

**Line 41: replace** "compare the strongest available NBA/NHL/NFL combinations together" **with** "compare the strongest
available NBA/NHL/NFL/soccer combinations together", and **add:**

> Soccer exact-percentage history is not yet available. Disclose the held-out test figures instead:
> - 2023–26 log loss 0.980 vs 1.071 for the frequency baseline;
> - model quality only, no betting edge.
