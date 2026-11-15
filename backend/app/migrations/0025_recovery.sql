-- Retrospective reconstructions only. Never read by live forecast, selector or training queries.
CREATE TABLE recovered_forecasts (
 recovery_id INTEGER PRIMARY KEY,
 game_id TEXT NOT NULL REFERENCES games(game_id),
 market TEXT NOT NULL,
 tipoff_at TEXT NOT NULL,
 cutoff_at TEXT NOT NULL,
 reconstructed_at TEXT NOT NULL,
 model_version TEXT NOT NULL,
 dataset_version TEXT NOT NULL,
 line REAL,
 line_source TEXT NOT NULL,
 quote_id INTEGER,
 p_a REAL NOT NULL CHECK(p_a BETWEEN 0 AND 1),
 p_mid REAL NOT NULL CHECK(p_mid BETWEEN 0 AND 1),
 p_b REAL NOT NULL CHECK(p_b BETWEEN 0 AND 1),
 results_used INTEGER NOT NULL,
 latest_result_at TEXT,
 UNIQUE(game_id, market, tipoff_at),
 CHECK(cutoff_at < tipoff_at AND reconstructed_at >= tipoff_at),
 CHECK(latest_result_at IS NULL OR latest_result_at <= cutoff_at),
 CHECK(abs(p_a+p_mid+p_b-1)<0.000001)
);
CREATE TRIGGER recovered_no_update BEFORE UPDATE ON recovered_forecasts
BEGIN SELECT RAISE(ABORT, 'reconstructions are append-only'); END;
CREATE TRIGGER recovered_no_delete BEFORE DELETE ON recovered_forecasts
BEGIN SELECT RAISE(ABORT, 'reconstructions are append-only'); END;
