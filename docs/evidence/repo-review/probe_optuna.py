# sqlite storage records every trial incl. pruned/failed; TPESampler seed determinism (sequential, n_jobs=1)
import os, optuna, sqlite3, warnings
warnings.filterwarnings("ignore"); optuna.logging.set_verbosity(optuna.logging.ERROR)
print("optuna", optuna.__version__)
db = os.path.join(os.path.dirname(__file__), "optuna_probe.db")
if os.path.exists(db): os.remove(db)
def obj(trial):
    x = trial.suggest_float("x", -5, 5); d = trial.suggest_int("depth", 2, 6)
    if trial.number in (3, 17): raise ValueError("synthetic failure")
    for step in range(5):
        trial.report((x-1)**2 + step*0.0 + d*0.01, step)
        if trial.should_prune(): raise optuna.TrialPruned()
    pass
    return (x-1)**2 + d*0.01
def run(name, storage):
    st = optuna.create_study(study_name=name, storage=storage, sampler=optuna.samplers.TPESampler(seed=123), pruner=optuna.pruners.MedianPruner(n_startup_trials=5))
    st.optimize(obj, n_trials=40, catch=(ValueError,))
    return st
s1 = run("a", f"sqlite:///{db}"); s2 = run("b", f"sqlite:///{db}")
same = [t.params for t in s1.trials] == [t.params for t in s2.trials]
from collections import Counter
print("states:", Counter(t.state.name for t in s1.trials), "best:", s1.best_params)
print("seeded runs identical param sequence:", same)
con = sqlite3.connect(db)
print("sqlite trials rows by study/state:", con.execute("select study_id,state,count(*) from trials group by 1,2").fetchall())
print("sqlite intermediate values rows:", con.execute("select count(*) from trial_intermediate_values").fetchone()[0], "param rows:", con.execute("select count(*) from trial_params").fetchone()[0])
print("failed trial system_attrs:", con.execute("select key, substr(value_json,1,80) from trial_system_attributes limit 3").fetchall())
