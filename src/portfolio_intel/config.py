"""Settings via pydantic-settings; all env vars."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PI_", extra="ignore")

    env: str = "local"
    base_currency: str = "USD"
    duckdb_path: str = "data/portfolio.duckdb"
    data_bucket: str | None = None  # s3 bucket in AWS
    snapshot_key: str | None = None  # e.g. snapshots/2026-09-25/portfolio.duckdb

    risk_window_days: int = 504  # ~2 years of daily returns
    min_price_coverage: float = 0.90
    methodology_version: str = "1.0.0"

    mcp_url: str = "http://localhost:8001/mcp"
    mcp_token: str = "dev-token-change-me"
    anthropic_model: str = "claude-sonnet-5"
    agent_max_steps: int = 8

    audit_sink: str = "jsonl"  # jsonl | firehose
    audit_path: str = "data/audit.jsonl"
    audit_stream: str | None = None


settings = Settings()
