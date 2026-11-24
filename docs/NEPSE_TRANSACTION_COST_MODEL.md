# NEPSE equity transaction-cost model

## Scope and disclosure

The scalar backtest engine uses `nepse_equity_costs_v1` by default when no explicit legacy `commission_rate` is supplied. Each executed BUY and SELL records the broker commission, SEBON transaction fee, DP/depository charge, total transaction cost and fee-schedule version.

The result basis is **net of modeled broker commission, SEBON fee, DP transfer charge and configured slippage, before capital-gains tax**. Capital-gains tax is not modeled: this engine does not yet maintain tax-lot/WACC, residency/entity classification or every corporate-action adjustment required for reliable tax computation. The current 2026 personal-investor CGT rates must not be applied indiscriminately to companies, foreign investors or other taxpayer classes.

## Effective-dated commission schedules

The rate for a trade applies to the full transaction notional, not only to the marginal amount in that band.

| Effective from | Up to NPR 50,000 | 50,001–500,000 | 500,001–2m | 2m–10m | Above 10m |
|---|---:|---:|---:|---:|---:|
| 25 Apr 2011 | 1.00% | 0.90% | 0.80% (to 1m) / 0.70% above 1m | 0.70% | 0.70% |
| 21 Jul 2016 | 0.60% | 0.55% | 0.50% | 0.45% | 0.40% |
| 27 Dec 2020 | 0.40% | 0.37% | 0.34% | 0.30% | 0.27% |
| 14 May 2024 | 0.36% | 0.33% | 0.31% | 0.27% | 0.24% |

The current schedule has a minimum commission of NPR 10 per executed transaction. The implementation does not infer a minimum fee for older schedules where the cited historic rate material does not establish one.

Sources:
- 2011 brokerage bands: https://www.sharesansar.com/faq
- 2016 reduction and effective date: https://kathmandupost.com/money/2016/07/21/brokers-commission-slashed-by-40-percent
- 2020 bands and effective date: https://www.sharesansar.com/newsdetail/board-decides-to-decrease-the-commission-rate-2020-12-25
- 2024 final schedule and implementation date: https://www.sharesansar.com/index.php/newsdetail/sebon-to-enact-revised-commission-rates-for-stockbrokers-implementation-begins-today-2024-05-14
- Current commission/minimum and fee confirmation: https://shubhakamanasecurities.com/en/pricing

## Other modeled charges

- SEBON share transaction fee: 0.015% of each executed buy or sell notional. SEBON Schedule 15 states the share transaction fee.
- Depository transfer: from 4 Apr 2013, NPR 25 per scrip transfer. Before that date, the model uses the published prior convention of NPR 20 or 0.25% of transferred value, whichever is higher.
- The backtest executes at most one aggregated fill per symbol per session. It therefore allocates one DP charge to each executed symbol/session fill. A partial exit on a later session is a separate transfer day and receives a separate DP charge.
- Slippage remains configured separately in basis points and is applied to execution price before notional-based fees are computed.

Sources:
- SEBON Schedule 15: https://www.sebon.gov.np/uploads/uploads/UUqlgCkhxAnGbqkZMirCcGEdtFnx4KBmNeQqdicw.pdf
- CDSC DP transfer fee explanation: https://cdsc.com.np/downloads_files/2018_09_11_07_36_35_CNS%20FAQ.pdf
- CDSC 2013 transition to a flat NPR 25 demat transfer charge: https://www.sharesansar.com/newsdetail/cdsc-to-be-fully-operational

## Legacy reproducibility

Passing an explicit `commission_rate` to `BacktestEngine` continues to use the legacy flat-commission-only mode. That mode intentionally does not add SEBON or DP charges, so older experiments can be reproduced. Each result records its fee model version and return basis. New backtests use the dated NEPSE fee schedule by default.

The dated model rejects trade dates before 25 Apr 2011 because this implementation does not yet have a verified rate series for earlier dates. This is deliberate: it must not silently invent a fee rate.

## Known limitations

- Prices in the current public history source are raw and not corporate-action adjusted. Data-quality and corporate-action controls remain necessary.
- CGT is excluded until FIFO/WACC tax lots, holding-period determination, taxpayer type and effective-dated tax rules are modeled.
- Broker contract notes remain the final reference for a real transaction. This is a research backtest cost model, not a settlement engine.
- The vectorized engine remains a separate simplified approximation and is not represented as using this scalar engine's full effective-dated fee model.
