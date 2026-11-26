from __future__ import annotations

import json

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, cast
from unittest.mock import patch

import httpx
import numpy as np
import sqlalchemy as sa
from app.models.benchmark import NEPSEIndex
from app.models.price import Price
from app.models.signal import Signal
from app.models.stock import Stock
from app.api.routes import research as research_routes
from app.services.nepal_market_data import (
    INDEX_PATH,
    REFERENCE_PATH,
    NepalMarketDataImporter,
)
from app.services.stock_sector_research import (
    StockSectorResearchService,
    _chart_series,
    _indicator_snapshot,
    _historical_score_validation,
    _rsi,
    _score_signal,
    _smc_snapshot,
    _wilder,
)
from sqlalchemy.orm import Session


def _mock_client() -> httpx.Client:
    reference = (
        "symbol,name,source_category,instrument_type,sector,status\n"
        "NABIL,Nabil Bank Limited,Commercial Bank,ordinary,Commercial Bank,listed\n"
        "NABILP,Nabil Promoter Share,Promoter Share,promoter_share,,listed\n"
        "OLDCO,Old Company,Merged,ordinary,,merged\n"
    )
    nabil = (
        "published_date,open,high,low,close,per_change,traded_quantity,traded_amount,status\n"
        "2024-01-01,500,510,495,505,1,1000,505000,A\n"
        "2024-01-02,505,515,500,512,1.39,1200,614400,A\n"
    )
    index = (
        "published_date,open,high,low,close,per_change,traded_quantity,traded_amount,status\n"
        "2024-01-01,2000,2010,1990,2005,0.25,,0,A\n"
        "2024-01-02,2005,2020,2000,2015,0.50,,0,A\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(REFERENCE_PATH):
            return httpx.Response(200, text=reference)
        if request.url.path.endswith(INDEX_PATH):
            return httpx.Response(200, text=index)
        if request.url.path.endswith("/data/nepse/NABIL.csv"):
            return httpx.Response(200, text=nabil)
        return httpx.Response(404, text="not found")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_catalogue_excludes_nonordinary_and_merged() -> None:
    client = _mock_client()
    importer = NepalMarketDataImporter(client=client)
    try:
        rows = importer.list_tradable_equities()
    finally:
        client.close()
    assert [row["symbol"] for row in rows] == ["NABIL"]


def test_imports_ohlcv_and_metadata_idempotently(db_session: Session) -> None:
    client = _mock_client()
    importer = NepalMarketDataImporter(session=db_session, client=client)
    try:
        first = importer.sync_equities(batch_size=1)
        second = importer.sync_equities(batch_size=1)
    finally:
        client.close()

    assert first["symbols"] == ["NABIL"]
    assert first["rows_inserted"] == 2
    assert second["rows_inserted"] == 0
    assert second["rows_updated"] == 2

    stock = db_session.scalar(sa.select(Stock).where(Stock.symbol == "NABIL"))
    assert stock is not None
    assert stock.instrument_type == "ordinary"
    assert stock.sector == "Commercial Bank"
    assert db_session.scalar(sa.select(sa.func.count()).select_from(Price)) == 2


def test_import_dry_run_writes_nothing(db_session: Session) -> None:
    client = _mock_client()
    importer = NepalMarketDataImporter(session=db_session, client=client)
    try:
        result = importer.sync_equities(batch_size=1, dry_run=True)
    finally:
        client.close()

    assert result["dry_run"] is True
    assert result["rows_inserted"] == 0
    assert db_session.scalar(sa.select(sa.func.count()).select_from(Stock)) == 0
    assert db_session.scalar(sa.select(sa.func.count()).select_from(Price)) == 0


def test_imports_nepse_index_as_benchmark_only(db_session: Session) -> None:
    client = _mock_client()
    importer = NepalMarketDataImporter(session=db_session, client=client)
    try:
        result = importer.sync_nepse_index()
    finally:
        client.close()

    assert result["series"] == "NEPSE_INDEX"
    assert result["role"] == "benchmark_only"
    assert result["inserted"] == 2
    assert db_session.scalar(sa.select(sa.func.count()).select_from(NEPSEIndex)) == 2


def test_stock_analysis_only_emits_stock_trigger_and_sector_context(db_session: Session) -> None:
    stock = Stock(
        symbol="TEST",
        name="Test Commercial Bank",
        sector="Commercial Bank",
        instrument_type="ordinary",
        is_active=True,
    )
    db_session.add(stock)
    db_session.flush()
    start = date(2024, 1, 1)
    index_level = 2000.0
    for i in range(230):
        close = 100.0 + i * 0.2
        day = start + timedelta(days=i)
        db_session.add(
            Price(
                stock_id=stock.id,
                date=day,
                open=close - 0.1,
                high=close + 0.2,
                low=close - 0.2,
                close=close,
                volume=1000,
            )
        )
        index_level += 1.0
        db_session.add(
            NEPSEIndex(
                date=day,
                open=index_level - 1,
                high=index_level + 1,
                low=index_level - 2,
                close=index_level,
                volume=100000,
            )
        )
    db_session.commit()

    result = StockSectorResearchService(db_session).analyze_stock("TEST")
    assert result["symbol"] == "TEST"
    assert result["market_context"]["signal_role"] == "benchmark_only_no_buy_or_sell_orders"
    assert result["sector_context"]["sector"] == "Commercial Bank"
    assert "breakout event study" in result["historical_event_study"]["method"]
    assert result["data_quality"]["source_prices_corporate_action_adjusted"] is False


def test_promoter_share_is_not_eligible_for_stock_triggers(db_session: Session) -> None:
    db_session.add(
        Stock(symbol="TESTP", instrument_type="promoter_share", is_active=True)
    )
    db_session.commit()
    service = StockSectorResearchService(db_session)
    try:
        service.analyze_stock("TESTP")
    except ValueError as exc:
        assert "ordinary shares" in str(exc)
    else:
        raise AssertionError("promoter share must not receive ordinary-share triggers")



def test_weighted_score_model_has_100_points_and_is_not_a_probability() -> None:
    indicators = {
        "close": 150.0,
        "sma_20": 140.0,
        "sma_50": 130.0,
        "sma_200": 120.0,
        "rsi_14": 62.0,
        "macd": 4.0,
        "macd_signal": 2.0,
        "macd_histogram": 2.0,
        "previous_macd_histogram": 1.5,
        "adx_14": 32.0,
        "stoch_k_14": 65.0,
        "stoch_d_3": 60.0,
        "obv_rising_20d": True,
        "volume_ratio_20d": 2.0,
        "daily_return": 0.04,
        "breakout_20d": True,
        "breakdown_20d": False,
        "return_60d": 0.12,
        "return_120d": 0.24,
        "return_200d": 0.35,
    }
    smc = {"bullish_points": 15, "bearish_points": 0}
    market = {"regime": "bullish"}
    sector = {
        "relative_strength_vs_nepse_20d": 0.04,
        "relative_strength_vs_nepse_60d": 0.07,
        "relative_strength_vs_nepse_120d": 0.11,
    }

    result = _score_signal(indicators, smc, market, sector)

    assert sum(result["weights"].values()) == 100
    assert result["buy_score"] == 100
    assert result["sell_score"] == 0
    assert "not a probability" in result["score_type"]


def test_weighted_score_model_handles_bearish_evidence() -> None:
    indicators = {
        "close": 80.0,
        "sma_20": 90.0,
        "sma_50": 100.0,
        "sma_200": 110.0,
        "rsi_14": 32.0,
        "macd": -4.0,
        "macd_signal": -2.0,
        "macd_histogram": -2.0,
        "previous_macd_histogram": -1.5,
        "adx_14": 32.0,
        "stoch_k_14": 25.0,
        "stoch_d_3": 40.0,
        "obv_rising_20d": False,
        "volume_ratio_20d": 2.0,
        "daily_return": -0.04,
        "breakout_20d": False,
        "breakdown_20d": True,
        "return_60d": -0.10,
        "return_120d": -0.20,
        "return_200d": -0.30,
    }
    result = _score_signal(
        indicators,
        {"bullish_points": 0, "bearish_points": 15},
        {"regime": "bearish"},
        {
            "relative_strength_vs_nepse_20d": -0.05,
            "relative_strength_vs_nepse_60d": -0.08,
            "relative_strength_vs_nepse_120d": -0.12,
        },
    )
    assert result["sell_score"] == 100
    assert result["buy_score"] == 0
    assert result["score_margin"] == -100


def test_wilder_rma_uses_initial_period_mean_and_recursive_smoothing() -> None:
    result = _wilder(np.asarray([1.0, 2.0, 3.0, 4.0, 5.0]), 3)
    assert np.isnan(result[0])
    assert np.isnan(result[1])
    assert result[2] == 2.0
    assert abs(result[3] - (8.0 / 3.0)) < 1e-12
    assert abs(result[4] - (31.0 / 9.0)) < 1e-12


def test_wilder_rsi_handles_rising_falling_and_flat_prices() -> None:
    rising = _rsi(np.arange(20.0), 14)
    falling = _rsi(np.arange(20.0, 0.0, -1.0), 14)
    flat = _rsi(np.full(20, 100.0), 14)

    assert rising[14] == 100.0
    assert falling[14] == 0.0
    assert flat[14] == 50.0


def test_smc_rules_do_not_emit_structure_without_minimum_history() -> None:
    result = _smc_snapshot([])
    assert result["available"] is False
    assert result["directional_points"] == 0


def test_chart_series_contains_candles_trend_macd_and_rsi(db_session: Session) -> None:
    stock = Stock(symbol="CHART", instrument_type="ordinary", is_active=True)
    db_session.add(stock)
    db_session.flush()
    for index in range(230):
        close = Decimal(str(100.0 + index * 0.3))
        db_session.add(
            Price(
                stock_id=stock.id,
                date=date(2024, 1, 1) + timedelta(days=index),
                open=close - Decimal("0.1"),
                high=close + Decimal("0.2"),
                low=close - Decimal("0.2"),
                close=close,
                volume=1000 + index * 5,
            )
        )
    db_session.flush()
    rows = db_session.scalars(
        sa.select(Price).where(Price.stock_id == stock.id).order_by(Price.date)
    ).all()
    price_rows = list(rows)
    indicators = _indicator_snapshot(price_rows)
    chart = _chart_series(price_rows, _smc_snapshot(price_rows))

    assert indicators["sma_20"] is not None
    assert indicators["macd_histogram"] is not None
    assert indicators["adx_14"] is not None
    assert indicators["stoch_k_14"] is not None
    assert indicators["stoch_d_3"] is not None
    assert indicators["obv"] is not None
    assert len(chart) == 220
    json.dumps(chart, allow_nan=False)
    assert {
        "open", "high", "low", "close", "volume", "sma_20", "sma_50", "sma_200",
        "macd", "macd_signal", "rsi_14", "adx_14", "stoch_k_14", "stoch_d_3", "obv",
    } <= set(chart[-1])



def test_historical_score_validation_uses_chronological_holdout() -> None:
    stock_rows = []
    sector_rows = []
    benchmark_rows = []
    start = date(2024, 1, 1)
    for index in range(300):
        day = start + timedelta(days=index)
        stock_close = Decimal(str(100.0 + index * 0.2 + index * index * 0.001))
        sector_close = Decimal(str(80.0 + index * 0.25 + index * index * 0.0015))
        index_close = Decimal(str(2000.0 + index * 0.2))
        stock_rows.append(
            Price(
                stock_id=1,
                date=day,
                open=stock_close - Decimal("0.1"),
                high=stock_close + Decimal("0.1"),
                low=stock_close - Decimal("0.3"),
                close=stock_close,
                volume=1000,
            )
        )
        sector_rows.append(
            Price(
                stock_id=2,
                date=day,
                open=sector_close - Decimal("0.1"),
                high=sector_close + Decimal("0.3"),
                low=sector_close - Decimal("0.3"),
                close=sector_close,
                volume=1200,
            )
        )
        benchmark_rows.append(
            NEPSEIndex(
                date=day,
                open=index_close - Decimal("0.1"),
                high=index_close + Decimal("0.2"),
                low=index_close - Decimal("0.2"),
                close=index_close,
                volume=100000,
            )
        )

    result = _historical_score_validation(
        stock_rows,
        benchmark_rows,
        [sector_rows],
    )

    assert result["status"] == "evaluated"
    assert result["validation_method"].startswith("chronological holdout")
    assert result["test_observations"] == 90
    assert result["buy_trigger_count"] > 0
    assert result["results"]["forward_20d"]["buy_sample_count"] > 0
    assert result["assumptions"]["nepse_index_role"].startswith("benchmark only")



def test_saved_score_signal_is_idempotent_and_not_probability(db_session: Session) -> None:
    stock = Stock(
        symbol="LEDGER",
        name="Ledger Test Limited",
        sector="Commercial Bank",
        instrument_type="ordinary",
        is_active=True,
    )
    db_session.add(stock)
    db_session.commit()

    base_item = {
        "symbol": "LEDGER",
        "as_of": "2026-11-23",
        "signal": "BUY_TRIGGER",
        "buy_score": 80,
        "sell_score": 20,
        "score_margin": 60,
        "score_model": {"score_type": "evidence points, not probability"},
        "market_context": {"regime": "bullish"},
        "sector_context": {"sector": "Commercial Bank"},
        "data_freshness": {"status": "current"},
        "data_quality": {"status": "usable"},
        "signal_reason": "Test reason",
        "reference_target_level": 120.0,
        "reference_stop_level": 90.0,
    }
    scan_result = {
        "as_of": "2026-11-23",
        "market_context": {"regime": "bullish"},
        "stocks_analyzed": 1,
        "signals": [base_item],
    }

    with patch.object(
        StockSectorResearchService,
        "scan_stocks",
        return_value=scan_result,
    ):
        first = research_routes.refresh_and_save_stock_signals(
            user=cast(Any, None),
            db=db_session,
            limit=300,
        )

    count_after_first = db_session.scalar(sa.select(sa.func.count()).select_from(Signal))
    assert first["snapshots_saved"] == 1
    assert first["value_semantics"].startswith("signed score margin")
    assert count_after_first == 1
    first_row = db_session.scalar(sa.select(Signal))
    assert first_row is not None
    first_recorded_at = first_row.recorded_at

    # Repeating the same direction on the same session updates the original
    # ledger row instead of creating a duplicate.
    repeated_item = {
        **base_item,
        "buy_score": 84,
        "sell_score": 16,
        "score_margin": 68,
        "signal_reason": "Updated test reason",
    }
    scan_result["signals"] = [repeated_item]
    with patch.object(
        StockSectorResearchService,
        "scan_stocks",
        return_value=scan_result,
    ):
        repeated = research_routes.refresh_and_save_stock_signals(
            user=cast(Any, None),
            db=db_session,
            limit=300,
        )

    repeated_rows = db_session.scalars(sa.select(Signal)).all()
    assert repeated["buy_triggers_recorded"] == 1
    assert len(repeated_rows) == 1
    assert repeated_rows[0].recorded_at == first_recorded_at
    assert abs(float(repeated_rows[0].value) - 0.68) < 1e-8
    assert json.loads(repeated_rows[0].rule_params or "{}")["signal_reason"] == "Updated test reason"

    updated_item = {**base_item, "signal": "SELL_TRIGGER", "buy_score": 15, "sell_score": 85, "score_margin": -70}
    scan_result["signals"] = [updated_item]
    with patch.object(
        StockSectorResearchService,
        "scan_stocks",
        return_value=scan_result,
    ):
        second = research_routes.refresh_and_save_stock_signals(
            user=cast(Any, None),
            db=db_session,
            limit=300,
        )

    rows = db_session.scalars(sa.select(Signal)).all()
    assert second["snapshots_saved"] == 1
    assert len(rows) == 2
    by_type = {row.signal_type: row for row in rows}
    assert by_type["BUY_TRIGGER"].signal_type == "BUY_TRIGGER"
    assert by_type["SELL_TRIGGER"].signal_type == "SELL_TRIGGER"
    assert abs(float(by_type["SELL_TRIGGER"].value) + 0.7) < 1e-8
    assert by_type["SELL_TRIGGER"].confidence is None



def test_signal_ledger_records_buy_sell_triggers_and_forward_outcomes(
    db_session: Session,
) -> None:
    buy_stock = Stock(
        symbol="BUYTEST",
        name="Buy Test Limited",
        sector="Commercial Bank",
        instrument_type="ordinary",
        is_active=True,
    )
    sell_stock = Stock(
        symbol="SELLTEST",
        name="Sell Test Limited",
        sector="Commercial Bank",
        instrument_type="ordinary",
        is_active=True,
    )
    db_session.add_all([buy_stock, sell_stock])
    db_session.flush()
    signal_date = date(2024, 1, 1)
    buy_opens = [101, 102, 103, 104, 105]
    buy_closes = [102, 104, 103, 106, 108]
    sell_opens = [100, 99, 98, 96, 97]
    sell_closes = [99, 98, 96, 97, 93]
    for stock, signal_close, opens, closes in (
        (buy_stock, 100, buy_opens, buy_closes),
        (sell_stock, 100, sell_opens, sell_closes),
    ):
        db_session.add(
            Price(
                stock_id=stock.id,
                date=signal_date,
                open=Decimal("99"),
                high=Decimal("101"),
                low=Decimal("98"),
                close=Decimal(str(signal_close)),
                volume=1000,
            )
        )
        for index, (open_price, close_price) in enumerate(zip(opens, closes, strict=True), start=1):
            db_session.add(
                Price(
                    stock_id=stock.id,
                    date=signal_date + timedelta(days=index),
                    open=Decimal(str(open_price)),
                    high=Decimal(str(max(open_price, close_price) + 1)),
                    low=Decimal(str(min(open_price, close_price) - 1)),
                    close=Decimal(str(close_price)),
                    volume=1200 + index,
                )
            )
    db_session.flush()

    def signal_item(
        symbol: str,
        signal_type: str,
        buy_score: int,
        sell_score: int,
        margin: int,
    ) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "as_of": signal_date.isoformat(),
            "signal": signal_type,
            "buy_score": buy_score,
            "sell_score": sell_score,
            "score_margin": margin,
            "score_model": {"score_type": "evidence points, not probability"},
            "market_context": {"regime": "sideways"},
            "sector_context": {"sector": "Commercial Bank"},
            "data_freshness": {"status": "current"},
            "data_quality": {"status": "usable"},
            "signal_reason": "Deterministic ledger test",
            "indicators": {"close": 100.0, "rsi_14": 62.0, "macd": 2.5},
            "confirmation_gates": {"market_not_bearish": True},
            "confirmation_checks": {"macd_above_signal": True},
            "smart_money_concepts": {"bullish_bos": signal_type == "BUY_TRIGGER"},
            "price_action": {"bullish_engulfing": signal_type == "BUY_TRIGGER"},
            "reference_stop_level": 95.0,
            "reference_target_level": 110.0,
            "reference_risk_reward_ratio": 2.0,
        }

    scan_result = {
        "as_of": signal_date.isoformat(),
        "market_context": {"regime": "sideways"},
        "stocks_analyzed": 2,
        "signals": [
            signal_item("BUYTEST", "BUY_TRIGGER", 80, 20, 60),
            signal_item("SELLTEST", "SELL_TRIGGER", 15, 85, -70),
        ],
    }
    with patch.object(
        StockSectorResearchService,
        "scan_stocks",
        return_value=scan_result,
    ):
        saved = research_routes.refresh_and_save_stock_signals(
            user=cast(Any, None),
            db=db_session,
            limit=300,
        )

    assert saved["buy_triggers_recorded"] == 1
    assert saved["sell_triggers_recorded"] == 1
    assert saved["trigger_records_updated"] == 2
    assert saved["outcome_refresh"]["signals_evaluated"] == 2
    assert saved["outcome_refresh"]["horizon_results_completed"] == 4

    records = db_session.scalars(
        sa.select(Signal).order_by(Signal.stock_id)
    ).all()
    assert len(records) == 2
    by_type = {record.signal_type: json.loads(record.rule_params or "{}") for record in records}
    buy_details = by_type["BUY_TRIGGER"]
    sell_details = by_type["SELL_TRIGGER"]
    assert buy_details["snapshot"]["indicators"]["rsi_14"] == 62.0
    assert buy_details["snapshot"]["confirmation_gates"]["market_not_bearish"] is True
    assert buy_details["forward_performance"]["outcomes"]["1"]["gross_return_pct"] > 0
    assert buy_details["forward_performance"]["outcomes"]["5"]["successful"] is True
    assert sell_details["forward_performance"]["outcomes"]["1"]["holding_return_pct"] < 0
    assert sell_details["forward_performance"]["outcomes"]["1"]["entry_basis"] == "next_session_open_assumed_exit"
    assert sell_details["forward_performance"]["outcomes"]["1"]["assumed_exit_price"] == 100.0
    assert sell_details["forward_performance"]["outcomes"]["5"]["successful_exit"] is True
    assert sell_details["forward_performance"]["outcomes"]["10"]["status"] == "pending"

    summary = research_routes.get_saved_stock_signal_performance(
        db=db_session,
        limit=100,
    )
    assert summary["buy_trigger_count"] == 1
    assert summary["sell_trigger_count"] == 1
    assert summary["horizons"]["1"]["buy"]["samples"] == 1
    assert summary["horizons"]["1"]["sell"]["favorable_exit_rate_pct"] == 100.0
    assert summary["horizons"]["5"]["buy"]["positive_return_rate_pct"] == 100.0

    history = research_routes.get_saved_stock_signal_history(
        db=db_session,
        symbol=None,
        limit=100,
    )
    assert history["count"] == 2
    history_by_type = {item["signal"]: item for item in history["signals"]}
    assert history_by_type["BUY_TRIGGER"]["recorded_at"]
    assert history_by_type["BUY_TRIGGER"]["updated_at"]
    assert history_by_type["BUY_TRIGGER"]["snapshot"]["indicators"]["rsi_14"] == 62.0
    assert history_by_type["SELL_TRIGGER"]["forward_performance"]["outcomes"]["1"]["status"] == "complete"

    # As more daily bars arrive, pending forward tests should mature in place.
    for stock, start_price, direction in (
        (buy_stock, 109, 1),
        (sell_stock, 92, -1),
    ):
        for index in range(6, 11):
            close_price = start_price + direction * (index - 5)
            db_session.add(
                Price(
                    stock_id=stock.id,
                    date=signal_date + timedelta(days=index),
                    open=Decimal(str(close_price - direction * 0.5)),
                    high=Decimal(str(close_price + 1)),
                    low=Decimal(str(close_price - 1)),
                    close=Decimal(str(close_price)),
                    volume=1500 + index,
                )
            )
    db_session.flush()
    with patch.object(
        StockSectorResearchService,
        "scan_stocks",
        return_value=scan_result,
    ):
        refreshed = research_routes.refresh_and_save_stock_signals(
            user=cast(Any, None),
            db=db_session,
            limit=300,
        )

    assert refreshed["buy_triggers_recorded"] == 1
    assert refreshed["sell_triggers_recorded"] == 1
    assert db_session.scalar(sa.select(sa.func.count()).select_from(Signal)) == 2
    refreshed_rows = db_session.scalars(sa.select(Signal)).all()
    refreshed_by_type = {
        row.signal_type: json.loads(row.rule_params or "{}")
        for row in refreshed_rows
    }
    assert refreshed_by_type["BUY_TRIGGER"]["forward_performance"]["outcomes"]["10"]["status"] == "complete"
    assert refreshed_by_type["SELL_TRIGGER"]["forward_performance"]["outcomes"]["10"]["successful_exit"] is True


