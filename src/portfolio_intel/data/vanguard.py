"""Parse Vanguard 'Holdings details' CSV exports (e.g. VGK)."""

import io
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from .symbols import normalise_ticker

SECTION_RE = re.compile(r"^(Equity|Fixed income|Short-term reserves),as of (\d{2}/\d{2}/\d{4})")
ISO2_TO_COUNTRY = {
    "AT": "Austria", "BE": "Belgium", "CH": "Switzerland", "DE": "Germany", "DK": "Denmark",
    "ES": "Spain", "FI": "Finland", "FR": "France", "GB": "United Kingdom", "IE": "Ireland",
    "IT": "Italy", "NL": "Netherlands", "NO": "Norway", "PL": "Poland", "PT": "Portugal",
    "SE": "Sweden", "US": "United States",
}  # fmt: skip
# Loyalty-share ("prime de fidélité") and untickered French lines -> parent company name
PF_RE = re.compile(r"\s*-\s*PF\b.*$|\s*\(PRIM FIDELITE\).*$|\s+EUR4\b", re.IGNORECASE)
NON_TRADEABLE_RE = re.compile(r"-RTS\b|-WRT\b|- CVR\b", re.IGNORECASE)


def _money(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.str.replace(r"[$,]", "", regex=True), errors="coerce")


def _sections(lines: list[str]) -> dict[str, tuple[str, pd.DataFrame]]:
    out = {}
    for i, line in enumerate(lines):
        m = SECTION_RE.match(line)
        if not m:
            continue
        h = next(k for k in range(i + 1, len(lines)) if lines[k].startswith(",SEDOL"))
        rows = [lines[h]]
        for row in lines[h + 1 :]:
            if not row.strip():
                break
            rows.append(row)
        df = pd.read_csv(io.StringIO("\n".join(rows)), dtype=str, keep_default_na=False)
        df = df.drop(columns=[c for c in df.columns if c.startswith("Unnamed")])
        as_of = datetime.strptime(m.group(2), "%m/%d/%Y").date().isoformat()
        out[m.group(1)] = (as_of, df)
    return out


def read_vanguard(path: Path) -> tuple[pd.DataFrame, str]:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    secs = _sections(lines)
    as_of, eq = secs["Equity"]

    df = pd.DataFrame(
        {
            "sedol": eq["SEDOL"].str.strip(),
            "name": eq["HOLDINGS"].str.strip(),
            "ticker": eq["TICKER"].str.strip().replace("---", ""),
            "sub_industry": eq["SUB-INDUSTRY"].str.strip(),
            "country": eq["COUNTRY"].map(ISO2_TO_COUNTRY).fillna(eq["COUNTRY"]),
            "dr_type": eq["SECURITYDEPOSITORYRECEIPTTYPE"].replace("---", ""),
            "market_value": _money(eq["MARKET VALUE*"]),
            "printed_pct": pd.to_numeric(eq["% OF FUNDS*"].str.rstrip("%"), errors="coerce"),
            "asset_class": "Equity",
        }
    )
    df["is_loyalty_line"] = df["name"].str.contains(PF_RE)
    df["parent_name"] = df["name"].str.replace(PF_RE, "", regex=True).str.strip()
    df["excluded_reason"] = ""
    df.loc[df["name"].str.contains(NON_TRADEABLE_RE), "excluded_reason"] = "right/warrant/CVR"

    # Cash: every line in "Short-term reserves" (currencies, sweep/liquidity funds) -> one CASH row
    cash_mv = 0.0
    if "Short-term reserves" in secs:
        cash_mv = float(_money(secs["Short-term reserves"][1]["MARKET VALUE*"]).sum())
    if "Fixed income" in secs and len(secs["Fixed income"][1]):
        raise ValueError("VGK unexpectedly holds fixed income; extend the parser")
    cash = pd.DataFrame(
        [
            {
                "sedol": "", "name": "CASH", "ticker": "", "asset_class": "Cash",
                "market_value": cash_mv, "excluded_reason": "",
                "is_loyalty_line": False, "parent_name": "CASH",
            }
        ]
    )  # fmt: skip
    df = pd.concat([df, cash], ignore_index=True)

    # Weights from market value, so small '<0.01%' holdings keep real weights and the total is 1
    included = df["excluded_reason"] == ""
    df["weight"] = 0.0
    df.loc[included, "weight"] = (
        df.loc[included, "market_value"] / df.loc[included, "market_value"].sum()
    )
    return df, as_of


# ---------------------------------------------------------------------------
# Security-ID resolution for Vanguard rows (section 2.2b, step 5)
# ---------------------------------------------------------------------------

