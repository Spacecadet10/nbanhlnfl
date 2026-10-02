"""Goal-dependence checks behind three registry rejections (backend/app/candidates/registry.py FAMILY_AUDIT; docs/
CANDIDATE_POOL.md §1): negative binomial (overdispersion), bivariate Poisson (residual home/away correlation) and
Dixon–Coles (where tied scores exceed independence). Read-only; development seasons only (warm-up seasons only train).

Walks the sealed goals_poisson configuration chronologically: each game's regulation rates come from earlier games only,
then the game updates the model. Usage (backend venv, from backend/):
    python ../docs/evidence/repo-review/probe_nhl_goal_dependence.py <path to an NHL store copy>
"""
import math
import sqlite3
import sys

from app import nhl_eval
from app.models.hockey import GoalsConfig, GoalsPoisson

conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
decision = nhl_eval.load_decision(conn)
model = GoalsPoisson(GoalsConfig(**decision["goals_config"]), nhl_eval.SPORT.rating_key)
dev = set(nhl_eval.PERIODS["development"])
obs = []
for row in nhl_eval.load_rows(conn, "development"):         # warm-up + development seasons only (SQL-filtered)
    lh, la = model.rates(row.game)
    reg = row.result.regulation
    if row.game.season in dev and reg is not None:
        obs.append((lh, la, reg[0], reg[1]))
    model.update(row.result)

n = len(obs)
disp_h = sum((yh - lh) ** 2 / lh for lh, la, yh, ya in obs) / n
disp_a = sum((ya - la) ** 2 / la for lh, la, yh, ya in obs) / n
rh = [(yh - lh) / math.sqrt(lh) for lh, la, yh, ya in obs]
ra = [(ya - la) / math.sqrt(la) for lh, la, yh, ya in obs]
mh, ma = sum(rh) / n, sum(ra) / n
corr = (sum((x - mh) * (y - ma) for x, y in zip(rh, ra))
        / math.sqrt(sum((x - mh) ** 2 for x in rh) * sum((y - ma) ** 2 for y in ra)))
pois = lambda k, lam: math.exp(-lam) * lam ** k / math.factorial(k)
print(f"games: {n} (regulation scores, seasons {sorted(dev)}, goals_poisson config {decision['goals_config']})")
print(f"pearson dispersion: home {disp_h:.3f}, away {disp_a:.3f}  (1 = Poisson; > 1 = overdispersion)")
print(f"residual home/away correlation (Pearson residuals): {corr:+.3f}")
print("tied regulation scores, observed vs expected under independent Poisson with each game's rates:")
for k in range(0, 5):
    o = sum(yh == k and ya == k for _, _, yh, ya in obs) / n
    e = sum(pois(k, lh) * pois(k, la) for lh, la, _, _ in obs) / n
    print(f"  {k}-{k}: observed {o:.1%}  expected {e:.1%}  ratio {o / e:.2f}")
o = sum(yh == ya for _, _, yh, ya in obs) / n
e = sum(sum(pois(k, lh) * pois(k, la) for k in range(0, 15)) for lh, la, _, _ in obs) / n
print(f"  any tie: observed {o:.1%}  expected {e:.1%}  ratio {o / e:.2f}")
