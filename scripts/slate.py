#!/usr/bin/env python3
"""The whole slate in one picture: every game 3-40 hours ahead, the model's side in each market, the sportsbook's price,
and the best number among DraftKings, FanDuel and BetMGM. Built for the owner to assemble his own tickets from one
message instead of a card per game.

Separate from the app: it reads copies of the stores (scripts/early_board.py), saves a write-once board log and PNG under
docs/evidence/early-board/, and sends the PNG to the paired Telegram chat with the bot's own credentials file. It does
not use or change the app's delivery ledger, forecasts or tickets, and it respects /stop (the ledger's 'enabled' flag,
read-only). Paper information, not betting advice: a lean is a spot to look at, not a proven edge.

    PYTHONPATH=backend backend/.venv/bin/python scripts/slate.py              # fetch books, build, send
    PYTHONPATH=backend backend/.venv/bin/python scripts/slate.py --no-send    # build and save the picture only
"""
import argparse, io, json, os, sqlite3, sys, tempfile, urllib.error, urllib.request, uuid
from datetime import datetime, timezone

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import early_board as eb      # noqa: E402
import odds_books             # noqa: E402
from app.telegram_card import _font, INK, MUTED   # noqa: E402  (the app's fonts and colours)

GREEN, RED, LINE, PAPER, HEAD = "#11664D", "#A83824", "#D9DEE2", "#FBFAF7", "#0E2740"
MARKET_NAME = {"winner": "Moneyline", "spread": "Spread", "total": "Total"}


def render(picks, waiting, now):
    games = {}
    for p in picks:
        games.setdefault((p["tipoff"], p["sport"], p["matchup"]), []).append(p)
    W = 1080
    img = Image.new("RGB", (W, 9000), PAPER)
    d = ImageDraw.Draw(img)
    d.text((48, 36), "PROP51 / SLATE", font=_font(50, True, True), fill=HEAD)
    d.text((48, 98), f"Made {now.astimezone(eb.TORONTO):%a %b %d, %I:%M %p %Z} · model side per market · best of DraftKings, FanDuel, BetMGM",
           font=_font(21), fill=MUTED)
    y = 150
    for (tip, sport, matchup), rows in sorted(games.items()):
        d.rounded_rectangle((36, y, W - 36, y + 54), radius=14, fill=HEAD)
        d.text((56, y + 10), f"{sport.upper()}  {matchup}", font=_font(30, True, True), fill="white")
        start = f"{eb.ts(tip).astimezone(eb.TORONTO):%a %I:%M %p}"
        d.text((W - 56 - d.textlength(start, font=_font(24)), y + 14), start, font=_font(24), fill="#DCE6EE")
        y += 64
        for p in sorted(rows, key=lambda p: eb.MARKETS.index(p["market"])):
            lean = p["edge"] > 0
            d.text((56, y + 2), MARKET_NAME[p["market"]].upper(), font=_font(17, True), fill=MUTED)
            d.text((56, y + 24), p["pick"], font=_font(31, True), fill=GREEN if lean else INK)
            d.text((430, y + 4), f"Model {p['model_prob']:.0%}  ·  Sportsbook {p['market_prob']:.0%}  ·  DK {p['price']:+d}", font=_font(22), fill=INK)
            note = f"model {100 * p['edge']:+.1f} over what the price needs" if lean else "no value at this price"
            best = ""
            if p.get("books") and not (p["best_book"] == "DraftKings" or (p["best_line"], p["best_price"]) == (p["line"], p["price"])):
                line = "" if p["best_line"] is None else eb.label(p["market"], p["side"], p["best_line"], *reversed(p["matchup"].split(" @ "))) + " "
                best = f"   ·   better at {p['best_book']}: {line}{p['best_price']:+d}"
            d.text((430, y + 34), note, font=_font(21, lean), fill=GREEN if lean else MUTED)
            if best:
                d.text((430 + d.textlength(note, font=_font(21, lean)), y + 34), best, font=_font(21, True), fill=RED)
            y += 72
            d.line((56, y - 6, W - 56, y - 6), fill=LINE, width=1)
        y += 14
    if not games:
        d.text((48, y), "No games with a current price in the next 40 hours.", font=_font(26), fill=INK)
        y += 50
    notes = []
    if waiting:
        notes.append("Not priced yet (a team plays again first): " + ", ".join(w.get("game", w["sport"].upper()) for w in waiting) + ".")
    if not any("books" in p for p in picks):
        notes.append("FanDuel and BetMGM prices were not available for this run; DraftKings only.")
    notes += ["Green = the model's probability is above what the DraftKings price needs. That is a spot to look at, not a proven edge:",
              "over the first month these disagreements did not make money on paper two hours before the game.",
              "Red = the same bet at a better number at another book. Always take the best number."]
    for n in notes:
        words, row = n.split(), ""
        for wd in words:
            if d.textlength((row + " " + wd).strip(), font=_font(20)) > W - 96:
                d.text((48, y), row, font=_font(20), fill=MUTED); y += 27; row = wd
            else:
                row = (row + " " + wd).strip()
        d.text((48, y), row, font=_font(20), fill=MUTED); y += 31
    y += 20
    if y + W > 10000:
        raise ValueError("Slate picture is too tall for Telegram; narrow the time window (--to-hours).")
    out = io.BytesIO()
    img.crop((0, 0, W, y)).save(out, format="PNG", optimize=True)
    return out.getvalue()


