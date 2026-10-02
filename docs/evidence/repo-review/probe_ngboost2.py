# Follow-up: seeded base learner (determinism) + chronological val early stopping + predict at best iter
import time, numpy as np
from sklearn.tree import DecisionTreeRegressor
from ngboost import NGBRegressor
from ngboost.distns import Normal
from scipy.stats import norm, pearsonr
rng = np.random.default_rng(0); n,p=6000,20
X = rng.normal(size=(n,p)); mu=3*X[:,0]-2*X[:,1]+X[:,2]*X[:,3]
sigma=np.exp(2.3+0.35*X[:,4]-0.25*X[:,5]); y=mu+sigma*rng.normal(size=n)
def run(seed, depth=3):
    base = DecisionTreeRegressor(criterion="squared_error", max_depth=depth, random_state=seed)
    m = NGBRegressor(Dist=Normal, Base=base, n_estimators=2000, learning_rate=0.03, random_state=seed, verbose=False)
    t=time.time(); m.fit(X[:4000],y[:4000],X_val=X[4000:4500],Y_val=y[4000:4500],early_stopping_rounds=50); ft=time.time()-t
    d_all = m.pred_dist(X[4500:]); d_best = m.pred_dist(X[4500:], max_iter=m.best_val_loss_itr+1)
    return m, ft, d_all, d_best
yt, st = y[4500:], sigma[4500:]
m, ft, da, db = run(42)
for name, d in (("all_iters", da), ("best_iter", db)):
    loc, sc = d.params['loc'], d.params['scale']; pit = norm.cdf((yt-loc)/sc)
    print(f"{name}: sigma_corr={pearsonr(sc,st)[0]:.3f} mean_sigma={sc.mean():.2f}(true {st.mean():.2f}) cov80={np.mean((pit>.1)&(pit<.9)):.3f} NLL={-norm.logpdf(yt,loc,sc).mean():.4f}")
print(f"fit_time={ft:.1f}s best_itr={m.best_val_loss_itr} base_models={len(m.base_models)}")
m2,_,da2,_ = run(42)
print("same seed (seeded Base): max|dloc|=", float(np.max(np.abs(da.params['loc']-da2.params['loc']))), "max|dscale|=", float(np.max(np.abs(da.params['scale']-da2.params['scale']))))
loc_c = db.params['loc']; sd_c = np.std(y[4000:4500]-m.pred_dist(X[4000:4500],max_iter=m.best_val_loss_itr+1).params['loc'])
pit_c = norm.cdf((yt-loc_c)/sd_c)
print(f"constant-SD baseline (same mean, SD from val resid={sd_c:.2f}): NLL={-norm.logpdf(yt,loc_c,sd_c).mean():.4f} cov80={np.mean((pit_c>.1)&(pit_c<.9)):.3f}  oracle NLL={-norm.logpdf(yt,mu[4500:],st).mean():.4f}")
