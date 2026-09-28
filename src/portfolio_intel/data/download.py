"""Download iShares holdings CSVs into data/raw/ishares/<TICKER>/<as_of>.csv."""

import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import yaml

URL = (
    "https://www.blackrock.com/varnish-api/blk-one01-product-data/product-data/api/v1/"
    "get-fund-document?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=us-ishares"
    "&locale=en_US&userType=individual&component=holdings"
    "&portfolioId={pid}&asOfDate={yyyymmdd}"
)
AS_OF_RE = re.compile(r'Fund Holdings as of,"?([^"\n]+)"?')
HEADERS = {"User-Agent": "Mozilla/5.0 (portfolio-intel research project)"}
RAW = Path("data/raw/ishares")


def previous_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        d -= timedelta(days=1)
    return d


def fetch(pid: int, day: date) -> tuple[bytes, str] | None:
    """Return (raw bytes, as-of date inside the file), or None if no file for that day."""
    r = httpx.get(
        URL.format(pid=pid, yyyymmdd=day.strftime("%Y%m%d")),
        headers=HEADERS,
        timeout=60,
        follow_redirects=True,
    )
    if r.status_code != 200:
        return None
    m = AS_OF_RE.search(r.content.decode("utf-8-sig", errors="replace"))
    if not m:
        return None  # an error page or empty response, not a CSV
    as_of = datetime.strptime(m.group(1).strip(), "%b %d, %Y").date().isoformat()
    return r.content, as_of


def download_ishares(ticker: str, pid: int, day: date, max_lookback: int = 5) -> Path:
    for _ in range(max_lookback):  # step back over holidays
        got = fetch(pid, day)
        if got:
            break
        print(f"{ticker}: nothing for {day}, trying the previous weekday")
        day = previous_weekday(day)
    else:
        raise RuntimeError(f"{ticker}: no holdings file found in the last {max_lookback} weekdays")

    content, as_of = got
    out = RAW / ticker / f"{as_of}.csv"  # named by the date written inside the file
    if out.exists():
        print(f"{ticker}: {out} already exists, not overwriting")
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(content)
    out.chmod(0o444)  # raw files are read-only
    print(f"{ticker}: saved {out}")
    return out


def fetch_all(day: date) -> None:
    cfg = yaml.safe_load(Path("reference/sources.yaml").read_text())
    for ticker, fund in cfg["ishares"].items():
        download_ishares(ticker, int(fund["portfolio_id"]), day)


def main() -> None:
    day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else previous_weekday(date.today())
    fetch_all(day)


if __name__ == "__main__":
    main()
