"""Ledoit-Wolf / EWMA covariance of daily returns, annualised."""

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

PERIODS_PER_YEAR = 252


def ledoit_wolf_cov(r: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    lw = LedoitWolf().fit(r.to_numpy())
    cov = pd.DataFrame(lw.covariance_ * PERIODS_PER_YEAR, index=r.columns, columns=r.columns)
    return cov, float(lw.shrinkage_)


def ewma_cov(r: pd.DataFrame, halflife: int = 126) -> pd.DataFrame:
    lam = 0.5 ** (1 / halflife)
    x = r.to_numpy() - r.to_numpy().mean(axis=0)
    wts = lam ** np.arange(len(x))[::-1]
    wts /= wts.sum()
    c = (x * wts[:, None]).T @ x
    return pd.DataFrame(c * PERIODS_PER_YEAR, index=r.columns, columns=r.columns)
