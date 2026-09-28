"""End-to-end ingest with every network call mocked: parse -> map -> prices -> classify ->
synthesise -> validate -> write DuckDB. Uses tiny iShares files and the committed VGK file."""

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from portfolio_intel.data import classify, download, prices
from portfolio_intel.data import cli as ingest
from portfolio_intel.data.store import open_readonly
from portfolio_intel.data.validate import DataQualityError

ROOT = Path(__file__).resolve().parents[2]

IEUR = """iShares Core MSCI Europe ETF
Fund Holdings as of,"Sep 25, 2026"

Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Location,Exchange,Currency,Market Currency
"ASML","ASML HOLDING","Information Technology","Equity","500.00","50.00","Netherlands","Euronext Amsterdam","USD","EUR"
"HSBA","HSBC HOLDINGS PLC","Financials","Equity","200.00","20.00","United Kingdom","London Stock Exchange","USD","GBP"
"SAN","BANCO SANTANDER","Financials","Equity","150.00","15.00","Spain","Bolsa De Madrid","USD","EUR"
"SAN","SANOFI","Health Care","Equity","140.00","14.00","France","Nyse Euronext - Euronext Paris","USD","EUR"
"EUR","EUR CASH","Cash and/or Derivatives","Cash","10.00","1.00","-","-","USD","EUR"
"""
AGG = """iShares Core U.S. Aggregate Bond ETF
Fund Holdings as of,"Sep 25, 2026"

Name,Sector,Asset Class,Market Value,Weight (%),CUSIP,ISIN,Location,Exchange,Currency,Duration,Maturity,Coupon (%),Market Currency
"TREASURY NOTE","Treasury","Fixed Income","600.00","60.00","91282CMM0","US91282CMM00","United States","-","USD","6.81","Feb 15, 2035","4.63","USD"
"APPLE INC","Industrial","Fixed Income","300.00","30.00","037833AA1","US037833AA10","United States","-","USD","12.10","Feb 15, 2050","3.00","USD"
"USD CASH","Cash and/or Derivatives","Cash","100.00","10.00","-","-","United States","-","USD","0.00","-","0.00","USD"
"""


def fake_prices(symbols, start, end, batch=100):
    idx = pd.bdate_range("2024-06-03", "2026-09-25")
    rng = np.random.default_rng(len(symbols))
    data = {s: 100 * np.cumprod(1 + rng.normal(0, 0.01, len(idx))) for s in symbols}
    return pd.DataFrame(data, index=idx)


def fake_fx(currencies, base, start, end):
    idx = pd.bdate_range("2024-06-03", "2026-09-25")
    fx = pd.DataFrame({c: 1.1 for c in currencies}, index=idx)
    fx[base] = 1.0
    return fx


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    for fund, text in (("IEUR", IEUR), ("AGG", AGG)):
        d = tmp_path / "data/raw/ishares" / fund
        d.mkdir(parents=True)
        (d / "2026-09-25.csv").write_text(text)
    vgk = tmp_path / "data/raw/vanguard/VGK"
    vgk.mkdir(parents=True)
    shutil.copy(ROOT / "data/fixtures/raw/vanguard/VGK/2026-08-31.csv", vgk)
    shutil.copytree(ROOT / "reference", tmp_path / "reference")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(prices, "CACHE", tmp_path / "data/cache")
    monkeypatch.setattr(prices, "download_prices", fake_prices)
    monkeypatch.setattr(prices, "download_fx", fake_fx)
    monkeypatch.setattr(classify, "CACHE_FILE", tmp_path / "data/cache/industry.json")
    monkeypatch.setattr(classify, "_fetch_one", lambda s: "Banks - Diversified")
    monkeypatch.setattr(classify.time, "sleep", lambda _: None)
    return tmp_path


def test_full_ingest(workspace):
    out = workspace / "data/portfolio.duckdb"
    sid = ingest.run("2026-09-25", out)
    con = open_readonly(out)
    try:
        assert con.execute("SELECT snapshot_id FROM snapshot").fetchone()[0] == sid
        w = dict(con.execute("SELECT portfolio_id, sum(weight) FROM holding GROUP BY 1").fetchall())
        assert set(w) == {"EQ_EU_BMK", "EQ_EU_PM", "EQ_EU_VGK", "FI_US_BMK", "FI_US_PM"}
        assert all(v == pytest.approx(1.0) for v in w.values())
        ids = {r[0] for r in con.execute("SELECT security_id FROM security").fetchall()}
        assert {"SAN.MC", "SAN.PA", "US91282CMM00", "CASH"} <= ids  # duplicate SAN resolved
        n_dq = con.execute("SELECT count(*) FROM dq_issue").fetchone()[0]
        assert n_dq > 0
    finally:
        con.close()
    # second run is served from the cache and is offline-capable
    ingest.run("2026-09-25", workspace / "data/fixture.duckdb", fixture=True, offline=True)


def test_ingest_stops_on_data_quality_errors(workspace, monkeypatch):
    monkeypatch.setattr(prices, "download_prices", lambda *a, **k: pd.DataFrame())
    with pytest.raises(DataQualityError):
        ingest.run("2026-09-25", workspace / "data/p.duckdb")


def test_industry_fetch_backs_off_and_resumes(workspace, monkeypatch):
    calls = iter([classify.RateLimitedError("X"), "Banks - Regional", "Insurance - Life"])

    def flaky(sym):
        v = next(calls)
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(classify, "_fetch_one", flaky)
    got = classify.fetch_industries(["A.L", "B.L"])
    assert got == {"A.L": "Banks - Regional", "B.L": "Insurance - Life"}
    assert json.loads(classify.CACHE_FILE.read_text())["A.L"] == "Banks - Regional"


def test_latest_file_and_missing(workspace):
    assert ingest.latest_file(Path("data/raw/ishares/IEUR"), "2026-12-31").stem == "2026-09-25"
    with pytest.raises(FileNotFoundError):
        ingest.latest_file(Path("data/raw/ishares/IEUR"), "2020-01-01")


def test_download_names_file_by_inner_date(workspace, monkeypatch):
    class R:
        status_code = 200
        content = b'iShares X\nFund Holdings as of,"Sep 25, 2026"\n'

    monkeypatch.setattr(download.httpx, "get", lambda *a, **k: R())
    monkeypatch.setattr(download, "RAW", workspace / "raw")
    p = download.download_ishares("TEST", 1, pd.Timestamp("2026-09-27").date())
    assert p.name == "2026-09-25.csv"
    assert download.download_ishares("TEST", 1, pd.Timestamp("2026-09-27").date()) == p
    assert download.previous_weekday(pd.Timestamp("2026-09-28").date()).isoformat() == "2026-09-25"
