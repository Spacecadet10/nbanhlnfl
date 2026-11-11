# Research brief (shared by all research agents) — 2026-11-10 ET

Goal: find FREE sources of a line and/or price for markets the app predicts but cannot price:
1. Team totals (home team total, away team total): NBA, NHL, NFL. Note: NHL team totals in the app INCLUDE overtime and shootout; NBA/NFL include overtime.
2. NHL three-way regulation result (home / draw / away after 60 minutes).
3. Soccer, ten markets: goals match total, home team goals, away team goals, both teams to score (BTTS); corners match total, home team corners, away team corners, corner handicap; double chance; draw no bet. Competitions: Premier League, La Liga, Bundesliga, Serie A, Ligue 1, Champions League, Europa League, internationals (Nations League, qualifiers, friendlies). HISTORICAL lines/prices (especially corners and goals) are very valuable even without live prices.

Hard rules:
- Free only. No account, no sign-up, no API key, no trial, no payment, no card. If a source needs any of these: record it as "needs key/account", do not use it.
- Only data served publicly to anyone. Never get around logins, captchas, paywalls, geo-blocks, Cloudflare challenges or rate limits. If robots.txt or terms forbid automated access, record that (quote one line) and skip fetching the data.
- At most one request per second, few requests. Identify honestly: use curl's default User-Agent (or `curl -A "research-check (personal, non-commercial)"`); never pretend to be a browser.
- Never read, print or save anything from .env* files. Do not touch ~/nba-predictor-delivery, ~/nba-predictor-live, any data/ folder or any app code. Do not send anything anywhere (no Telegram, no posting).
- A source only counts if you fetched it today and saved the real response. No source from memory, no "should work".

Where to save: samples under samples/<category-letter>-<short-source-name>/ (raw response as served, plus a tiny meta.txt: URL, UTC time, HTTP status). Notes: notes-<your categories>.md.

For EVERY candidate record (also dead ends):
- Name, exact URL/endpoint, how you found it (what you searched)
- Which missing markets it covers, which sports/leagues
- Line only, or line + both prices; whose numbers (which sportsbook)
- Freshness (live / delayed / daily); history and how far back
- Early lines (day before) or only near game time
- Access rules: robots.txt + terms in one line; limits hit
- Saved sample path, and ONE worked example matched to a real game from reference-games.txt (same teams, same start time UTC). If no matching game is offered, say so.
- Verdict: usable now / usable with care / not usable, and why.
Also write down for each category the searches you ran (search engine queries, sites tried) before calling it empty.
