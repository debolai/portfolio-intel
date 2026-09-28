"""`pi-ingest` entrypoint: raw files -> validated, versioned DuckDB snapshot."""

import argparse
import logging
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from portfolio_intel.config import settings

from . import validate as dq
from .classify import classify, fetch_industries, region_of
from .ishares import bond_security_ids, read_ishares, split_holdings
from .prices import cached_fx, cached_prices, to_long
from .store import write_snapshot
from .symbols import load_sedol_overrides, map_equities
from .synth import load_tilts, make_tilted
from .vanguard import read_vanguard, resolve_security_ids, to_holdings

log = logging.getLogger("pi-ingest")
RAW = Path("data/raw")
REFERENCE = Path("reference")
FIXTURE_N = 50

PORTFOLIOS = pd.DataFrame(
    [
        ("EQ_EU_BMK", "iShares Core MSCI Europe ETF (IEUR)", "benchmark", None,
         "iShares IEUR", "IEUR"),
        ("EQ_EU_PM", "European equity PM portfolio (synthetic tilt of IEUR)", "portfolio",
         "EQ_EU_BMK", "reference/tilts_eq_eu.yaml", None),
        ("EQ_EU_VGK", "Vanguard FTSE Developed Europe (VGK)", "portfolio", "EQ_EU_BMK",
         "Vanguard VGK", "VGK"),
        ("FI_US_BMK", "iShares Core US Aggregate Bond ETF (AGG)", "benchmark", None,
         "iShares AGG", "AGG"),
        ("FI_US_PM", "US bond PM portfolio (synthetic tilt of AGG)", "portfolio", "FI_US_BMK",
         "reference/tilts_fi_us.yaml", None),
    ],
    columns=["portfolio_id", "name", "kind", "benchmark_id", "source", "etf_symbol"],
).assign(base_currency="USD")  # fmt: skip


@dataclass
class Inputs:
    ieur: pd.DataFrame  # mapped equities + CASH
    ieur_excluded: pd.DataFrame
    agg: pd.DataFrame  # bonds + CASH
    agg_excluded: pd.DataFrame
    vgk_resolved: pd.DataFrame
    vgk: pd.DataFrame  # one row per security_id
    as_of: dict[str, str]


def latest_file(folder: Path, as_of: str) -> Path:
    files = sorted(p for p in folder.glob("*.csv") if p.stem <= as_of)
    if not files:
        raise FileNotFoundError(f"no raw file on or before {as_of} in {folder}")
    return files[-1]


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def parse_inputs(as_of: str) -> Inputs:
    ieur_raw, ieur_as_of = read_ishares(latest_file(RAW / "ishares/IEUR", as_of))
    ieur, ieur_ex = split_holdings(ieur_raw)
    is_eq = ieur["asset_class"] == "Equity"
    ieur = pd.concat(
        [map_equities(ieur[is_eq]), ieur[~is_eq].assign(security_id="CASH")], ignore_index=True
    )

    agg_raw, agg_as_of = read_ishares(latest_file(RAW / "ishares/AGG", as_of))
    agg, agg_ex = split_holdings(agg_raw)
    agg["security_id"] = bond_security_ids(agg)

    vgk_raw, vgk_as_of = read_vanguard(latest_file(RAW / "vanguard/VGK", as_of))
    vgk_resolved = resolve_security_ids(vgk_raw, ieur[is_eq], load_sedol_overrides())
    return Inputs(
        ieur, ieur_ex, agg, agg_ex, vgk_resolved, to_holdings(vgk_resolved),
        {"EQ_EU_BMK": ieur_as_of, "FI_US_BMK": agg_as_of, "EQ_EU_VGK": vgk_as_of},
    )  # fmt: skip


def sector_from_sub_industry(vgk: pd.DataFrame, ieur_eq: pd.DataFrame) -> pd.Series:
    """VGK has no sector column: learn sub-industry -> sector from names held by both funds."""
    both = vgk.merge(ieur_eq[["security_id", "sector"]], on="security_id")
    learned = both.groupby("sub_industry")["sector"].agg(lambda s: str(s.mode().iloc[0]))
    return vgk["sub_industry"].map(learned)