COUNTRY_DEFAULTS = {  # main exchange suffix and trading currency, used when a name isn't in IEUR
    "United Kingdom": (".L", "GBP"), "Germany": (".DE", "EUR"), "France": (".PA", "EUR"),
    "Switzerland": (".SW", "CHF"), "Netherlands": (".AS", "EUR"), "Spain": (".MC", "EUR"),
    "Italy": (".MI", "EUR"), "Sweden": (".ST", "SEK"), "Denmark": (".CO", "DKK"),
    "Finland": (".HE", "EUR"), "Norway": (".OL", "NOK"), "Belgium": (".BR", "EUR"),
    "Austria": (".VI", "EUR"), "Ireland": (".IR", "EUR"), "Portugal": (".LS", "EUR"),
    "Poland": (".WA", "PLN"), "United States": ("", "USD"),
}  # fmt: skip


def _default(country: str, i: int) -> str | None:
    d = COUNTRY_DEFAULTS.get(country)
    return d[i] if d else None


def resolve_security_ids(
    vgk: pd.DataFrame, ieur: pd.DataFrame, sedol_overrides: dict[str, str]
) -> pd.DataFrame:
    """Give every VGK row a security_id and trading currency.

    vgk:  output of read_vanguard
    ieur: parsed IEUR equities with columns ticker, country, security_id, currency
    sedol_overrides: {"B71JBK1": "OR.PA", ...} from reference/symbol_overrides.csv
    """
    out = vgk.copy()
    out["ticker_norm"] = out["ticker"].map(lambda t: normalise_ticker(t) if t else "")

    # 1. Match to IEUR on ticker + country. Separators are ignored for the match because
    #    Vanguard writes Nordic share classes run together ("ESSITYB" vs iShares "ESSITY B").
    out["ticker_key"] = out["ticker_norm"].str.replace("-", "", regex=False)
    ref = (
        ieur.dropna(subset=["ticker", "security_id"])
        .assign(ticker_key=lambda x: x["ticker"].map(normalise_ticker).str.replace("-", ""))[
            ["ticker_key", "country", "security_id", "currency"]
        ]
        .drop_duplicates(["ticker_key", "country"])
    )
    ref = ref[ref["ticker_key"] != ""]
    out = out.merge(ref, on=["ticker_key", "country"], how="left")
    out["id_source"] = out["security_id"].notna().map({True: "ieur", False: ""})

    # 2. Manual overrides by SEDOL (win over everything else)
    ov = out["sedol"].map(sedol_overrides)
    out.loc[ov.notna(), "security_id"] = ov[ov.notna()]
    out.loc[ov.notna(), "id_source"] = "override"

    # 3. Country default exchange for tickered equities still unmatched
    miss = (
        out["security_id"].isna()
        & (out["ticker_norm"] != "")
        & (out["asset_class"] == "Equity")
        & (out["excluded_reason"] == "")
    )
    suffix = out.loc[miss, "country"].map(lambda c: _default(c, 0))
    idx = suffix[suffix.notna()].index
    out.loc[idx, "security_id"] = out.loc[idx, "ticker_norm"] + suffix[idx]
    out.loc[idx, "id_source"] = "country_default"

    # 4. Loyalty-share lines take their parent's security_id (exact name, then name prefix)
    parents = out[
        ~out["is_loyalty_line"].astype(bool)
        & out["security_id"].notna()
        & (out["asset_class"] == "Equity")
    ]
    by_name = dict(zip(parents["name"].str.upper(), parents["security_id"], strict=True))

    def parent_id(pname: str) -> str | None:
        key = pname.upper()
        if key in by_name:
            return str(by_name[key])
        hits = {sid for n, sid in by_name.items() if n.startswith(key + " ")}
        return str(hits.pop()) if len(hits) == 1 else None

    loy = out["is_loyalty_line"].astype(bool)
    out.loc[loy, "security_id"] = out.loc[loy, "parent_name"].map(parent_id)
    out.loc[loy & out["security_id"].notna(), "id_source"] = "loyalty_parent"

    # Currency: from IEUR where matched, else the country default
    out["currency"] = out["currency"].fillna(out["country"].map(lambda c: _default(c, 1)))

    # Cash line
    cash = out["asset_class"] == "Cash"
    out.loc[cash, ["security_id", "id_source", "currency"]] = ["CASH", "cash", "USD"]
    return out


def to_holdings(resolved: pd.DataFrame) -> pd.DataFrame:
    """Merge rows sharing a security_id (loyalty lines into parents, duplicate lines)."""
    return (
        resolved.dropna(subset=["security_id"])
        .groupby("security_id", as_index=False)
        .agg(
            weight=("weight", "sum"),
            market_value=("market_value", "sum"),
            name=("name", "first"),
            currency=("currency", "first"),
            country=("country", "first"),
            sub_industry=("sub_industry", "first"),
            ticker=("ticker", "first"),
        )
    )
