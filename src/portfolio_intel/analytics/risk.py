"""Ex-ante tracking error, Euler contributions, total risk and beta."""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ActiveRisk:
    te: float  # annualised, decimal
    contrib: pd.Series  # CTE_i, decimal, sums to te
    mcte: pd.Series
    active: pd.Series
    covered_active_abs: float  # share of sum|a| inside the covariance universe


def active_risk(w_p: pd.Series, w_b: pd.Series, cov: pd.DataFrame) -> ActiveRisk:
    ids = w_p.index.union(w_b.index)
    a_all = w_p.reindex(ids, fill_value=0.0) - w_b.reindex(ids, fill_value=0.0)
    a_cov = a_all.drop("CASH", errors="ignore")  # zero variance, no contribution
    a = a_cov.reindex(cov.index, fill_value=0.0)
    s = cov.to_numpy()
    sa = s @ a.to_numpy()
    te = float(np.sqrt(max(float(a.to_numpy() @ sa), 0.0)))
    denom = float(a_cov.abs().sum())
    covered = float(a.abs().sum() / denom) if denom else 1.0
    if te == 0.0:
        zero = pd.Series(0.0, index=cov.index)
        return ActiveRisk(0.0, zero, zero, a_all, covered)
    mcte = pd.Series(sa / te, index=cov.index)
    contrib = a * mcte
    return ActiveRisk(te, contrib, mcte, a_all, covered)


def total_risk_and_beta(w_p: pd.Series, w_b: pd.Series, cov: pd.DataFrame) -> dict[str, float]:
    p = w_p.reindex(cov.index, fill_value=0.0).to_numpy()
    b = w_b.reindex(cov.index, fill_value=0.0).to_numpy()
    s = cov.to_numpy()
    var_b = float(b @ s @ b)
    return {
        "vol_port": float(np.sqrt(p @ s @ p)),
        "vol_bmk": float(np.sqrt(var_b)),
        "beta": float(p @ s @ b / var_b) if var_b else float("nan"),
    }


def group_contributions(contrib: pd.Series, groups: pd.Series) -> pd.Series:
    """Sum member contributions by group (sector, industry, country...)."""
    return contrib.groupby(groups.reindex(contrib.index).fillna("Unclassified")).sum()