def build_securities(inp: Inputs, industries: dict[str, str]) -> pd.DataFrame:
    eq_cols = ["security_id", "ticker", "exchange", "name", "sector", "country", "currency"]
    ieur_eq = inp.ieur[(inp.ieur["asset_class"] == "Equity") & inp.ieur["security_id"].notna()]
    vgk_only = inp.vgk[~inp.vgk["security_id"].isin(ieur_eq["security_id"])]
    vgk_only = vgk_only[vgk_only["security_id"] != "CASH"].assign(
        exchange=None, sector=lambda x: sector_from_sub_industry(x, ieur_eq)
    )
    equities = pd.concat([ieur_eq[eq_cols], vgk_only[eq_cols]], ignore_index=True).assign(
        asset_class="Equity", price_symbol=lambda x: x["security_id"]
    )
    sub = inp.vgk.set_index("security_id")["sub_industry"].replace("---", None).dropna()
    equities = classify(equities, industries, sub)

    bonds = inp.agg[inp.agg["security_id"] != "CASH"]
    bonds = (
        bonds.groupby("security_id", as_index=False)
        .agg(
            name=("name", "first"), sector=("sector", "first"), country=("country", "first"),
            currency=("currency", "first"), duration=("duration", "first"),
            maturity=("maturity", "first"), coupon_pct=("coupon_pct", "first"),
        )
        .assign(
            asset_class="Fixed Income",
            industry=lambda x: x["sector"],
            industry_group=lambda x: x["sector"],
            region=lambda x: x["country"].map(region_of),
            maturity=lambda x: pd.to_datetime(
                x["maturity"], format="%b %d, %Y", errors="coerce"
            ).dt.date,
        )
    )  # fmt: skip
    cash = pd.DataFrame(
        [{"security_id": "CASH", "name": "Cash", "asset_class": "Cash", "sector": "Cash",
          "industry": "Cash", "industry_group": "Cash", "region": "Cash", "country": "Cash",
          "currency": "USD", "duration": 0.0}]
    )  # fmt: skip
    return pd.concat([equities, bonds, cash], ignore_index=True)


def holdings_table(inp: Inputs, securities: pd.DataFrame) -> tuple[pd.DataFrame, list[dq.Issue]]:
    issues: list[dq.Issue] = []
    ieur = inp.ieur
    issues += dq.check_unmapped("EQ_EU_BMK", ieur[ieur["security_id"].isna()])
    ieur = ieur.dropna(subset=["security_id"])
    bmk_eq = ieur.groupby("security_id", as_index=False)[["weight", "market_value"]].sum()
    bmk_eq["weight"] /= bmk_eq["weight"].sum()  # renormalise after dropping unmapped rows
    agg = inp.agg.groupby("security_id", as_index=False)[["weight", "market_value"]].sum()

    attrs = securities.set_index("security_id")
    eq_pm = make_tilted(
        bmk_eq.join(attrs["industry"], on="security_id"), load_tilts(REFERENCE / "tilts_eq_eu.yaml")
    )
    fi_pm = make_tilted(
        agg.join(attrs[["sector", "duration"]], on="security_id"),
        load_tilts(REFERENCE / "tilts_fi_us.yaml"),
        tilt_column="sector",
    )
    frames = {
        "EQ_EU_BMK": (bmk_eq, inp.as_of["EQ_EU_BMK"]),
        "EQ_EU_PM": (eq_pm, inp.as_of["EQ_EU_BMK"]),
        "EQ_EU_VGK": (inp.vgk[["security_id", "weight", "market_value"]], inp.as_of["EQ_EU_VGK"]),
        "FI_US_BMK": (agg, inp.as_of["FI_US_BMK"]),
        "FI_US_PM": (fi_pm, inp.as_of["FI_US_BMK"]),
    }
    holdings = pd.concat(
        [df.assign(portfolio_id=pid, as_of=a) for pid, (df, a) in frames.items()], ignore_index=True
    )
    holdings["as_of"] = pd.to_datetime(holdings["as_of"]).dt.date
    return holdings, issues


