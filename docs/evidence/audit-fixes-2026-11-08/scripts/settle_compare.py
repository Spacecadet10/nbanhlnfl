"""Run settle(notify=True) with a mocked Telegram on a COPY of the real ledger and stores, twice, and print what it
appended and what it would have sent. Usage: PYTHONPATH=<backend of the code under test> NBA_DATA_DIR=<copy>
NBA_TELEGRAM_LEDGER=<ledger copy> python settle_compare.py <label>"""
import json, os, sqlite3, sys
from app import config, telegram as tg, tickets as t
ledger = os.environ["NBA_TELEGRAM_LEDGER"]
assert "scratchpad" in ledger and "scratchpad" in str(config.data_dir()), "copies only"
con = sqlite3.connect(ledger)
chat = con.execute("select chat_id from ticket_batches limit 1").fetchone()[0]   # never printed
con.close()
config.telegram_settings = lambda: {"TELEGRAM_CHAT_ID": chat, "TELEGRAM_BOT_TOKEN": "mock"}
calls = []
class Bot:
    def __init__(self, *a): pass
    def identity(self): return "mock"
    def call(self, method, payload, **kw):
        calls.append(payload.get("text", "").replace("\n", " / ")); return {"message_id": 900000 + len(calls)}
tg.Telegram = Bot
tg.controls = lambda *a: True
def rows():
    c = sqlite3.connect(ledger)
    out = c.execute("select ticket_id, evidence_hash from ticket_grades order by rowid").fetchall()
    keys = c.execute("select delivery_key from deliveries where delivery_key like 'ticket-result:%' order by delivery_key").fetchall()
    c.close(); return out, [k[0] for k in keys]
before, keys_before = rows()
now = sys.argv[2]
for run, stamp in ((1, now), (2, now[:17] + "59Z")):
    n = len(calls)
    report = t.settle(now=stamp, notify=True)
    after, keys_after = rows()
    print(json.dumps({"code": sys.argv[1], "run": run, "tickets": len(report["tickets"]), "errors": report["errors"],
                      "notification_errors": report["notification_errors"],
                      "grade_rows_before": len(before), "grade_rows_after": len(after),
                      "new_grade_rows": [(a, b[:10]) for a, b in after[len(before):]],
                      "result_notices_before": len(keys_before), "new_result_notice_keys": sorted(set(keys_after) - set(keys_before)),
                      "telegram_calls": calls[n:]}))
    before, keys_before = after, keys_after
