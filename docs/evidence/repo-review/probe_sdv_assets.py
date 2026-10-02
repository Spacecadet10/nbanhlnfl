import pandas as pd, os
d = os.path.join(os.path.dirname(__file__), "sdv_dl")
for f in ("goalie_box_2025.parquet","player_boxscores_2025.parquet","nba_player_impact_2025.parquet"):
    df = pd.read_parquet(os.path.join(d,f))
    print(f"=== {f}: shape={df.shape}")
    print("columns:", list(df.columns))
    for c in [c for c in df.columns if any(k in c.lower() for k in ("date","season"))][:3]:
        print(f"  {c}: min={df[c].min()} max={df[c].max()}")
    print(df.head(2).T.head(45).to_string()[:2500])