def restrict_to_fixture(holdings: pd.DataFrame) -> pd.DataFrame:
    """50 largest benchmark lines per sleeve; every portfolio restricted and renormalised."""
    keep: set[str] = {"CASH"}
    for bmk in ("EQ_EU_BMK", "FI_US_BMK"):
        b = holdings[(holdings["portfolio_id"] == bmk) & (holdings["security_id"] != "CASH")]
        keep |= set(b.nlargest(FIXTURE_N, "weight")["security_id"])
    out = holdings[holdings["security_id"].isin(keep)].copy()
    out["weight"] = out["weight"] / out.groupby("portfolio_id")["weight"].transform("sum")
    return out


def run(
    as_of: str, out: Path, fixture: bool = False, upload: bool = False, offline: bool = False
) -> str:
    inp = parse_inputs(as_of)
    issues: list[dq.Issue] = []
    issues += dq.check_excluded("EQ_EU_BMK", inp.ieur_excluded)
    issues += dq.check_excluded("FI_US_BMK", inp.agg_excluded)

    equity_ids = sorted(
        (set(inp.ieur["security_id"].dropna()) | set(inp.vgk["security_id"])) - {"CASH"}
    )
    etfs = [s for s in PORTFOLIOS["etf_symbol"].dropna()]
    end = (date.fromisoformat(as_of) + timedelta(days=1)).isoformat()  # yfinance end is exclusive
    start = (
        date.fromisoformat(as_of) - timedelta(days=int(settings.risk_window_days * 1.5) + 30)
    ).isoformat()
    px, failed = cached_prices(equity_ids + etfs, start, end, offline)
    issues += dq.check_vgk_resolution(inp.vgk_resolved, set(failed))

    priced = [s for s in equity_ids if s not in failed]
    industries = fetch_industries(priced, offline=offline)
    securities = build_securities(inp, industries)
    holdings, h_issues = holdings_table(inp, securities)
    issues += h_issues
    if fixture:
        holdings = restrict_to_fixture(holdings)
        securities = securities[securities["security_id"].isin(holdings["security_id"])]
        px = px[[c for c in px.columns if c in set(holdings["security_id"]) | set(etfs)]]

    # --- data-quality checks -------------------------------------------------------------
    issues += dq.check_weights(holdings)
    coverage = dq.price_coverage(px, settings.risk_window_days)
    industry = securities.set_index("security_id")["industry"]
    for pid in ("EQ_EU_BMK", "EQ_EU_PM", "EQ_EU_VGK"):
        w = holdings[holdings["portfolio_id"] == pid].set_index("security_id")["weight"]
        issues += dq.check_industry(pid, w, industry)
        if pid == "EQ_EU_BMK":
            issues += dq.check_price_coverage(pid, w, coverage, settings.min_price_coverage)
    issues += dq.check_prices(px.iloc[-settings.risk_window_days :])
    bonds = securities[securities["asset_class"] == "Fixed Income"]
    issues += dq.check_durations("FI_US_BMK", bonds)
    for i in issues:
        if i.severity != "info" and not i.security_id:
            log.warning("DQ %s %s [%s] %s", i.severity, i.check_name, i.portfolio_id, i.detail)
    dq.raise_on_errors(issues)

    currency = securities.set_index("security_id")["currency"]
    etf_ccy = pd.Series("USD", index=etfs)
    prices = to_long(px, pd.concat([currency, etf_ccy]))
    currencies = set(securities.loc[securities["asset_class"] == "Equity", "currency"].dropna())
    fx_wide = cached_fx(currencies, settings.base_currency, start, end, offline)
    fx = (
        fx_wide.rename_axis("date").reset_index()
        .melt(id_vars="date", var_name="currency", value_name="base_per_unit")
        .dropna()
        .assign(date=lambda x: pd.to_datetime(x["date"]).dt.date)
    )  # fmt: skip

    snapshot_id = str(uuid.uuid4())
    snapshot = pd.DataFrame(
        [{"snapshot_id": snapshot_id, "created_at": datetime.now(UTC).replace(tzinfo=None),
          "as_of": date.fromisoformat(as_of), "methodology_version": settings.methodology_version,
          "git_sha": git_sha()}]
    )  # fmt: skip
    write_snapshot(
        out,
        {
            "snapshot": snapshot, "security": securities, "portfolio": PORTFOLIOS,
            "holding": holdings, "price": prices, "fx": fx, "dq_issue": dq.to_frame(issues),
        },
    )  # fmt: skip
    counts = holdings.groupby("portfolio_id").size().to_dict()
    log.info(
        "wrote %s (snapshot %s): %s holdings, %d prices", out, snapshot_id, counts, len(prices)
    )
    if upload:
        upload_snapshot(out, as_of, snapshot_id)
    return snapshot_id


