"""Follow-up check on COPIES of the real ledger and stores, Telegram mocked: settlement twice, then the trigger
(preflight --brief --notify) twice and status once. Prints what was appended, what would have been sent, the trigger-run
records and the undelivered-result list. Usage: PYTHONPATH=<backend under test> NBA_DATA_DIR=<copy>
NBA_TELEGRAM_LEDGER=<ledger copy> python followup_check.py"""
import io, json, os, sqlite3, sys
from contextlib import redirect_stdout
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
        calls.append(str(payload.get("text", payload.get("caption", ""))).split("\n")[0]); return {"message_id": 900000 + len(calls)}
tg.Telegram = Bot
tg.controls = lambda *a: True
def counts():
    c = sqlite3.connect(ledger)
    tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    out = {"receipts": dict(c.execute("select status,count(*) from deliveries group by status").fetchall()),
           "grade_rows": c.execute("select count(*) from ticket_grades").fetchone()[0],
           "trigger_runs": [list(r) for r in c.execute("select run_id,recorded_at,day,due,reason,observations from ticket_trigger_runs order by run_id")] if "ticket_trigger_runs" in tables else "no table",
           "observations_by_run": [list(r) for r in c.execute("select run_id,issue,count(*) from ticket_trigger_observations group by run_id,issue order by run_id,issue")] if "ticket_trigger_observations" in tables else "no table"}
    c.close(); return out
print(json.dumps({"step": "before", **counts()}))
for run in (1, 2):
    n = len(calls); report = t.settle(notify=True)
    print(json.dumps({"step": f"settle {run}", "tickets": len(report["tickets"]), "errors": report["errors"],
                      "notification_errors": report["notification_errors"], "undelivered_results": report["undelivered_results"],
                      "telegram_calls": calls[n:], "grade_rows": counts()["grade_rows"]}))
def cli(*args):
    sys.argv = ["tickets", *args]; buf = io.StringIO()
    with redirect_stdout(buf): code = t.main()
    return code, json.loads(buf.getvalue())
for run in (1, 2):
    n = len(calls); code, out = cli("preflight", "--brief", "--notify")
    review = out["review"]
    print(json.dumps({"step": f"preflight {run}", "exit": code, "as_of": out["as_of"], "due": review.get("due"), "reason": review.get("reason"),
                      "observations_error": review.get("observations_error"), "not_reviewed_now": len(review.get("not_reviewed_now", [])),
                      "missed_before_start": [{k: m.get(k) for k in ("sport", "matchup", "explained", "explanation", "trigger_runs")} for m in review.get("missed_before_start", [])],
                      "undelivered_results": out.get("undelivered_results", "absent (none owed)"), "notification_errors": out["notification_errors"],
                      "mock_telegram_calls": calls[n:]}))
code, out = cli("status")
print(json.dumps({"step": "status", "exit": code, "due": out["review"].get("due")}))
print(json.dumps({"step": "after", **counts()}))
