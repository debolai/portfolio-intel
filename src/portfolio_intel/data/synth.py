"""Seeded synthetic PM portfolios: a documented, reproducible tilt of a benchmark."""

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


def load_tilts(path: Path) -> dict[str, Any]:
    cfg: dict[str, Any] = yaml.safe_load(path.read_text())
    return cfg


def make_tilted(
    bmk: pd.DataFrame, cfg: dict[str, Any], tilt_column: str = "industry"
) -> pd.DataFrame:
    """bmk: security_id, weight, and `tilt_column` (plus `duration` for long-duration tilts).

    Drops the smallest names, multiplies weights by the tilt for their group and by lognormal
    noise, rescales to (1 - cash_weight) and adds a CASH line. Same seed and file, same result.
    """
    rng = np.random.default_rng(cfg["seed"])
    df = bmk[bmk["security_id"] != "CASH"].sort_values(["weight", "security_id"]).copy()
    df = df.iloc[int(len(df) * cfg["drop_smallest_pct"]) :]
    mult = df[tilt_column].map(cfg["industry_tilts"]).fillna(1.0)
    if "long_duration_threshold" in cfg:
        long = df["duration"].fillna(0.0) > cfg["long_duration_threshold"]
        mult = mult.where(~long, mult * cfg["long_duration_tilt"])
    noise = rng.lognormal(0.0, cfg["idiosyncratic_noise"], len(df))
    w = df["weight"] * mult * noise
    df["weight"] = w / w.sum() * (1 - cfg["cash_weight"])
    cash = pd.DataFrame([{"security_id": "CASH", "weight": cfg["cash_weight"]}])
    return pd.concat([df[["security_id", "weight"]], cash], ignore_index=True)
