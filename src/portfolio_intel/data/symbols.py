"""Exchange -> Yahoo suffix mapping; the Yahoo symbol is each equity's security ID."""

import re
from pathlib import Path

import pandas as pd

REFERENCE = Path("reference")


def normalise_ticker(ticker: str) -> str:
    t = ticker.strip().rstrip("./")  # "RR." (iShares) and "RR/" (Vanguard) -> "RR"
    return re.sub(r"[ ./]+", "-", t)  # "BT.A", "BT/A" -> "BT-A"; "NOVO B" -> "NOVO-B"


def load_suffixes(path: Path = REFERENCE / "exchange_suffix.csv") -> dict[str, str]:
    df = pd.read_csv(path, keep_default_na=False)  # keep empty suffixes as ""
    return dict(zip(df["exchange"], df["yahoo_suffix"], strict=True))


def load_overrides(
    path: Path = REFERENCE / "symbol_overrides.csv",
) -> dict[tuple[str, str], str]:
    """(ticker, exchange) -> symbol, for iShares rows."""
    df = pd.read_csv(path, keep_default_na=False, dtype=str)
    return {
        (str(t), str(e)): str(y)
        for t, e, y in zip(df["ticker"], df["exchange"], df["yahoo_symbol"], strict=True)
        if t
    }


def load_sedol_overrides(path: Path = REFERENCE / "symbol_overrides.csv") -> dict[str, str]:
    """SEDOL -> symbol, for Vanguard rows."""
    df = pd.read_csv(path, keep_default_na=False, dtype=str)
    return {str(s): str(y) for s, y in zip(df["sedol"], df["yahoo_symbol"], strict=True) if s}


def to_yahoo(
    ticker: str,
    exchange: str,
    suffixes: dict[str, str],
    overrides: dict[tuple[str, str], str],
) -> str | None:
    if (ticker, exchange) in overrides:
        return overrides[(ticker, exchange)]
    if exchange not in suffixes:
        return None  # unmapped exchange: reported by the DQ check
    return normalise_ticker(ticker) + suffixes[exchange]


def map_equities(equities: pd.DataFrame) -> pd.DataFrame:
    """Add `security_id` and `price_symbol` to iShares equity rows (None when unmapped)."""
    suffixes, overrides = load_suffixes(), load_overrides()
    out = equities.copy()
    sym = [
        to_yahoo(t, e, suffixes, overrides) if isinstance(t, str) and t else None
        for t, e in zip(out["ticker"], out["exchange"], strict=True)
    ]
    out["security_id"] = sym
    out["price_symbol"] = sym
    return out
