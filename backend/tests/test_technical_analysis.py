from __future__ import annotations

from datetime import date, timedelta

from app.services.technical_analysis import analyze_ohlcv


def make_bars(count: int) -> list[dict[str, object]]:
    start = date(2026, 1, 1)
    rows: list[dict[str, object]] = []
    for index in range(count):
        close = 100.0 + (index * 0.45) + (1.2 * ((index % 9) / 9.0))
        open_price = close - 0.2
        rows.append(
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "open": open_price,
                "high": close + 0.8,
                "low": open_price - 0.8,
                "close": close,
                "volume": 1000 + (index % 7) * 120,
            }
        )
    return rows


def test_analysis_returns_indicators_score_and_signal() -> None:
    result = analyze_ohlcv(make_bars(90), "nabil")

    assert result["symbol"] == "NABIL"
    assert result["observation_count"] == 90
    assert result["signal_eligible"] is True
    assert 0.0 <= result["technical_score"] <= 100.0
    assert result["signal"] in {"BUY", "SELL", "HOLD"}
    assert result["indicators"]["rsi_14"] is not None
    assert result["indicators"]["macd"] is not None
    assert result["indicators"]["ema_20"] is not None
    assert result["indicators"]["bb_upper"] is not None
    assert result["methodology"]["score_is_probability"] is False


def test_insufficient_history_never_produces_buy_or_sell() -> None:
    result = analyze_ohlcv(make_bars(40), "NABIL")

    assert result["signal_eligible"] is False
    assert result["signal"] == "HOLD"
    assert any("at least" in item for item in result["explanation"])


def test_earlier_bars_are_unchanged_when_future_bars_are_appended() -> None:
    rows = make_bars(100)
    initial = analyze_ohlcv(rows[:70], "NABIL")
    extended = analyze_ohlcv(rows, "NABIL")

    for index in range(60):
        assert initial["bars"][index] == extended["bars"][index]


def test_smc_marks_bullish_fair_value_gap_and_liquidity_sweep() -> None:
    rows = make_bars(30)
    rows[0].update({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5})
    rows[1].update({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.4})
    rows[2].update({"open": 104.0, "high": 105.0, "low": 103.0, "close": 104.5})
    rows[8].update({"open": 103.0, "high": 110.0, "low": 103.0, "close": 104.0})

    result = analyze_ohlcv(rows, "NABIL")
    by_date = {bar["date"]: bar for bar in result["bars"]}

    assert by_date[rows[2]["date"]]["smc_fvg_direction"] == "BULLISH"
    assert by_date[rows[8]["date"]]["smc_liquidity_sweep"] == "HIGH"


def test_invalid_and_duplicate_rows_are_excluded() -> None:
    rows = make_bars(60)
    rows.append(dict(rows[-1]))
    rows.append(
        {
            "date": "2026-03-31",
            "open": 100.0,
            "high": 90.0,
            "low": 110.0,
            "close": 105.0,
            "volume": 1000,
        }
    )

    result = analyze_ohlcv(rows, "NABIL")

    assert result["observation_count"] == 60
    assert result["excluded_or_duplicate_rows"] == 2
