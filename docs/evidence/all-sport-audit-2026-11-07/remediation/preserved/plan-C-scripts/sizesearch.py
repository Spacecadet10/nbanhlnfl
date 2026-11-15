import csv, os, sys, hashlib, gzip
rows = list(csv.DictReader(open(sys.argv[1])))
sizes = {int(r["byte_size"]) for r in rows}; hashes = {r["content_hash"] for r in rows}
cand = 0; hits = []
for root in sys.argv[2:]:
    for dp, dns, fns in os.walk(root, onerror=lambda e: None):
        if '/.venv' in dp or '/node_modules' in dp: continue
        for fn in fns:
            p = os.path.join(dp, fn)
            try: st = os.lstat(p)
            except OSError: continue
            if st.st_size in sizes and os.path.isfile(p) and not os.path.islink(p):
                cand += 1
                h = hashlib.sha256(open(p,'rb').read()).hexdigest()
                if h in hashes: hits.append((h, p))
print("size-matched candidates", cand, "hash hits", len(hits))
for h,p in hits: print("HIT", h, p)
