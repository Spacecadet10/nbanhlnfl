# Personal Telegram picks

The dedicated sender reads NBA/NHL canonical forecasts from the existing stores with query-only connections and a
consistent read transaction. It does not refresh, predict, train, grade, migrate or lock the forecast stores. NFL is not
supported. The existing source jobs must be running and fresh. All Telegram settings live in `app/config.py`'s private
configuration path; no token belongs in the frontend, a plist, a command argument or a committed file.

## Private setup

Save `TELEGRAM_BOT_TOKEN=...` in repository-root `.env.telegram`, mode600; `.gitignore` excludes it. The integration is
currently personal to @Prop51picksbot. From the repository root:

```sh
PYTHONPATH=backend backend/.venv/bin/python -m app.cli telegram connect
```

Open the private pairing link it returns and tap Start. Run connect again to bind that private chat. A generic /start,
a group, a wrong code or multiple candidate chats cannot silently select a recipient. Once paired, the destination is
not automatically replaced. Never publish the pairing link. Bot credentials and paired chat ID stay in the private file.

```sh
PYTHONPATH=backend backend/.venv/bin/python -m app.cli telegram status
PYTHONPATH=backend backend/.venv/bin/python -m app.cli telegram test
PYTHONPATH=backend backend/.venv/bin/python -m app.cli telegram preview --date 2026-11-01 --game-id espn:401909903
PYTHONPATH=backend backend/.venv/bin/python -m app.cli telegram run --once
```

`test` sends one connection confirmation; `preview` sends a prominently labelled past-game formatting example with real
stored times and outcomes. It does not alter a final game into a pending forecast. Repeating the same test/preview does
not resend it. `status` reports receipts, including failed, uncertain or interrupted sending attempts requiring review.
Send `/stop` in the paired private chat to pause future delivery; `/start` resumes. Other chats cannot change that state.

## Scheduled delivery

`scripts/launchd/com.nba-predictor.telegram.plist` is a separate personal notification agent, not a replacement for any
model or source job. It runs `telegram run --once` approximately every60s while the Mac is awake and the user session is
available. launchd does not overlap instances of the same job. It reads this checkout's authoritative `data/`, the same
path configured for existing live jobs. Preview servers are not required. Internet access and upstream forecast/source
jobs remain necessary; this is not a cloud24/7 service.

One message summarizes a game's currently recorded pregame markets and the app's existing strong Best Pick, if any, under a prominent “Recommended pick” heading. Messages are delivered as locally rendered PNG cards with a highlighted recommendation and stacked market panels, without icons. Experimental opposite signals stay separate. Install the optional `notifications` backend dependency (Pillow); this host uses `uv pip install --python backend/.venv/bin/python 'Pillow>=10.4,<14'`. The underlying validated/escaped message is retained in the receipt ledger. Rendering completes before a delivery is claimed; image upload uses multipart `sendPhoto`, not a public image URL.
Only scheduled games starting within two hours, with a pending canonical forecast genuinely generated in that window,
are eligible. Source refresh failures/staleness suppress delivery. No final, in-progress, reconstructed or demo pick is
sent as a new live pick. Experimental opposite flags use only other matching picks, retain the exact market/direction/
displayed percentage/model/source/line provenance, and disclose thin samples and unverified bookmaker offers/prices.
Three-way markets have no single opposite. These flags do not alter the existing Best Pick ranking.

Receipts are stored in the separate ignored `.telegram-delivery.sqlite`, mode600. The database enforces a unique delivery
key per chat/game/forecast IDs and meaningful best-pick/opposite-signal changes. Genuine new forecast IDs can produce
updates; mere count or clock changes do not spam repeats. Two competing processes cannot both claim the same message.
The claim is committed before calling Telegram. Successful responses store the message ID. Confirmed connection failures
and Telegram429 rejections retry after backoff if the game remains eligible; a timeout/ambiguous acknowledgement or a
crashed `sending` claim is not automatically resent. Telegram does not supply a client idempotency key, so exactly-once
delivery across network failures cannot be guaranteed. Inspect the receipt before any manual retry; no reset command is
provided in this first version. Known failed/uncertain receipts do not imply Telegram accepted a message.

To install after local verification, copy the template to `~/Library/LaunchAgents/` and bootstrap it in the user gui
domain. It contains paths only, no token. Stop it with:

```sh
launchctl bootout gui/$(id -u)/com.nba-predictor.telegram
```

Logs are the ignored root `.telegram-delivery.log` and `.telegram-delivery-error.log`. Transport exceptions are replaced
with redacted diagnostics because Telegram embeds bot tokens in request URLs. An existing webhook is refused, never
deleted. Revoking or rotating the bot token requires updating the private credentials file.
