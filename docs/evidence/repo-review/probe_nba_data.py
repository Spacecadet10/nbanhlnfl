import pandas as pd, glob, os
f = glob.glob(os.path.join(os.path.dirname(__file__), "nba_data_x", "*.csv"))[0]
df = pd.read_csv(f, low_memory=False)
print(os.path.basename(f), df.shape); print(list(df.columns))
print("games:", df["GAMEID"].nunique() if "GAMEID" in df else "n/a")
print(df.head(3).T.to_string()[:2500])
