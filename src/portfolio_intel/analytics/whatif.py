"""Trade simulator: changes an in-memory copy of weights. Never touches stored holdings."""

from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field


class Trade(BaseModel):
    security_id: str
    delta_weight_bps: float = Field(
        ge=-500,
        le=500,
        description="Change in portfolio weight in basis points of NAV. -50 = trim 0.50% of NAV.",
    )


class WhatIfError(ValueError): ...


def apply_trades(
    w: pd.Series, trades: list[Trade], funding: Literal["cash", "pro_rata"] = "cash"
) -> pd.Series:
    w = w.copy()
    traded = []
    for t in trades:
        d = t.delta_weight_bps / 10_000
        current = float(w.get(t.security_id, 0.0))
        if current + d < -1e-12:
            raise WhatIfError(
                f"{t.security_id}: position is {current * 1e4:.1f}bps; "
                f"cannot trim {-t.delta_weight_bps:.1f}bps (long-only)."
            )
        w[t.security_id] = current + d
        traded.append(t.security_id)
    net = sum(t.delta_weight_bps for t in trades) / 10_000
    if funding == "cash":
        w["CASH"] = float(w.get("CASH", 0.0)) - net
        if w["CASH"] < -1e-12:
            raise WhatIfError("Insufficient cash to fund the buys; use funding='pro_rata'.")
    else:
        others = w.index.difference([*traded, "CASH"])
        base = float(w[others].sum())
        if base <= 0:
            raise WhatIfError("No other positions to fund the trades pro rata.")
        w[others] = w[others] - net * w[others] / base
    if abs(w.sum() - 1.0) > 1e-9:
        raise WhatIfError("Weights no longer sum to 100%.")
    return w[w.abs() > 1e-12]
