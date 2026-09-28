"""yfinance / Stooq price loaders + FX, cached as Parquet under data/cache/."""

import io
import logging
from pathlib import Path

import httpx
import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)
CACHE = Path("data/cache")
STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"


def _yahoo_close(chunk: list[str], start: str, end: str) -> pd.DataFrame:
    raw = yf.download(
        chunk,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        group_by="ticker",
        threads=True,
    )
    if raw is None or raw.empty:
        return pd.DataFrame()
    got = [s for s in chunk if s in raw.columns.get_level_values(0)]
    close = pd.concat({s: raw[s]["Close"] for s in got}, axis=1)
    out: pd.DataFrame = close.dropna(axis=1, how="all")
    return out


def download_prices(symbols: list[str], start: str, end: str, batch: int = 100) -> pd.DataFrame:
    """Wide frame: index=date, columns=symbol, values=adjusted close in local currency."""
    frames = [
        _yahoo_close(symbols[i : i + batch], start, end) for i in range(0, len(symbols), batch)
    ]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1).sort_index()
    out.index = pd.DatetimeIndex(out.index).tz_localize(None).normalize()
    return out


def stooq_close(symbol: str, start: str, end: str) -> pd.Series | None:
    """Fallback source. `symbol` must already carry the Stooq suffix (e.g. 'hsba.uk')."""
    try:
        r = httpx.get(STOOQ_URL.format(symbol=symbol.lower()), timeout=30)
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or not r.text.startswith("Date"):
        return None
    df = pd.read_csv(io.StringIO(r.text), parse_dates=["Date"], index_col="Date")
    s = df["Close"].loc[start:end]
    return s if len(s) else None


def cached_prices(
    symbols: list[str], start: str, end: str, offline: bool = False
) -> tuple[pd.DataFrame, list[str]]:
    """Prices for `symbols` from the Parquet cache, downloading only what is missing.

    Symbols that fail are not remembered: Yahoo failures are often transient, so they are
    retried on the next run (a handful of dead listings costs one small request).

    Returns (wide prices, symbols that could not be downloaded).
    """
    path = CACHE / "prices.parquet"
    cache = pd.read_parquet(path) if path.exists() else pd.DataFrame()

    have = set(cache.columns)
    stale = len(cache) and cache.index.max() < pd.Timestamp(end) - pd.Timedelta(days=4)
    if stale and not offline:
        have = set()  # refresh everything to extend the history
    missing = [s for s in symbols if s not in have]
    if offline:
        missing = []
    if missing:
        log.info("downloading prices for %d symbols", len(missing))
        new = download_prices(missing, start, end)
        cache = (
            new
            if cache.empty
            else cache.drop(columns=new.columns, errors="ignore").join(new, how="outer")
        )
        CACHE.mkdir(parents=True, exist_ok=True)
        cache.sort_index().to_parquet(path)

    cols = [s for s in symbols if s in cache.columns]
    out = cache.loc[start:end, cols]
    return out, [s for s in symbols if s not in cache.columns]


def download_fx(currencies: set[str], base: str, start: str, end: str) -> pd.DataFrame:
    """Columns = currency, values = units of BASE per 1 unit of that currency."""
    pairs = {c: f"{c}{base}=X" for c in sorted(currencies) if c != base}
    raw = yf.download(list(pairs.values()), start=start, end=end, progress=False)
    if raw is None:
        raise RuntimeError("FX download failed")
    close = raw["Close"]
    fx = pd.DataFrame({c: close[p] for c, p in pairs.items()})
    fx[base] = 1.0
    fx.index = pd.DatetimeIndex(fx.index).tz_localize(None).normalize()
    return fx.sort_index()


def cached_fx(
    currencies: set[str], base: str, start: str, end: str, offline: bool = False
) -> pd.DataFrame:
    path = CACHE / "fx.parquet"
    if offline:
        return pd.read_parquet(path).loc[start:end, sorted(currencies | {base})]
    if path.exists():
        fx = pd.read_parquet(path)
        fresh = fx.index.max() >= pd.Timestamp(end) - pd.Timedelta(days=4)
        if fresh and currencies <= set(fx.columns) and fx.index.min() <= pd.Timestamp(start):
            return fx.loc[start:end, sorted(currencies | {base})]
    fx = download_fx(currencies, base, start, end)
    CACHE.mkdir(parents=True, exist_ok=True)
    fx.to_parquet(path)
    return fx


def to_long(wide: pd.DataFrame, currency: pd.Series, source: str = "yahoo") -> pd.DataFrame:
    """Wide prices (columns = price symbol = security_id) -> date, security_id, close, ..."""
    long = (
        wide.rename_axis("date")
        .reset_index()
        .melt(id_vars="date", var_name="security_id", value_name="close")
        .dropna(subset=["close"])
    )
    long["currency"] = long["security_id"].map(currency)
    long["source"] = source
    long["date"] = pd.to_datetime(long["date"]).dt.date
    return long
