"""Fetch each completed-game detail page once (2 s apart) for games starting in [now-14d, now)."""
import sys, time, subprocess, datetime, os
sys.path.insert(0, os.path.dirname(__file__)); import parse
P = os.path.join(os.path.dirname(__file__), 'pages'); LOG = os.path.join(os.path.dirname(__file__), 'fetch-log.txt')
now = datetime.datetime(2026, 11, 11, 5, 0, tzinfo=datetime.timezone.utc); since = now - datetime.timedelta(days=14)
for sport, first, slug in (('nba', 'nba-predictions.html', 'nba-basketball'), ('nhl', 'nhl-predictions.html', 'nhl-hockey')):
    os.makedirs(f'{P}/detail-{sport}', exist_ok=True)
    files = [first] + [f'{slug}-completed-{n}.html' for n in range(2, 8)]
    links = []
    for f in files:
        t, tb = parse.tables(f'{P}/{f}')
        for h, rows in tb:
            if 'Final' not in ' '.join(h): continue
            for c, dt, l in rows:
                if dt and l and since.isoformat()[:19] <= dt[0][:19] < now.isoformat()[:19]: links.append(l[0])
    links = sorted(set(links)); print(sport, len(links), flush=True)
    for l in links:
        out = f'{P}/detail-{sport}/{l.rsplit("/", 1)[1]}.html'
        if os.path.exists(out): continue
        time.sleep(2)
        code = subprocess.run(['curl', '-s', '-A', 'research-check (personal, non-commercial)', '--max-time', '30', '-o', out, '-w', '%{http_code}', 'https://www.dratings.com' + l], capture_output=True, text=True).stdout
        with open(LOG, 'a') as f: f.write(f"{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%dT%H:%M:%SZ} | {code} | {os.path.getsize(out)} | https://www.dratings.com{l} -> detail-{sport}/{os.path.basename(out)}\n")
print('done')