def upload_snapshot(path: Path, as_of: str, snapshot_id: str) -> None:
    import boto3

    if not settings.data_bucket:
        raise SystemExit("--upload needs PI_DATA_BUCKET")
    key = f"snapshots/{as_of}/{snapshot_id}/portfolio.duckdb"
    s3 = boto3.client("s3")
    s3.upload_file(str(path), settings.data_bucket, key)
    s3.put_object(Bucket=settings.data_bucket, Key="snapshots/LATEST", Body=key.encode())
    log.info("uploaded s3://%s/%s", settings.data_bucket, key)


SYNC_DIRS = ("raw", "cache")


def sync_with_bucket(direction: str) -> None:
    """Mirror data/raw and data/cache with s3://$PI_DATA_BUCKET/{raw,cache}/ ("down" or "up").

    Only missing files are copied: raw files are immutable evidence, and caches only grow.
    """
    import boto3

    if not settings.data_bucket:
        raise SystemExit("--sync needs PI_DATA_BUCKET")
    s3, bucket = boto3.client("s3"), settings.data_bucket
    for prefix in SYNC_DIRS:
        remote: set[str] = set()
        pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=f"{prefix}/")
        for page in pages:
            remote |= {o["Key"] for o in page.get("Contents", [])}
        local_root = Path("data")
        if direction == "down":
            for key in sorted(remote):
                dest = local_root / key
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    s3.download_file(bucket, key, str(dest))
        else:
            for path in sorted((local_root / prefix).rglob("*")):
                key = path.relative_to(local_root).as_posix()
                cache_file = prefix == "cache"  # caches are rewritten; raw files never are
                if path.is_file() and (cache_file or key not in remote):
                    s3.upload_file(str(path), bucket, key)
    log.info("synced data/{raw,cache} %s with s3://%s", direction, bucket)


def main() -> None:
    ap = argparse.ArgumentParser(prog="pi-ingest")
    ap.add_argument("--as-of", required=True, help="YYYY-MM-DD; latest raw files on or before it")
    ap.add_argument("--out", type=Path, default=Path(settings.duckdb_path))
    ap.add_argument(
        "--fixture", action="store_true", help="write the small committed test snapshot"
    )
    ap.add_argument("--upload", action="store_true", help="also upload to s3://$PI_DATA_BUCKET")
    ap.add_argument("--offline", action="store_true", help="use data/cache only; no downloads")
    ap.add_argument(
        "--sync",
        action="store_true",
        help="AWS: pull raw files and caches from the data bucket, fetch today's iShares files, "
        "then push new files back (used by the scheduled ingest task)",
    )
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    if args.sync:
        from .download import fetch_all

        sync_with_bucket("down")
        fetch_all(date.fromisoformat(args.as_of))
    try:
        run(args.as_of, args.out, args.fixture, args.upload, args.offline)
    except dq.DataQualityError as e:
        raise SystemExit(str(e)) from e
    finally:
        if args.sync:
            sync_with_bucket("up")
