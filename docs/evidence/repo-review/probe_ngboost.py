import time, numpy as np, ngboost, sklearn, scipy
from ngboost import NGBRegressor
from ngboost.distns import Normal
from scipy.stats import norm, pearsonr, spearmanr
print("ngboost", ngboost.__version__ if hasattr(ngboost,'__version__') else '?', "sklearn", sklearn.__version__, "numpy", np.__version__, "scipy", scipy.__version__)
rng = np.random.default_rng(0)
n, p = 6000, 20
X = rng.normal(size=(n, p))
mu = 3*X[:,0] - 2*X[:,1] + X[:,2]*X[:,3]
sigma = np.exp(2.3 + 0.35*X[:,4] - 0.25*X[:,5])  # ~ NBA margin SD scale (~10)
y = mu + sigma*rng.normal(size=n)
tr, te = slice(0, 4500), slice(4500, None)   # ordered split (rows iid anyway)
def run(seed, es=None):
    m = NGBRegressor(Dist=Normal, n_estimators=600, learning_rate=0.03, random_state=seed, verbose=False)
    t = time.time()
    if es:
        m.fit(X[:4000], y[:4000], X_val=X[4000:4500], Y_val=y[4000:4500], early_stopping_rounds=es)
    else:
        m.fit(X[tr], y[tr])
    ft = time.time()-t
    d = m.pred_dist(X[te]); loc, sc = d.params['loc'], d.params['scale']
    return m, ft, loc, sc
m, ft, loc, sc = run(42)
yt, st = y[te], sigma[te]
pit = norm.cdf((yt-loc)/sc)
cov80 = np.mean((pit>0.1)&(pit<0.9))
print(f"fit_time_s={ft:.1f}  n_estimators=600 lr=0.03")
print(f"sigma corr pearson={pearsonr(sc,st)[0]:.3f} spearman={spearmanr(sc,st)[0]:.3f}  mean pred sigma={sc.mean():.2f} true={st.mean():.2f}")
print(f"80% interval coverage={cov80:.3f}  PIT deciles={np.histogram(pit,bins=10,range=(0,1))[0].tolist()}")
nll = -norm.logpdf(yt,loc,sc).mean()
const_sd = np.std(y[tr]-m.predict(X[tr]))
nll_c = -norm.logpdf(yt,loc,const_sd).mean()
print(f"test NLL heteroscedastic={nll:.4f} vs same-mean constant-SD({const_sd:.2f})={nll_c:.4f}  (oracle={-norm.logpdf(yt,mu[te],st).mean():.4f})")
m2, ft2, loc2, sc2 = run(42)
print("determinism same seed: max|dloc|=", float(np.max(np.abs(loc-loc2))), "max|dscale|=", float(np.max(np.abs(sc-sc2))))
m3, ft3, loc3, sc3 = run(7)
print("different seed: max|dloc|=", float(np.max(np.abs(loc-loc3))))
m4, ft4, _, _ = run(42, es=50)
print(f"early stopping: best_val_loss_itr={m4.best_val_loss_itr} fit_time={ft4:.1f}s n_base_models={len(m4.base_models)}")
