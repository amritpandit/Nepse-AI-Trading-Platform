"""Causal daily technical-analysis features and an explainable rule score.

The score is a heuristic research measure, not a calibrated probability or a
promise of future return. Every indicator and SMC event is computed using only
the current bar and bars that precede it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

MIN_INDICATOR_HISTORY = 55
DISPLAY_BARS = 500


def _json_number(value: object, digits: int = 4) -> float | None:
    """Convert numeric values to JSON-safe finite floats."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(number, digits) if np.isfinite(number) else None


def _is_finite(value: object) -> bool:
    try:
        return bool(np.isfinite(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _compute_smc(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate confirmed-pivot structure, FVGs, sweeps and order-block zones.

    A pivot is published only on the bar where its right-hand confirmation
    window has completed. This avoids placing a historical pivot on a date
    when it was not yet knowable.
    """
    count = len(df)
    opens = df["open"].to_numpy(dtype=np.float64)
    highs = df["high"].to_numpy(dtype=np.float64)
    lows = df["low"].to_numpy(dtype=np.float64)
    closes = df["close"].to_numpy(dtype=np.float64)
    atr = df["atr_14"].to_numpy(dtype=np.float64)

    swing_left = 3
    swing_right = 3
    liquidity_lookback = 20
    order_block_lookback = 10

    structure_events: list[str | None] = [None] * count
    fvg_directions: list[str | None] = [None] * count
    sweep_directions: list[str | None] = [None] * count
    order_block_directions: list[str | None] = [None] * count
    bos_bullish = np.zeros(count, dtype=np.int8)
    bos_bearish = np.zeros(count, dtype=np.int8)
    choch_bullish = np.zeros(count, dtype=np.int8)
    choch_bearish = np.zeros(count, dtype=np.int8)
    fvg_bullish = np.zeros(count, dtype=np.int8)
    fvg_bearish = np.zeros(count, dtype=np.int8)
    fvg_size_pct = np.full(count, np.nan, dtype=np.float64)
    sweep_low = np.zeros(count, dtype=np.int8)
    sweep_high = np.zeros(count, dtype=np.int8)
    order_block_top = np.full(count, np.nan, dtype=np.float64)
    order_block_bottom = np.full(count, np.nan, dtype=np.float64)

    last_swing_high: float | None = None
    last_swing_low: float | None = None
    high_level_broken = False
    low_level_broken = False
    structure_trend = 0

    for index in range(count):
        pivot_index = index - swing_right
        if pivot_index >= swing_left:
            high_window = highs[pivot_index - swing_left : pivot_index + swing_right + 1]
            low_window = lows[pivot_index - swing_left : pivot_index + swing_right + 1]
            candidate_high = highs[pivot_index]
            candidate_low = lows[pivot_index]

            if (
                np.isfinite(high_window).all()
                and candidate_high == float(np.max(high_window))
                and np.count_nonzero(high_window == candidate_high) == 1
            ):
                last_swing_high = float(candidate_high)
                high_level_broken = False

            if (
                np.isfinite(low_window).all()
                and candidate_low == float(np.min(low_window))
                and np.count_nonzero(low_window == candidate_low) == 1
            ):
                last_swing_low = float(candidate_low)
                low_level_broken = False

        if index >= 1:
            previous_close = closes[index - 1]
            current_close = closes[index]

            if (
                last_swing_high is not None
                and not high_level_broken
                and previous_close <= last_swing_high < current_close
            ):
                event = "CHOCH_BULLISH" if structure_trend < 0 else "BOS_BULLISH"
                structure_events[index] = event
                if event == "CHOCH_BULLISH":
                    choch_bullish[index] = 1
                else:
                    bos_bullish[index] = 1
                structure_trend = 1
                high_level_broken = True

                candidates = [
                    j
                    for j in range(max(0, index - order_block_lookback), index)
                    if closes[j] < opens[j]
                ]
                if candidates:
                    block_index = candidates[-1]
                    order_block_directions[index] = "BULLISH"
                    order_block_top[index] = highs[block_index]
                    order_block_bottom[index] = lows[block_index]

            elif (
                last_swing_low is not None
                and not low_level_broken
                and previous_close >= last_swing_low > current_close
            ):
                event = "CHOCH_BEARISH" if structure_trend > 0 else "BOS_BEARISH"
                structure_events[index] = event
                if event == "CHOCH_BEARISH":
                    choch_bearish[index] = 1
                else:
                    bos_bearish[index] = 1
                structure_trend = -1
                low_level_broken = True

                candidates = [
                    j
                    for j in range(max(0, index - order_block_lookback), index)
                    if closes[j] > opens[j]
                ]
                if candidates:
                    block_index = candidates[-1]
                    order_block_directions[index] = "BEARISH"
                    order_block_top[index] = highs[block_index]
                    order_block_bottom[index] = lows[block_index]

        if index >= 2:
            bullish_gap = lows[index] - highs[index - 2]
            bearish_gap = lows[index - 2] - highs[index]
            atr_value = atr[index]
            minimum_gap = 0.05 * atr_value if np.isfinite(atr_value) else 0.0

            if bullish_gap > minimum_gap:
                fvg_directions[index] = "BULLISH"
                fvg_bullish[index] = 1
                fvg_size_pct[index] = bullish_gap / closes[index] * 100.0
            elif bearish_gap > minimum_gap:
                fvg_directions[index] = "BEARISH"
                fvg_bearish[index] = 1
                fvg_size_pct[index] = bearish_gap / closes[index] * 100.0

        if index >= 5:
            start = max(0, index - liquidity_lookback)
            prior_high = float(np.max(highs[start:index]))
            prior_low = float(np.min(lows[start:index]))

            if highs[index] > prior_high and closes[index] < prior_high:
                sweep_directions[index] = "HIGH"
                sweep_high[index] = 1
            elif lows[index] < prior_low and closes[index] > prior_low:
                sweep_directions[index] = "LOW"
                sweep_low[index] = 1

    return pd.DataFrame(
        {
            "smc_structure_event": structure_events,
            "smc_bos_bullish": bos_bullish,
            "smc_bos_bearish": bos_bearish,
            "smc_choch_bullish": choch_bullish,
            "smc_choch_bearish": choch_bearish,
            "smc_fvg_direction": fvg_directions,
            "smc_fvg_bullish": fvg_bullish,
            "smc_fvg_bearish": fvg_bearish,
            "smc_fvg_size_pct": fvg_size_pct,
            "smc_liquidity_sweep": sweep_directions,
            "smc_liquidity_sweep_low": sweep_low,
            "smc_liquidity_sweep_high": sweep_high,
            "smc_order_block_direction": order_block_directions,
            "smc_order_block_top": order_block_top,
            "smc_order_block_bottom": order_block_bottom,
        }
    )


def analyze_ohlcv(
    rows: Sequence[Mapping[str, Any]],
    symbol: str,
    display_limit: int = DISPLAY_BARS,
) -> dict[str, Any]:
    """Return OHLCV bars, causal indicators and confirmed SMC event history."""
    if not rows:
        raise ValueError("No historical OHLCV observations were supplied.")

    df = pd.DataFrame(list(rows))
    required = ["date", "open", "high", "low", "close", "volume"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"OHLCV data is missing required fields: {', '.join(missing)}")

    original_count = len(df)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    numeric_columns = ["open", "high", "low", "close", "volume"]
    valid = df["date"].notna() & np.isfinite(df[numeric_columns]).all(axis=1)
    valid &= df["open"].gt(0) & df["high"].gt(0) & df["low"].gt(0) & df["close"].gt(0)
    valid &= df["volume"].ge(0)
    valid &= df["high"].ge(df[["open", "close", "low"]].max(axis=1))
    valid &= df["low"].le(df[["open", "close", "high"]].min(axis=1))
    df = (
        df.loc[valid]
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    valid_count = len(df)

    if valid_count >= 2:
        close_changes = df["close"].pct_change().abs()
        discontinuity_positions = np.flatnonzero(close_changes.gt(0.15).to_numpy())
    else:
        discontinuity_positions = np.asarray([], dtype=np.int64)

    latest_discontinuity: dict[str, Any] = {
        "flagged": False,
        "event_date": None,
        "absolute_change_pct": None,
        "reason": None,
    }
    if discontinuity_positions.size:
        event_position = int(discontinuity_positions[-1])
        previous_close = float(df.iloc[event_position - 1]["close"])
        event_close = float(df.iloc[event_position]["close"])
        change_pct = abs(event_close / previous_close - 1.0) * 100.0
        latest_discontinuity = {
            "flagged": True,
            "event_date": pd.Timestamp(df.iloc[event_position]["date"]).date().isoformat(),
            "absolute_change_pct": round(change_pct, 2),
            "reason": "Possible corporate action or abnormal source-price discontinuity",
        }
        # As in the canonical research model, do not mix pre-event raw prices
        # into indicators until a new post-event price history is available.
        df = df.iloc[event_position:].reset_index(drop=True)

    excluded_invalid_or_duplicate_rows = original_count - valid_count
    excluded_pre_discontinuity_rows = valid_count - len(df)
    excluded_total_rows = original_count - len(df)

    if len(df) < 3:
        if latest_discontinuity["flagged"]:
            raise ValueError(
                "Fewer than three observations remain after the latest material price discontinuity."
            )
        raise ValueError("At least three valid daily OHLCV observations are required.")

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    df["ema_20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    df["ema_50"] = close.ewm(span=50, adjust=False, min_periods=50).mean()
    df["sma_20"] = close.rolling(window=20, min_periods=20).mean()
    df["sma_50"] = close.rolling(window=50, min_periods=50).mean()

    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)
    average_gain = gains.ewm(alpha=1.0 / 14.0, adjust=False, min_periods=14).mean()
    average_loss = losses.ewm(alpha=1.0 / 14.0, adjust=False, min_periods=14).mean()
    relative_strength = average_gain / average_loss.replace(0.0, np.nan)
    df["rsi_14"] = 100.0 - (100.0 / (1.0 + relative_strength))
    df.loc[(average_loss == 0.0) & (average_gain > 0.0), "rsi_14"] = 100.0
    df.loc[(average_loss == 0.0) & (average_gain == 0.0), "rsi_14"] = 50.0

    ema_fast = close.ewm(span=12, adjust=False, min_periods=26).mean()
    ema_slow = close.ewm(span=26, adjust=False, min_periods=26).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False, min_periods=9).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr_14"] = true_range.ewm(alpha=1.0 / 14.0, adjust=False, min_periods=14).mean()
    df["atr_pct"] = (df["atr_14"] / close) * 100.0

    bb_mid = close.rolling(window=20, min_periods=20).mean()
    bb_std = close.rolling(window=20, min_periods=20).std(ddof=0)
    df["bb_mid"] = bb_mid
    df["bb_upper"] = bb_mid + (2.0 * bb_std)
    df["bb_lower"] = bb_mid - (2.0 * bb_std)

    volume_sma = volume.rolling(window=20, min_periods=20).mean()
    df["volume_sma_20"] = volume_sma
    df["volume_ratio"] = volume.div(volume_sma.where(volume_sma > 0.0))
    df["obv"] = (np.sign(delta).fillna(0.0) * volume).cumsum()

    smc = _compute_smc(df)
    df = pd.concat([df, smc], axis=1)

    history_sufficient = len(df) >= MIN_INDICATOR_HISTORY
    latest = df.iloc[-1]

    bars: list[dict[str, Any]] = []
    recent_events: list[dict[str, Any]] = []
    view = df.tail(max(1, min(display_limit, DISPLAY_BARS)))
    for _, row in view.iterrows():
        bar = {
            "date": pd.Timestamp(row["date"]).date().isoformat(),
            "open": _json_number(row["open"]),
            "high": _json_number(row["high"]),
            "low": _json_number(row["low"]),
            "close": _json_number(row["close"]),
            "volume": _json_number(row["volume"], digits=0),
            "ema_20": _json_number(row["ema_20"]),
            "ema_50": _json_number(row["ema_50"]),
            "sma_50": _json_number(row["sma_50"]),
            "bb_upper": _json_number(row["bb_upper"]),
            "bb_mid": _json_number(row["bb_mid"]),
            "bb_lower": _json_number(row["bb_lower"]),
            "rsi_14": _json_number(row["rsi_14"]),
            "macd": _json_number(row["macd"]),
            "macd_signal": _json_number(row["macd_signal"]),
            "macd_hist": _json_number(row["macd_hist"]),
            "atr_14": _json_number(row["atr_14"]),
            "atr_pct": _json_number(row["atr_pct"]),
            "volume_ratio": _json_number(row["volume_ratio"]),
            "smc_structure_event": row["smc_structure_event"] or None,
            "smc_fvg_direction": row["smc_fvg_direction"] or None,
            "smc_liquidity_sweep": row["smc_liquidity_sweep"] or None,
            "smc_order_block_direction": row["smc_order_block_direction"] or None,
            "smc_order_block_top": _json_number(row["smc_order_block_top"]),
            "smc_order_block_bottom": _json_number(row["smc_order_block_bottom"]),
        }
        bars.append(bar)

        if any(
            bar[key] is not None
            for key in [
                "smc_structure_event",
                "smc_fvg_direction",
                "smc_liquidity_sweep",
                "smc_order_block_direction",
            ]
        ):
            recent_events.append(
                {
                    "date": bar["date"],
                    "structure": bar["smc_structure_event"],
                    "fvg": bar["smc_fvg_direction"],
                    "liquidity_sweep": bar["smc_liquidity_sweep"],
                    "order_block": bar["smc_order_block_direction"],
                    "order_block_top": bar["smc_order_block_top"],
                    "order_block_bottom": bar["smc_order_block_bottom"],
                }
            )

    indicators = {
        name: _json_number(latest[name])
        for name in [
            "rsi_14",
            "macd",
            "macd_signal",
            "macd_hist",
            "ema_20",
            "ema_50",
            "sma_20",
            "sma_50",
            "bb_upper",
            "bb_mid",
            "bb_lower",
            "atr_14",
            "atr_pct",
            "volume_ratio",
            "obv",
        ]
    }

    return {
        "symbol": symbol.upper(),
        "as_of": pd.Timestamp(latest["date"]).date().isoformat(),
        "observation_count": len(df),
        "excluded_or_duplicate_rows": excluded_invalid_or_duplicate_rows,
        "excluded_pre_discontinuity_rows": excluded_pre_discontinuity_rows,
        "excluded_total_rows": excluded_total_rows,
        "price_discontinuity": latest_discontinuity,
        "indicator_history_sufficient": history_sufficient,
        "data_quality": {
            "source_prices_corporate_action_adjusted": False,
            "post_discontinuity_history_only": True,
            "warning": (
                "Raw price series may contain corporate actions or source errors; "
                "observations before the latest >15% close discontinuity are excluded."
                if latest_discontinuity["flagged"]
                else "No >15% close discontinuity was detected in the supplied history."
            ),
        },
        "minimum_indicator_history": MIN_INDICATOR_HISTORY,
        "explanation": [
            "Indicators are calculated from daily OHLCV bars; this module does not generate a standalone BUY/SELL decision.",
            "Use the platform composite research model for directional scores and trigger gates.",
        ],
        "indicators": indicators,
        "recent_smc_events": recent_events[-30:],
        "bars": bars,
        "methodology": {
            "version": "technical_smc_events_v1",
            "minimum_indicator_history": MIN_INDICATOR_HISTORY,
            "discontinuity_threshold_pct": 15.0,
            "pre_event_raw_price_policy": "exclude all observations before the latest detected >15% close change",
            "smc_pivots": "3-left/3-right; events are dated only after right-side confirmation",
            "lookahead_policy": "indicators and events at each bar use only data available up to that bar",
        },
    }
