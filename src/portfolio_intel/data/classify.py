"""Industry enrichment (Yahoo-derived, not GICS) + manual overrides, and region."""

import json
import logging
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)
CACHE_FILE = Path("data/cache/industry.json")
MAP_FILE = Path("reference/industry_map.csv")
OVERRIDES_FILE = Path("reference/industry_overrides.csv")

EUROPE = {
    "Austria", "Belgium", "Denmark", "Finland", "France", "Germany", "Greece", "Ireland",
    "Italy", "Luxembourg", "Netherlands", "Norway", "Poland", "Portugal", "Spain", "Sweden",
    "Switzerland", "United Kingdom",
}  # fmt: skip
NORTH_AMERICA = {"United States", "Canada"}


def region_of(country: str | None) -> str:
    if country in EUROPE:
        return "Europe"
    if country in NORTH_AMERICA:
        return "North America"
    return "Other"


class RateLimitedError(RuntimeError):
    pass


def _fetch_one(symbol: str) -> str:
    try:
        info = yf.Ticker(symbol).info
    except Exception as e:  # yfinance raises many different errors for unknown symbols
        if "Rate" in type(e).__name__ or "Too Many Requests" in str(e):
            raise RateLimitedError(symbol) from e
        log.debug("%s: %s", symbol, e)
        return ""
    return str(info.get("industry") or "")


def _save(cache: dict[str, str]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=0, sort_keys=True))


def fetch_industries(
    symbols: list[str], pause: float = 0.4, max_backoff: float = 300.0, offline: bool = False
) -> dict[str, str]:
    """symbol -> Yahoo industry ("" when Yahoo has none). Cached in data/cache/industry.json.

    Yahoo rate-limits `.info` hard, so symbols are fetched one at a time with a pause, and a
    429 triggers exponential backoff. Rate-limited symbols are never cached as "no industry";
    if backoff is exhausted the remaining symbols stay uncached and the next run resumes.
    """
    cache: dict[str, str] = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}
    missing = [] if offline else [s for s in symbols if s not in cache]
    if missing:
        log.info("fetching Yahoo industry for %d symbols", len(missing))
    backoff = 30.0
    for i, sym in enumerate(missing):
        while True:
            try:
                cache[sym] = _fetch_one(sym)
                backoff = 30.0
                break
            except RateLimitedError:
                if backoff > max_backoff:
                    log.warning("Yahoo still rate-limiting; %d symbols left", len(missing) - i)
                    _save(cache)
                    return {s: cache.get(s, "") for s in symbols}
                log.info("rate limited; sleeping %.0fs", backoff)
                _save(cache)
                time.sleep(backoff)
                backoff *= 2
        if i % 25 == 0:  # checkpoint, so an interrupted run resumes
            _save(cache)
            log.info("industry %d/%d", i, len(missing))
        time.sleep(pause)
    _save(cache)
    return {s: cache.get(s, "") for s in symbols}


def classify(
    securities: pd.DataFrame,
    yahoo_industry: dict[str, str],
    sub_industry: pd.Series | None = None,
) -> pd.DataFrame:
    """Add industry, industry_group, industry_source and region to equity rows.

    Priority: manual override > Yahoo industry > Vanguard (GICS-style) sub-industry, each mapped
    onto one taxonomy by reference/industry_map.csv. `sub_industry` is security_id -> label.
    """
    m = pd.read_csv(MAP_FILE, keep_default_na=False)
    maps = {src: g.set_index("source_industry") for src, g in m.groupby("source")}
    out = securities.copy()
    out["yahoo_industry"] = out["security_id"].map(yahoo_industry).fillna("")
    out["industry"] = out["yahoo_industry"].map(maps["yahoo"]["industry"])
    out["industry_group"] = out["yahoo_industry"].map(maps["yahoo"]["industry_group"])
    out["industry_source"] = out["industry"].notna().map({True: "yahoo", False: ""})
    if sub_industry is not None:
        sub = out["security_id"].map(sub_industry)
        fill = out["industry"].isna() & sub.isin(maps["vanguard"].index)
        out.loc[fill, "industry"] = sub[fill].map(maps["vanguard"]["industry"])
        out.loc[fill, "industry_group"] = sub[fill].map(maps["vanguard"]["industry_group"])
        out.loc[fill, "industry_source"] = "vanguard"
    if OVERRIDES_FILE.exists():
        ov = pd.read_csv(OVERRIDES_FILE, keep_default_na=False).set_index("security_id")
        hit = out["security_id"].isin(ov.index)
        out.loc[hit, "industry"] = out.loc[hit, "security_id"].map(ov["industry"])
        out.loc[hit, "industry_group"] = out.loc[hit, "security_id"].map(ov["industry_group"])
        out.loc[hit, "industry_source"] = "override"
    out["region"] = out["country"].map(region_of)
    return out
