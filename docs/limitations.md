# Limitations

What this project deliberately does not do, and where its numbers are approximations. The
formulas themselves are in [methodology.md](methodology.md).

## Out of scope

- **Order generation or submission.** No tool can create, stage or submit an order. The
  what-if simulator only changes an in-memory copy of weights.
- **Optimisation**, compliance checks, liquidity or transaction-cost analysis.
- **A licensed factor model.** Risk comes from a shrunk sample covariance of ~1,250 stocks,
  so there is no factor vs stock-specific split of tracking error.

## Data

- **Holdings** come from public ETF files, not an OMS/IBOR. VGK's are monthly; iShares'
  are daily, so the two equity portfolios can have different as-of dates.
- **Synthetic PM portfolios** are seeded tilts of the benchmarks, not real funds.
- **Prices** come from Yahoo Finance, which has occasional bad prints (unadjusted splits,
  pence/pound flips on London quotes). One-day moves beyond +200%/−80% are treated as
  missing. Dead listings (e.g. Covestro, Evraz) can't be priced and are reported in the
  data-quality table.
- **Classification** is Yahoo-derived (with Vanguard's sub-industry as fallback), not GICS.
- **Bond durations** are the provider's effective durations as of the snapshot date.

## Risk estimates

- Short estimation window (504 trading days); securities with less than 90% return history
  are excluded from the covariance matrix. Every risk response reports the covered share of
  active weight and warns below 95%.
- Residual gaps are filled with zero returns, which slightly understates variance for thinly
  traded names.
- Daily closes across European exchanges are nearly, not exactly, synchronous.
- Ex-ante TE assumes today's weights are held and the historical covariance applies.
- Fixed income has no convexity, key-rate or spread duration, and no bond covariance: the
  risk tools cover equities only.

## The LLM

- The agent never computes numbers; every figure must appear in a tool output. The numeric
  grounding check is a heuristic: it accepts rounding and small integers (counts, list
  positions), so it can miss an invented figure that happens to match a tool number.
- Tool selection and argument choice are measured by the golden eval set, not guaranteed.
