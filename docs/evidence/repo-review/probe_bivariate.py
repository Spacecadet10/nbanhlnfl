# Standalone scipy bivariate Poisson (Karlis-Ntzoufras 2003) + Dixon-Coles(1997) tau, team attack/defence + HFA,
# cross-checked against penaltyblog 1.13.0 on synthetic NHL-like data with known lambda3.
import time, numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln, logsumexp
rng = np.random.default_rng(1); T, G = 32, 2624
att = rng.normal(0, 0.12, T); att -= att.mean(); dfn = rng.normal(0, 0.12, T); dfn -= dfn.mean()
hfa, mu0, lam3_true = 0.05, np.log(2.8), 0.20
h = rng.integers(0, T, G); a = (h + rng.integers(1, T, G)) % T
l1 = np.exp(mu0 + hfa + att[h] + dfn[a]) - lam3_true; l2 = np.exp(mu0 + att[a] + dfn[h]) - lam3_true
w3 = rng.poisson(lam3_true, G); yh = rng.poisson(l1) + w3; ya = rng.poisson(l2) + w3
print(f"synthetic corr(yh,ya)={np.corrcoef(yh,ya)[0,1]:.3f}")
def unpack(p):  # sum-to-zero via last team = -sum(others)
    A = np.append(p[:T-1], -p[:T-1].sum()); D = np.append(p[T-1:2*T-2], -p[T-1:2*T-2].sum()); return A, D, p[-3], p[-2], p[-1]
def lpois(k, lam): return k*np.log(lam) - lam - gammaln(k+1)
def nll_bp(p):  # marginal mean m = lam_i + lam3 -> lam_i = m - lam3 (KN parameterisation used here: log-link on marginal)
    A, D, m0, hf, lc = unpack(p); l3 = np.exp(lc)
    m1 = np.exp(m0 + hf + A[h] + D[a]); m2 = np.exp(m0 + A[a] + D[h])
    L1, L2 = np.maximum(m1 - l3, 1e-6), np.maximum(m2 - l3, 1e-6)
    K = np.arange(0, 12)[None, :]; kmax = np.minimum(yh, ya)[:, None]
    terms = lpois(yh[:,None]-K, L1[:,None]) + lpois(ya[:,None]-K, L2[:,None]) + lpois(K, l3)
    terms = np.where(K <= kmax, terms, -np.inf)
    return -logsumexp(terms, axis=1).sum()
def nll_dc(p):
    A, D, m0, hf, rho = unpack(p)
    m1 = np.exp(m0 + hf + A[h] + D[a]); m2 = np.exp(m0 + A[a] + D[h])
    tau = np.ones(G); z = (yh==0)&(ya==0); tau[z] = 1 - rho*m1[z]*m2[z]
    i = (yh==0)&(ya==1); tau[i] = 1 + rho*m1[i]; i = (yh==1)&(ya==0); tau[i] = 1 + rho*m2[i]; i = (yh==1)&(ya==1); tau[i] = 1 - rho
    return -(lpois(yh, m1) + lpois(ya, m2) + np.log(np.maximum(tau, 1e-9))).sum()
x0 = np.r_[np.zeros(2*T-2), np.log(yh.mean()), 0.0, np.log(0.1)]
t = time.time(); r = minimize(nll_bp, x0, method="L-BFGS-B"); tb = time.time()-t
A, D, m0, hf, lc = unpack(r.x)
print(f"own BP: success={r.success} nll={r.fun:.2f} lambda3={np.exp(lc):.3f} (true {lam3_true}) hfa={hf:.3f} (true {hfa}) corr(att)={np.corrcoef(A,att)[0,1]:.3f} t={tb:.1f}s")
x0d = x0.copy(); x0d[-1] = 0.0
t = time.time(); rd = minimize(nll_dc, x0d, method="L-BFGS-B"); td = time.time()-t
print(f"own DC: success={rd.success} nll={rd.fun:.2f} rho={rd.x[-1]:.3f} hfa={rd.x[-2]:.3f} t={td:.1f}s")
xi = np.r_[np.zeros(2*T-2), np.log(yh.mean()), 0.0]
ri = minimize(lambda q: nll_dc(np.r_[q, 0.0]), xi, method="L-BFGS-B")
print(f"independent Poisson nll={ri.fun:.2f}  LR(BP vs indep)={2*(ri.fun-r.fun):.2f}  LR(DC vs indep)={2*(ri.fun-rd.fun):.2f}")
try:
    import penaltyblog as pb
    names = np.array([f"T{i:02d}" for i in range(T)])
    t = time.time(); m = pb.models.BivariatePoissonGoalModel(yh, ya, names[h], names[a]); m.fit(); tp = time.time()-t
    P = m.get_params(); print(f"penaltyblog {pb.__version__} BP: lambda3={P['lambda3']:.3f} hfa={P['home_advantage']:.3f} t={tp:.1f}s  (note: its lambda1=exp(hfa+att+def) is the NON-shared part; marginal mean = lambda1+lambda3)")
    t = time.time(); md = pb.models.DixonColesGoalModel(yh, ya, names[h], names[a]); md.fit(); P = md.get_params(); print(f"penaltyblog DC: rho={P['rho']:.3f} hfa={P['home_advantage']:.3f} t={time.time()-t:.1f}s")
except Exception as e:
    print("penaltyblog not available here:", type(e).__name__, e)
