# NEPSE stock research score: rules and validation standard

Snapshot: 25 November 2026

This document describes the existing `sector-stock-score-v1` research model. It is a
transparent, rule-based research aid for listed ordinary shares. It is not a
machine-learned probability model, broker instruction, or claim of guaranteed
profitability.

## 1. Evidence score (100 points)

The engine produces independent BUY and SELL evidence points. Each component
can contribute to one side, the other side, or neither. The scores are
not probabilities and are not forced to sum to 100.

| Component | Weight | What it examines |
| --- | ---: | --- |
| NEPSE market regime | 10 | Index trend regime from recent market history |
| Sector relative strength | 10 | 20/60/120-session median sector returns versus NEPSE |
| Trend structure | 15 | Close vs SMA20/SMA50/SMA200, with ADX strength confirmation |
| Medium-term momentum | 10 | 60/120/200-session stock returns |
| RSI / stochastic momentum | 10 | RSI14 plus stochastic %K/%D confirmation |
| MACD | 10 | MACD versus signal line and histogram direction |
| Volume confirmation | 10 | Relative volume, daily direction and OBV trend |
| Price action | 15 | 20-session breakout/breakdown and selected reversal candles |
| SMC price-action proxies | 10 | Daily OHLC-derived structure, FVG, liquidity sweep and order-block proxies |
| **Total component weight** | **100** | |

The chart also displays EMA/SMA overlays, Bollinger Bands, ATR and other
diagnostics. Not every plotted indicator independently contributes a score.

## 2. Trigger and safety rules

A current BUY trigger requires all of the following:

- BUY evidence at least 65/100 and at least 25 points higher than SELL evidence.
- NEPSE market-regime history available; market regime not bearish.
- Sector history available and sector relative strength versus NEPSE non-negative.
- At least 50 clean stock sessions and 200 sessions for the medium-term
  filter.
- No recent >15% raw-price discontinuity unless at least 200 clean
  post-discontinuity sessions exist.
- Latest usable completed price session no more than seven calendar days old.

A SELL trigger is a reduce/exit review for an existing long holding, never a
short-sale instruction. It requires SELL evidence at least 65/100, at least a
25-point SELL margin, bearish price/volume confirmation, at least 200 clean
sessions and fresh data. WATCH is shown when a directional score reaches 45
but trigger requirements are not satisfied. Otherwise the model returns HOLD
or STALE_DATA.

For a qualifying trigger, the existing service calculates an illustrative
ATR-based reference stop at two ATR and target at four ATR. These are research
levels, not live orders or a substitute for position sizing, portfolio limits
or investor suitability.

## 3. How to interpret SMC and price action

SMC events in this platform are inferred from daily OHLC bars: confirmed swing
structure, break of structure / change of character, fair-value-gap proxies,
liquidity-sweep proxies and order-block zones. They are not actual order-book,
broker, exchange participant, or institutional-flow observations. Daily OHLC
cannot identify who placed an order or prove institutional intent. SMC carries
a smaller weight because of this limitation.

Raw market prices are not corporate-action adjusted. Large discontinuities
are flagged, and the technical-analysis page displays the number of older bars
excluded. Do not treat an unadjusted historical price jump as a verified
economic return.

## 4. What is and is not validated

The code includes a chronological 70/30 holdout evaluation for the full
score-model rule set, with per-date feature history, next-session-open entries
and fixed 5/10/20-session outcome horizons. The separate 20-session breakout
event study is not a validation of the full composite scoring model.

As of this snapshot, the Neon database has no persisted rows in `backtests`,
`models` or `trades`. No portfolio-wide historical validation,
risk-adjusted performance or live/paper-trading result has been established
from those tables. The raw price source is not corporate-action adjusted, 45
listed ordinary-share symbols are not yet present in Neon, and the index
history is one session behind stock prices.

Therefore the score weights and thresholds should be regarded as a
pre-specified research hypothesis, not a globally proven NEPSE strategy. Do
not market the model as a verified profitable algorithm or translate the
points to probabilities.

## 5. Required validation before production reliance

Before treating BUY/SELL triggers as production-grade, complete the following
against a point-in-time, quality-controlled universe:

1. Reconcile listing/delisting and instrument histories to control survivorship
   and symbol-change bias.
2. Obtain or reconstruct corporate-action-adjusted OHLCV and document source
   lineage. Never silently repair impossible OHLC rows.
3. Rebuild market and sector features as of each historical signal date. Check
   that no future benchmark, sector or revised source data leaks into the
   signal.
4. Run expanding-window or rolling walk-forward tests, plus a final untouched
   holdout. Freeze score weights before that final evaluation.
5. Include actual Nepal broker/exchange fees, taxes, commissions, slippage,
   liquidity/turnover limits, price limits, suspensions, partial fills and
   executable next-session prices.
6. Report number of trades, win rate, average/median net return, expectancy,
   profit factor, max drawdown, volatility, Sharpe/Sortino, turnover and
   exposure, plus results by sector, market regime and liquidity bucket.
7. Compare with simple baselines (NEPSE buy-and-hold, equal-weight eligible
   universe and simple trend/momentum rules). Test sensitivity to thresholds,
   holding periods, costs and data gaps.
8. Paper-trade with timestamped signals and no look-ahead for a meaningful
   period before any broker execution integration is considered.

No score should be called validated solely because it has plausible indicator
rules or a high historical fit on the same data used to design it.
