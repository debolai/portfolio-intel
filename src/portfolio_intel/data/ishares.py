"""Parse iShares holdings CSVs and turn them into security-level holdings."""

import io
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

AS_OF_RE = re.compile(r'Fund Holdings as of,"?([^"\n]+)"?')

COLUMNS = {
    "Ticker": "ticker",
    "Name": "name",
    "Sector": "sector",
    "Asset Class": "asset_class",
    "Market Value": "market_value",
    "Weight (%)": "weight_pct",
    "Location": "country",
    "Exchange": "exchange",
    "Currency": "fund_currency",  # the ETF's reporting currency (USD on every row)
    "Market Currency": "currency",  # the security's own trading currency
    # fixed-income files only:
    "ISIN": "isin",
    "CUSIP": "cusip",
    "SEDOL": "sedol",
    "Duration": "duration",  # effective duration (see docs/methodology.md)
    "Mod. Duration": "mod_duration",
    "Maturity": "maturity",
    "Coupon (%)": "coupon_pct",
    "YTM (%)": "ytm_pct",
}

SECURITY_CLASSES = {"Equity", "Fixed Income"}
CASH_CLASSES = {"Cash", "Money Market"}
EXCLUDED_CLASSES = {"Cash Collateral and Margins", "FX", "Futures"}
NON_TRADEABLE_RE = re.compile(r"-RTS\b|-WRT\b|-CVR\b|- CVR\b", re.IGNORECASE)


def read_ishares(path: Path) -> tuple[pd.DataFrame, str]:
    """Raw holdings table exactly as published (numbers parsed, columns renamed)."""
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    m = AS_OF_RE.search(text)
    if not m:
        raise ValueError(f"{path}: no 'Fund Holdings as of' line")
    as_of = datetime.strptime(m.group(1).strip(), "%b %d, %Y").date().isoformat()

    start = next(
        i for i, line in enumerate(lines) if line.lstrip('"').startswith(("Ticker,", "Name,"))
    )
    body = []
    for line in lines[start:]:
        if not line.strip() or line.startswith(("\xa0", '"\xa0', "The content")):
            break  # end of holdings; disclaimer footer follows
        body.append(line)
    df = pd.read_csv(
        io.StringIO("\n".join(body)),
        thousands=",",
        na_values=["-", ""],
        keep_default_na=False,
        dtype={"Ticker": str, "CUSIP": str, "ISIN": str, "SEDOL": str},
    )
    df = df.rename(columns={k: v for k, v in COLUMNS.items() if k in df.columns})
    df["printed_weight"] = df.pop("weight_pct") / 100.0
    return df, as_of


def split_holdings(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply the non-security row rules (blueprint 2.2, step 3).

    Returns (holdings, excluded). Holdings are securities plus one aggregated CASH row,
    with `weight` computed from market value so that it sums to exactly 1. Printed weights
    are rounded to 0.01%, which loses most of AGG's ~13,000 small bond lines.
    `excluded` lists the dropped rows with a reason and their share of gross market value.
    """
    df = raw.copy()
    name = df["name"].fillna("")
    reason = pd.Series("", index=df.index)
    reason[df["asset_class"].isin(EXCLUDED_CLASSES)] = df["asset_class"]
    reason[df["asset_class"].isin(SECURITY_CLASSES) & name.str.contains(NON_TRADEABLE_RE)] = (
        "right/warrant/CVR"
    )
    unknown = ~df["asset_class"].isin(SECURITY_CLASSES | CASH_CLASSES | EXCLUDED_CLASSES)
    reason[unknown] = "unknown asset class: " + df.loc[unknown, "asset_class"].astype(str)

    total_mv = float(df["market_value"].sum())
    excluded = df[reason != ""].assign(
        excluded_reason=reason[reason != ""],
        weight=lambda x: x["market_value"] / total_mv,
    )

    kept = df[reason == ""]
    is_cash = kept["asset_class"].isin(CASH_CLASSES)
    securities = kept[~is_cash].copy()
    cash = pd.DataFrame(
        [
            {
                "name": "CASH",
                "asset_class": "Cash",
                "market_value": float(kept.loc[is_cash, "market_value"].sum()),
                "currency": "USD",
                "duration": 0.0,
            }
        ]
    )
    holdings = pd.concat([securities, cash], ignore_index=True)
    holdings["weight"] = holdings["market_value"] / holdings["market_value"].sum()
    return holdings, excluded


def bond_security_ids(df: pd.DataFrame) -> pd.Series:
    """ISIN, falling back to CUSIP; CASH for the cash line."""
    sid = df["isin"].where(df["isin"].notna() & (df["isin"] != ""), df["cusip"])
    return sid.where(df["asset_class"] != "Cash", "CASH")
