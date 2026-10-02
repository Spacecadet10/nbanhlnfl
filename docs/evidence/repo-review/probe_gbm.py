import time, numpy as np, subprocess, xgboost as xgb, lightgbm as lgb, sklearn
print("xgboost", xgb.__version__, "lightgbm", lgb.__version__, "sklearn", sklearn.__version__, "numpy", np.__version__)
for mod in (xgb, lgb):
    import os, glob
    libs = glob.glob(os.path.join(os.path.dirname(mod.__file__), "**", "*.dylib"), recursive=True)
    for l in libs:
        out = subprocess.run(["otool","-L",l],capture_output=True,text=True).stdout
        print(os.path.basename(l), "->", [x.strip().split(" ")[0] for x in out.splitlines()[1:] if "omp" in x])
rng = np.random.default_rng(0); n,p=6000,20
X = rng.normal(size=(n,p)); sigma=np.exp(2.3+0.35*X[:,4]-0.25*X[:,5])
y = 3*X[:,0]-2*X[:,1]+X[:,2]*X[:,3]+sigma*rng.normal(size=n)
def fx(seed):
    m = xgb.XGBRegressor(n_estimators=600, learning_rate=0.03, max_depth=4, subsample=0.8, colsample_bytree=0.8, random_state=seed, n_jobs=4)
    t=time.time(); m.fit(X[:4500],y[:4500]); return time.time()-t, m.predict(X[4500:])
def fl(seed):
    m = lgb.LGBMRegressor(n_estimators=600, learning_rate=0.03, num_leaves=15, subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=seed, n_jobs=4, deterministic=True, force_row_wise=True, verbose=-1)
    t=time.time(); m.fit(X[:4500],y[:4500]); return time.time()-t, m.predict(X[4500:])
for name,f in (("xgboost",fx),("lightgbm",fl)):
    t1,p1=f(42); t2,p2=f(42); t3,p3=f(7)
    rmse=np.sqrt(np.mean((p1-y[4500:])**2))
    print(f"{name}: fit={t1:.2f}s rmse={rmse:.3f} same-seed max|diff|={np.max(np.abs(p1-p2))} diff-seed max|diff|={np.max(np.abs(p1-p3)):.4f}")
# xgboost distributional? (reg:squarederror only here); note LightGBM/XGB have no native Normal-scale head