def credentials(root):
    out = {}
    for line in open(os.path.join(root, ".env.telegram")):
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            out[k] = v
    return out


def stopped(root):
    """True when the owner sent /stop to the bot (the app's own flag, read-only)."""
    try:
        c = sqlite3.connect(f"file:{os.path.join(root, '.telegram-delivery.sqlite')}?mode=ro", uri=True)
        r = c.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()
        return bool(r) and r[0] != "1"
    except sqlite3.Error:
        return False


def send(root, png, caption):
    cred = credentials(root)
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in (("chat_id", cred["TELEGRAM_CHAT_ID"]), ("caption", caption)):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"prop51-slate.png\"\r\nContent-Type: image/png\r\n\r\n".encode() + png + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{cred['TELEGRAM_BOT_TOKEN']}/sendPhoto", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:                                   # never print the exception or URL: it contains the bot token
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()).get("ok") is True
    except urllib.error.HTTPError as exc:
        print(f"Telegram answered HTTP {exc.code}; not sent")
    except (urllib.error.URLError, TimeoutError):
        print("No connection to Telegram; not sent")
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--no-fetch", action="store_true", help="do not ask TheRundown for fresh prices first")
    ap.add_argument("--from-hours", type=float, default=3)
    ap.add_argument("--to-hours", type=float, default=40)
    a = ap.parse_args()
    root = odds_books.root_dir()
    if not a.no_fetch and os.path.exists(os.path.join(root, ".env.odds")):
        odds_books.fetch(root)
    dest = tempfile.mkdtemp(prefix="slate-")
    os.environ.setdefault("NBA_DATA_DIR", dest)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    picks, waiting = eb.board(root, now, a.from_hours, a.to_hours, dest)
    out_dir = os.path.join(root, "docs", "evidence", "early-board")
    log, md, leans = eb.write_board(picks, waiting, out_dir, now)
    png = render(picks, waiting, now)
    path = md[:-3] + ".png"
    with open(path, "xb") as fh:
        fh.write(png)
    print(f"{len(picks)} game-markets, {len(leans)} leans; picture saved: {path}")
    if a.no_send:
        return
    if stopped(root):
        print("Not sent: the bot is paused (/stop).")
        return
    caption = f"PROP51 SLATE · {now.astimezone(eb.TORONTO):%a %b %d} · whole slate in one picture. Information for building your own ticket, not a recommendation to bet."
    print("sent to Telegram" if send(root, png, caption) else "NOT sent")


if __name__ == "__main__":
    main()
