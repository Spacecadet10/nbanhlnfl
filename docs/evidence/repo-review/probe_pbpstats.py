# ONE request per source through pbpstats' own loaders, default headers. Game 0022300001 (2023-24 opener)
import time, traceback, os, pbpstats
print("pbpstats", pbpstats.__version__ if hasattr(pbpstats,'__version__') else '1.3.11')
from pbpstats.data_loader import StatsNbaPbpLoader, StatsNbaPbpWebLoader, LivePbpLoader, LivePbpWebLoader
gid = "0022300001"; cache = os.path.join(os.path.dirname(__file__), "pbp_cache")
for name, mk in (("stats.nba.com playbyplayv2", lambda: StatsNbaPbpLoader(gid, StatsNbaPbpWebLoader(cache))),
                 ("live S3 playbyplay", lambda: LivePbpLoader(gid, LivePbpWebLoader(cache)))):
    t = time.time()
    try:
        l = mk(); print(f"{name}: OK items={len(l.items)} t={time.time()-t:.1f}s first={l.items[0].data if hasattr(l.items[0],'data') else l.items[0].__dict__}"[:400])
    except Exception as e:
        print(f"{name}: FAIL after {time.time()-t:.1f}s: {type(e).__name__}: {str(e)[:300]}")