def test_forward_outcomes_quarantine_large_raw_price_discontinuities(
    db_session: Session,
) -> None:
    stock = Stock(
        symbol="GAPTEST",
        name="Gap Test Limited",
        sector="Commercial Bank",
        instrument_type="ordinary",
        is_active=True,
    )
    db_session.add(stock)
    db_session.flush()
    signal_date = date(2026, 1, 1)
    signal = Signal(
        stock_id=stock.id,
        date=signal_date,
        signal_type="BUY_TRIGGER",
        value=0.60,
        provider="research_score",
        model_source="sector-stock-score-v1",
        rule_params=json.dumps({"snapshot": {"close": 100.0}}),
    )
    db_session.add(signal)

    # The first forward session is continuous; the second has a raw 60% drop
    # that may represent a corporate action or a source-data discontinuity.
    bars = [
        (100.0, 100.0),
        (40.0, 40.0),
        (41.0, 41.0),
        (42.0, 42.0),
        (43.0, 43.0),
    ]
    for index, (open_price, close_price) in enumerate(bars, start=1):
        db_session.add(
            Price(
                stock_id=stock.id,
                date=signal_date + timedelta(days=index),
                open=Decimal(str(open_price)),
                high=Decimal(str(max(open_price, close_price) + 1)),
                low=Decimal(str(min(open_price, close_price) - 1)),
                close=Decimal(str(close_price)),
                volume=1000,
            )
        )
    db_session.flush()

    refresh = research_routes._update_forward_outcomes(db_session)
    details = json.loads(signal.rule_params or "{}")
    outcomes = details["forward_performance"]["outcomes"]

    assert refresh["signals_evaluated"] == 1
    assert outcomes["1"]["status"] == "complete"
    assert outcomes["5"]["status"] == "review_required"
    assert outcomes["5"]["flagged_price_gaps"][0]["date"] == (
        signal_date + timedelta(days=2)
    ).isoformat()
    assert outcomes["5"]["flagged_price_gaps"][0]["change_pct"] == -60.0
    assert outcomes["10"]["status"] == "pending"

    summary = research_routes.get_saved_stock_signal_performance(
        db=db_session,
        limit=100,
    )
    assert summary["horizons"]["1"]["buy"]["samples"] == 1
    assert summary["horizons"]["5"]["buy"]["samples"] == 0
