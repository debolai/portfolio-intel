"""Every test runs against the small committed fixture snapshot; no internet, no real keys."""

import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/fixtures/portfolio_small.duckdb"
AUDIT = Path(tempfile.mkdtemp()) / "audit.jsonl"

# Must run before portfolio_intel.config is imported (Settings() reads env at import time).
os.environ["PI_DUCKDB_PATH"] = str(FIXTURE)
os.environ["PI_AUDIT_PATH"] = str(AUDIT)
os.environ["PI_AUDIT_SINK"] = "jsonl"
os.environ.setdefault("PI_MCP_TOKEN", "test-token")
os.environ["PI_DATA_BUCKET"] = ""
os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")

import pytest  # noqa: E402

from portfolio_intel.service import PortfolioService  # noqa: E402


@pytest.fixture(scope="session")
def svc() -> PortfolioService:
    return PortfolioService(FIXTURE)


@pytest.fixture
def audit_path() -> Path:
    return AUDIT
