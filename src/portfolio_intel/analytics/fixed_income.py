"""Duration, active duration and DV01 for the bond sleeve."""

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class DurationStats:
    duration_port: float  # years
    duration_bmk: float
    active_duration: float
    dv01_port: float  # currency units per 1bp for the given NAV
    dv01_bmk: float
    contrib: pd.Series  # a_i * D_i, years; sums to active_duration


def duration_stats(
    w_p: pd.Series, w_b: pd.Series, duration: pd.Series, nav: float
) -> DurationStats:
    """D_p = sum w_i D_i (cash has zero duration). DV01 = D x 0.0001 x NAV."""
    ids = w_p.index.union(w_b.index)
    d = duration.reindex(ids).fillna(0.0)
    p = w_p.reindex(ids, fill_value=0.0)
    b = w_b.reindex(ids, fill_value=0.0)
    dp, db = float((p * d).sum()), float((b * d).sum())
    return DurationStats(
        duration_port=dp,
        duration_bmk=db,
        active_duration=dp - db,
        dv01_port=dp * 1e-4 * nav,
        dv01_bmk=db * 1e-4 * nav,
        contrib=(p - b) * d,
    )
