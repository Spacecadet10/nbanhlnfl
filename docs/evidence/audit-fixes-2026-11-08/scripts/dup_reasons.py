import json, sqlite3, sys, collections, re
con = sqlite3.connect("file:.telegram-delivery.sqlite?mode=ro", uri=True)
rows = con.execute("select batch_key, created_at, payload from ticket_batches order by created_at").fetchall()
NFL = re.compile(r"\b(NFL|touchdown|quarterback|QB|football|Sunday slate|ATL|CIN|PHI|BUF|KC|week ?\d+)\b", re.I)
for key, created, payload in rows:
    d = json.loads(payload)
    rev = (d.get("plan") or {}).get("review") or {}
    fr = rev.get("forecasts_reviewed") or {}
    by_reason = collections.defaultdict(list)
    for fid, e in fr.items():
        r = e.get("reason") if isinstance(e, dict) else str(e)
        by_reason[r].append(fid)
    print("==", created, "forecasts", len(fr), "distinct reasons", len(by_reason))
    for r, ids in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        sports = collections.Counter(i.split(":")[0] for i in ids)
        if len(sports) > 1 or (len(ids) > 1):
            print("  x%d %s :: %s" % (len(ids), dict(sports), (r or "")[:230]))
    for fid, e in fr.items():
        r = e.get("reason") if isinstance(e, dict) else str(e)
        if fid.startswith("nba:") and re.search(r"\bNFL\b|football|quarterback", r or "", re.I):
            print("  NBA-mentions-NFL", fid, "::", r[:300])
