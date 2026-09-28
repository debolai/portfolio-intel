"""Active exposures by any classification column."""

import pandas as pd


def weights_frame(
    w_p: pd.Series, w_b: pd.Series, attrs: pd.DataFrame, filters: dict[str, str] | None = None
) -> pd.DataFrame:
    """Security-level port/bmk/active weights (decimals) joined to attributes, filtered."""
    ids = w_p.index.union(w_b.index)
    df = pd.DataFrame(
        {"port": w_p.reindex(ids, fill_value=0.0), "bmk": w_b.reindex(ids, fill_value=0.0)}
    )
    df = df.join(attrs, how="left")
    for col, val in (filters or {}).items():
        if col not in df.columns:
            raise ValueError(f"unknown filter column {col!r}")
        df = df[df[col].fillna("").astype(str).str.casefold() == val.casefold()]
    df["active"] = df["port"] - df["bmk"]
    return df


def active_exposures(
    w_p: pd.Series,
    w_b: pd.Series,
    attrs: pd.DataFrame,
    dimension: str,
    filters: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Grouped port/bmk/active weights in percentage points, largest |active| first."""
    df = weights_frame(w_p, w_b, attrs, filters)
    df[dimension] = df[dimension].fillna("Unclassified")
    g = df.groupby(dimension)[["port", "bmk"]].sum()
    g["active"] = g["port"] - g["bmk"]
    return (g * 100).round(4).sort_values("active", key=abs, ascending=False)  # pct points
