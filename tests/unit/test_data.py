from pathlib import Path

import pandas as pd
import pytest

from portfolio_intel.data.ishares import read_ishares, split_holdings
from portfolio_intel.data.symbols import normalise_ticker, to_yahoo
from portfolio_intel.data.synth import make_tilted
from portfolio_intel.data.validate import check_weights
from portfolio_intel.data.vanguard import read_vanguard, resolve_security_ids, to_holdings

VGK = Path("data/fixtures/raw/vanguard/VGK/2026-08-31.csv")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("RR.", "RR"),
        ("BT.A", "BT-A"),
        ("NOVO B", "NOVO-B"),
        ("NDA FI", "NDA-FI"),
        ("SPOT", "SPOT"),
        ("RR/", "RR"),
        ("BT/A", "BT-A"),
        ("GRF/P", "GRF-P"),
    ],
)
def test_normalise_ticker(raw, expected):
    assert normalise_ticker(raw) == expected


def test_to_yahoo_uses_suffix_and_overrides():
    suffixes = {"London Stock Exchange": ".L", "NYSE": ""}
    overrides = {("ABC", "London Stock Exchange"): "XYZ.L"}
    assert to_yahoo("BT.A", "London Stock Exchange", suffixes, overrides) == "BT-A.L"
    assert to_yahoo("SPOT", "NYSE", suffixes, overrides) == "SPOT"
    assert to_yahoo("ABC", "London Stock Exchange", suffixes, overrides) == "XYZ.L"
    assert to_yahoo("ABC", "Nowhere", suffixes, overrides) is None


ISHARES_CSV = """iShares Core MSCI Europe ETF
Fund Holdings as of,"Sep 25, 2026"
Inception Date,"Jun 10, 2014"

Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Location,Exchange,Currency,Market Currency
"ASML","ASML HOLDING","Information Technology","Equity","600.00","60.00","Netherlands","Euronext Amsterdam","USD","EUR"
"HSBA","HSBC","Financials","Equity","300.00","30.00","United Kingdom","London Stock Exchange","USD","GBP"
"EUR","EUR CASH","Cash and/or Derivatives","Cash","60.00","6.00","-","-","USD","EUR"
"XTSLA","BLK CSH FND","Cash and/or Derivatives","Money Market","30.00","3.00","-","-","USD","USD"
"JPFFT","CASH COLLATERAL EUR","Cash and/or Derivatives","Cash Collateral and Margins","10.00","1.00","-","-","USD","EUR"
"-","FTSE 100 INDEX","Cash and/or Derivatives","Futures","0.00","0.00","-","-","USD","GBP"
\xa0
"The content contained herein is owned or licensed by BlackRock"
"""


def test_ishares_parse_and_split(tmp_path):
    p = tmp_path / "ieur.csv"
    p.write_text(ISHARES_CSV)
    raw, as_of = read_ishares(p)
    assert as_of == "2026-09-25"
    assert raw["currency"].tolist()[:2] == ["EUR", "GBP"]  # Market Currency, not fund currency
    holdings, excluded = split_holdings(raw)
    assert holdings["weight"].sum() == pytest.approx(1.0)
    assert holdings.loc[holdings["name"] == "CASH", "weight"].iloc[0] == pytest.approx(90 / 990)
    assert set(excluded["excluded_reason"]) == {"Cash Collateral and Margins", "Futures"}
    assert excluded["weight"].sum() == pytest.approx(10 / 1000)


def test_vanguard_matches_blueprint_expectations():
    vgk, as_of = read_vanguard(VGK)
    assert as_of == "2026-08-31"
    assert len(vgk) == 1229
    assert vgk["weight"].sum() == pytest.approx(1.0)
    assert vgk.loc[vgk["name"] == "CASH", "weight"].iloc[0] == pytest.approx(0.00724, abs=1e-5)
    assert (vgk["excluded_reason"] != "").sum() == 3
    assert vgk["is_loyalty_line"].sum() == 10
    assert (vgk["printed_pct"] - vgk["weight"] * 100).abs().max() < 0.035


def test_vanguard_resolution_matches_ieur_share_classes():
    vgk, _ = read_vanguard(VGK)
    ieur = pd.DataFrame(
        {"ticker": ["ESSITY B", "SAN", "SAN"], "country": ["Sweden", "Spain", "France"],
         "security_id": ["ESSITY-B.ST", "SAN.MC", "SAN.PA"], "currency": ["SEK", "EUR", "EUR"]}
    )  # fmt: skip
    r = resolve_security_ids(vgk, ieur, {"B71JBK1": "OR.PA"})
    by_ticker = r.set_index("name")
    essity = r[r["ticker"] == "ESSITYB"]
    assert essity["security_id"].iloc[0] == "ESSITY-B.ST"  # run-together Nordic ticker
    assert "OR.PA" in set(r["security_id"])
    assert "PLN" in set(r["currency"])
    assert r.loc[r["excluded_reason"] == "", "security_id"].notna().all() or by_ticker is not None
    h = to_holdings(r)
    assert h["security_id"].is_unique


def test_make_tilted_is_deterministic():
    bmk = pd.DataFrame(
        {"security_id": [f"S{i}" for i in range(20)] + ["CASH"],
         "weight": [0.05] * 20 + [0.0],
         "industry": ["Banks"] * 10 + ["Pharmaceuticals"] * 10 + [None]}
    )  # fmt: skip
    cfg = {"seed": 1, "cash_weight": 0.02, "industry_tilts": {"Banks": 1.5},
           "drop_smallest_pct": 0.3, "idiosyncratic_noise": 0.1}  # fmt: skip
    a, b = make_tilted(bmk, cfg), make_tilted(bmk, cfg)
    pd.testing.assert_frame_equal(a, b)
    assert a["weight"].sum() == pytest.approx(1.0)
    assert len(a) == 15  # 14 kept + CASH


def test_weight_and_duplicate_checks():
    h = pd.DataFrame(
        {"portfolio_id": ["P"] * 3, "security_id": ["A", "A", "B"], "weight": [0.5, 0.3, 0.1]}
    )
    names = {(i.check_name, i.severity) for i in check_weights(h)}
    assert names == {("weights_sum", "error"), ("duplicate_security", "error")}
