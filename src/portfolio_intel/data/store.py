"""DuckDB schema + read-only access. Only the ingest CLI opens the database writable."""

from pathlib import Path

import duckdb
import pandas as pd

SCHEMA = """
CREATE TABLE snapshot   (snapshot_id VARCHAR, created_at TIMESTAMP, as_of DATE,
                         methodology_version VARCHAR, git_sha VARCHAR);
CREATE TABLE security   (security_id VARCHAR PRIMARY KEY, ticker VARCHAR, exchange VARCHAR,
                         name VARCHAR, asset_class VARCHAR, sector VARCHAR, industry VARCHAR,
                         industry_group VARCHAR, country VARCHAR, region VARCHAR,
                         currency VARCHAR, price_symbol VARCHAR,
                         duration DOUBLE, maturity DATE, coupon_pct DOUBLE);
CREATE TABLE portfolio  (portfolio_id VARCHAR PRIMARY KEY, name VARCHAR, kind VARCHAR,
                         benchmark_id VARCHAR, base_currency VARCHAR, source VARCHAR,
                         etf_symbol VARCHAR);
CREATE TABLE holding    (as_of DATE, portfolio_id VARCHAR, security_id VARCHAR,
                         weight DOUBLE, market_value DOUBLE);
CREATE TABLE price      (date DATE, security_id VARCHAR, close DOUBLE,
                         currency VARCHAR, source VARCHAR);
CREATE TABLE fx         (date DATE, currency VARCHAR, base_per_unit DOUBLE);
CREATE TABLE dq_issue   (check_name VARCHAR, portfolio_id VARCHAR, security_id VARCHAR,
                         severity VARCHAR, detail VARCHAR);
"""

TABLE_COLUMNS = {
    "snapshot": ["snapshot_id", "created_at", "as_of", "methodology_version", "git_sha"],
    "security": [
        "security_id", "ticker", "exchange", "name", "asset_class", "sector", "industry",
        "industry_group", "country", "region", "currency", "price_symbol",
        "duration", "maturity", "coupon_pct",
    ],
    "portfolio": [
        "portfolio_id", "name", "kind", "benchmark_id", "base_currency", "source", "etf_symbol",
    ],
    "holding": ["as_of", "portfolio_id", "security_id", "weight", "market_value"],
    "price": ["date", "security_id", "close", "currency", "source"],
    "fx": ["date", "currency", "base_per_unit"],
    "dq_issue": ["check_name", "portfolio_id", "security_id", "severity", "detail"],
}  # fmt: skip


def write_snapshot(path: Path, tables: dict[str, pd.DataFrame]) -> None:
    """Write a fresh database file (replacing any existing one) from DataFrames."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.duckdb")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    try:
        con.execute(SCHEMA)
        for name, cols in TABLE_COLUMNS.items():
            df = tables.get(name, pd.DataFrame(columns=cols)).reindex(columns=cols)
            con.register("df_in", df)
            con.execute(f"INSERT INTO {name} SELECT * FROM df_in")
            con.unregister("df_in")
    finally:
        con.close()
    tmp.replace(path)  # atomic: readers never see a half-written file


def open_readonly(path: str | Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path), read_only=True)
