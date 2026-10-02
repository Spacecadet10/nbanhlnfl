import numpy as np, statsmodels, statsmodels.api as sm, time
print("statsmodels", statsmodels.__version__)
rng=np.random.default_rng(0); n=4000
X=sm.add_constant(rng.normal(size=(n,3)))
mu=np.exp(X@np.array([1.05,0.1,-0.08,0.05]))  # ~3 goals
alpha=0.05
y=rng.negative_binomial(1/alpha, 1/(1+alpha*mu))
t=time.time(); r=sm.NegativeBinomial(y,X).fit(disp=0); print(f"discrete NegativeBinomial: params={np.round(r.params,3).tolist()} (last=alpha) converged={r.mle_retvals['converged']} t={time.time()-t:.2f}s")
t=time.time(); g=sm.GLM(y,X,family=sm.families.NegativeBinomial(alpha=0.05)).fit(); print(f"GLM NB(alpha fixed .05): params={np.round(g.params,3).tolist()} t={time.time()-t:.2f}s")
p=sm.GLM(y,X,family=sm.families.Poisson()).fit(); print("Poisson GLM params", np.round(p.params,3).tolist(), "Pearson chi2/df", round(p.pearson_chi2/p.df_resid,3))
