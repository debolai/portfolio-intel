# Portfolio Intelligence — Methodology (v1.0.0)

Every number returned by the tools is computed deterministically from one data snapshot.
Each response carries `as_of`, `data_snapshot_id`, `methodology_version` and a `calc_id`
(`sha256(tool + canonical_json(args) + snapshot_id + methodology_version)[:16]`), so the same
question on the same snapshot always gives the same numbers.

## Data

| Portfolio | Source | Notes |
|---|---|---|
| `EQ_EU_BMK` | iShares Core MSCI Europe ETF (IEUR) holdings file | Equity benchmark |
| `EQ_EU_PM` | Seeded synthetic tilt of IEUR (`reference/tilts_eq_eu.yaml`) | ~200 names, 2% cash |
| `EQ_EU_VGK` | Vanguard FTSE Developed Europe (VGK) holdings file | Real second fund; benchmark `EQ_EU_BMK` |
| `FI_US_BMK` | iShares Core US Aggregate Bond ETF (AGG) holdings file | Bond benchmark |
| `FI_US_PM` | Seeded synthetic tilt of AGG (`reference/tilts_fi_us.yaml`) | Overweight credit and long duration |

- **Weights** are computed from market value (`market value / total market value` of the
  included lines), not from the printed `Weight (%)` column, which is rounded to 0.01% and
  loses most of AGG's ~13,000 small bond lines (they print as 0.00%).
- **Cash and money-market lines** are summed into one `CASH` line (zero volatility, zero
  duration). **Collateral, FX forwards and futures** are excluded; their weight is reported
  in the data-quality table. Rights, warrants and CVRs are excluded.
- **Security IDs**: equities use the Yahoo Finance symbol (ticker + exchange suffix, e.g.
  `SAN.MC` vs `SAN.PA`); bonds use the ISIN (falling back to CUSIP). VGK rows are matched to
  IEUR on ticker + country (separators ignored, because Vanguard writes `ESSITYB` where
  iShares writes `ESSITY B`), then SEDOL overrides, then the country's main exchange.
  French loyalty-share (*prime de fidélité*) lines are merged into the parent company.
- **Holdings dates differ by fund**: iShares publishes daily; Vanguard's public holdings are
  monthly. Each holding row keeps its own `as_of`.
- **Prices** are dividend- and split-adjusted daily closes from Yahoo Finance
  (`auto_adjust=True`), so returns approximate total returns. **FX** is Yahoo `CCYUSD=X`.

## Classification

The industry taxonomy is **Yahoo-derived, not GICS** (GICS industry data is licensed).
Each equity's industry comes from, in priority order:

1. a manual override in `reference/industry_overrides.csv`,
2. Yahoo Finance's `industry` field, mapped by `reference/industry_map.csv`,
3. Vanguard's GICS-style sub-industry from the VGK holdings file, mapped by the same file.

Both sources map onto one taxonomy (`industry`, `industry_group`), so "Banks" means the same
thing in every portfolio. `sector` for iShares names is the provider's GICS sector; for
VGK-only names it is inferred from their sub-industry using names held by both funds.
`region` is derived from the company's country of domicile.

## Returns

Daily simple returns in the base currency (USD):

    r_base = (1 + r_local)(1 + r_fx) − 1

where `r_fx` is the return of USD per unit of the security's trading currency.

- Prices are forward-filled for at most 3 days to bridge local holidays.
- A one-day local return above +200% or below −80% is treated as a **data error** (an
  unadjusted split, or a London quote flipping between pence and pounds) and set to missing.
- The estimation window is the last 504 trading days (~2 years). A security enters the
  covariance matrix only if it has returns on at least 90% of those days (`coverage`).
  Remaining gaps are filled with 0, which slightly understates variance for thinly traded
  names. European closes are nearly synchronous (most at 17:30 CET); a global universe would
  need weekly returns.

## Covariance

`Σ` is the Ledoit-Wolf shrinkage estimator of the daily return covariance, annualised ×252.
With ~1,250 securities and 504 days the sample covariance is singular; shrinkage toward a
scaled identity keeps it positive definite and reduces estimation error. The shrinkage
intensity is reported in every risk response. An EWMA estimator (half-life 126 days) is
available as an alternative.

## Active exposures

With `w_p`, `w_b` the portfolio and benchmark weights over the union of securities (missing
= 0), active weight is `a = w_p − w_b`, which sums to 0. Group exposures are sums of member
weights. All weights are reported in **percentage points of NAV**.

## Tracking error and its decomposition (ex-ante)

    TE        = sqrt(aᵀ Σ a)                  (cash excluded: zero variance)
    MCTE_i    = (Σ a)_i / TE
    CTE_i     = a_i · MCTE_i

By Euler's theorem (TE is homogeneous of degree one in `a`), `Σ_i CTE_i = TE` exactly, so
`pct_of_te = CTE_i / TE` sums to 100%. Group contributions are sums of member contributions.
A **negative contribution** means the position hedges the rest of the active book.

`risk_coverage_pct` is the share of `Σ|a|` (excluding cash) inside the covariance universe.
Below 95% the response warns that TE is likely understated.

Total risk is `sqrt(wᵀ Σ w)`; beta is `w_pᵀ Σ w_b / w_bᵀ Σ w_b`.

**Ex-ante vs realised TE.** Ex-ante TE uses current weights and estimated covariance.
Realised (ex-post) TE is the annualised standard deviation of the daily return difference
between two funds' ETF prices (√252). It is only available for real funds (`EQ_EU_VGK` vs
`EQ_EU_BMK`). The two differ because weights change over time, because VGK and IEUR track
different indices (FTSE vs MSCI: e.g. Poland), and because covariance is estimated.

## Fixed income

- The holdings file's `Duration` column is iShares' **effective duration** (years); it also
  publishes `Mod. Duration`, which is kept but not used.
- Portfolio duration `D_p = Σ w_i D_i` (cash has zero duration); active duration
  `D_p − D_b`; contribution to active duration `a_i D_i`, summed by sector.
- DV01 = `D × 0.0001 × NAV`. NAV is a notional parameter (default $100mm), not a real fund size.
- Limitations: first-order, parallel-shift sensitivity only. No convexity, key-rate or spread
  duration. Durations are the provider's, as of the snapshot date.

## What-if simulation

`delta_weight_bps` is a change in portfolio weight in basis points of NAV: "trim X by 50bps"
means `delta_weight_bps = −50` (reduce the weight by 0.50% of NAV), not "sell half".
Funding is either from cash or pro rata across all other holdings. Trades are bounded to
±500bps, 20 trades per simulation, long-only. The simulator changes an in-memory copy of
the weights and recomputes every metric with the cached covariance. Results are labelled
`hypothetical: true`. No order is ever created; no tool can create one.

## Synthetic portfolios

Generated deterministically from a seed and a documented tilt file: drop the smallest names,
multiply weights by an industry (or bond-sector) tilt and by lognormal noise, rescale to
`1 − cash_weight`, add a cash line. Same seed and file → identical portfolio. Two broad ETFs
tracking similar indices differ by well under 1% TE, which makes a decomposition uninformative;
the tilt produces an ex-ante TE of roughly 2–4%.
