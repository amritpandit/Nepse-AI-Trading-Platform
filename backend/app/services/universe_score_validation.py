"""Cross-stock chronological holdout validation for the canonical research score model.

This is an event-study ranking aid, not a capital-constrained portfolio simulation.
"""
from __future__ import annotations

from statistics import fmean
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models.benchmark import NEPSEIndex
from app.models.stock import Stock
from app.services.stock_sector_research import (
    StockSectorResearchService,
    _clean_after_latest_discontinuity,
    _historical_score_validation,
)


VALIDATION_HORIZONS = (5, 10, 20)


class UniverseScoreValidationService:
    """Evaluate active ordinary shares on a chronological out-of-sample holdout."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def run(
        self,
        *,
        sector: str | None = None,
        limit: int = 30,
        min_samples: int = 5,
    ) -> dict[str, Any]:
        """Rank a bounded stock cohort by historical score-model excess returns.

        The 70/30 chronological split and the underlying entry/exit assumptions
        are shared with the single-stock validation shown on the stock-detail page.
        All same-sector member histories are retained so sector-relative strength
        is calculated from date-matched observations rather than future data.
        """
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if not 1 <= min_samples <= 50:
            raise ValueError("min_samples must be between 1 and 50")

        query = (
            sa.select(Stock)
            .where(
                Stock.instrument_type == "ordinary",
                Stock.is_active.is_(True),
            )
            .order_by(Stock.symbol)
        )
        normalized_sector = sector.strip() if sector and sector.strip() else None
        if normalized_sector:
            query = query.where(Stock.sector == normalized_sector)

        cohort = self.session.scalars(query).all()
        selected = cohort[:limit]
        if not selected:
            return {
                "model_version": "sector-stock-score-v1",
                "sector_filter": normalized_sector,
                "requested_limit": limit,
                "eligible_symbols_in_cohort": 0,
                "symbols_analyzed": 0,
                "rank_eligible_symbols": 0,
                "minimum_buy_samples_for_ranking": min_samples,
                "results": [],
                "cross_symbol_summary": {},
                "methodology": self._methodology(),
            }

        research = StockSectorResearchService(self.session)
        histories = research._load_histories(cohort, 800)
        benchmark_rows = self.session.scalars(
            sa.select(NEPSEIndex)
            .order_by(NEPSEIndex.date.desc())
            .limit(800)
        ).all()
        benchmark_rows = list(reversed(benchmark_rows))

        members_by_sector: dict[str, list[tuple[int, list[Any]]]] = {}
        for member in cohort:
            if member.sector:
                members_by_sector.setdefault(member.sector, []).append(
                    (member.id, histories.get(member.id, []))
                )

        results: list[dict[str, Any]] = []
        for stock in selected:
            raw_history = histories.get(stock.id, [])
            clean_history, discontinuity = _clean_after_latest_discontinuity(raw_history)
            if stock.sector:
                sector_histories = [
                    member_history
                    for member_id, member_history in members_by_sector.get(stock.sector, [])
                    if member_id != stock.id
                ]
            else:
                sector_histories = []

            validation = _historical_score_validation(
                clean_history,
                benchmark_rows,
                sector_histories,
            )
            validation_results = validation.get("results", {})
            horizon_metrics: dict[str, dict[str, Any]] = {}
            for horizon in VALIDATION_HORIZONS:
                metrics = validation_results.get(f"forward_{horizon}d", {})
                buy_samples = int(metrics.get("buy_sample_count", 0) or 0)
                benchmark_samples = int(
                    metrics.get("nepse_benchmark_sample_count", 0) or 0
                )
                buy_mean = metrics.get("buy_mean_net_return")
                buy_win_rate = metrics.get("buy_win_rate")
                nepse_mean = metrics.get("nepse_mean_return")
                excess = metrics.get("mean_excess_vs_nepse")
                sell_samples = int(metrics.get("sell_sample_count", 0) or 0)
                sell_negative_share = metrics.get("negative_return_share_after_sell")
                horizon_metrics[str(horizon)] = {
                    "buy_samples": buy_samples,
                    "nepse_benchmark_samples": benchmark_samples,
                    "buy_win_rate_pct": self._percent(buy_win_rate),
                    "buy_mean_net_return_pct": self._percent(buy_mean),
                    "buy_median_net_return_pct": self._percent(
                        metrics.get("buy_median_net_return")
                    ),
                    "nepse_mean_return_pct": self._percent(nepse_mean),
                    "mean_excess_vs_nepse_pct": self._percent(excess),
                    "sell_samples": sell_samples,
                    "negative_return_share_after_sell_pct": self._percent(
                        sell_negative_share
                    ),
                    "mean_forward_return_after_sell_pct": self._percent(
                        metrics.get("mean_forward_return_after_sell")
                    ),
                }

            twenty = horizon_metrics["20"]
            rank_eligible = (
                validation.get("status") == "evaluated"
                and twenty["buy_samples"] >= min_samples
                and twenty["nepse_benchmark_samples"] >= min_samples
                and twenty["mean_excess_vs_nepse_pct"] is not None
            )
            results.append(
                {
                    "symbol": stock.symbol,
                    "name": stock.name,
                    "sector": stock.sector,
                    "validation_status": validation.get("status", "unavailable"),
                    "test_start_date": validation.get("test_start_date"),
                    "test_end_date": validation.get("test_end_date"),
                    "clean_history_bars": len(clean_history),
                    "price_discontinuity_flagged": bool(discontinuity.get("flagged")),
                    "buy_trigger_count": int(validation.get("buy_trigger_count", 0) or 0),
                    "sell_trigger_count": int(validation.get("sell_trigger_count", 0) or 0),
                    "rank_eligible": rank_eligible,
                    "rank_basis": "20-session mean net BUY excess return versus NEPSE",
                    "horizons": horizon_metrics,
                    "validation_reason": validation.get("reason"),
                }
            )

        results.sort(
            key=lambda item: (
                not item["rank_eligible"],
                -(
                    item["horizons"]["20"]["mean_excess_vs_nepse_pct"]
                    if item["horizons"]["20"]["mean_excess_vs_nepse_pct"] is not None
                    else -1_000_000.0
                ),
                -item["horizons"]["20"]["buy_samples"],
                item["symbol"],
            )
        )
        rank = 0
        for item in results:
            if item["rank_eligible"]:
                rank += 1
                item["rank"] = rank
            else:
                item["rank"] = None

        ranked = [item for item in results if item["rank_eligible"]]
        cross_symbol_summary: dict[str, dict[str, Any]] = {}
        for horizon in VALIDATION_HORIZONS:
            eligible_metrics = [
                item["horizons"][str(horizon)]
                for item in ranked
                if (
                    item["horizons"][str(horizon)]["buy_samples"] >= min_samples
                    and item["horizons"][str(horizon)]["nepse_benchmark_samples"]
                    >= min_samples
                )
            ]
            cross_symbol_summary[str(horizon)] = {
                "symbols_with_minimum_samples": len(eligible_metrics),
                "mean_symbol_buy_win_rate_pct": self._mean_field(
                    eligible_metrics, "buy_win_rate_pct"
                ),
                "mean_symbol_buy_net_return_pct": self._mean_field(
                    eligible_metrics, "buy_mean_net_return_pct"
                ),
                "mean_symbol_excess_vs_nepse_pct": self._mean_field(
                    eligible_metrics, "mean_excess_vs_nepse_pct"
                ),
            }

        latest_price_date = max(
            (rows[-1].date.isoformat() for rows in histories.values() if rows),
            default=None,
        )
        return {
            "model_version": "sector-stock-score-v1",
            "sector_filter": normalized_sector,
            "requested_limit": limit,
            "eligible_symbols_in_cohort": len(cohort),
            "symbols_analyzed": len(results),
            "rank_eligible_symbols": len(ranked),
            "minimum_buy_samples_for_ranking": min_samples,
            "latest_price_date_in_cohort": latest_price_date,
            "ranking_method": (
                "Descending 20-session mean net BUY return in the chronological "
                "out-of-sample period minus matched NEPSE Index return. "
                "Only symbols meeting the minimum BUY-sample threshold are ranked."
            ),
            "results": results,
            "cross_symbol_summary": cross_symbol_summary,
            "methodology": self._methodology(),
        }

    @staticmethod
    def _percent(value: Any) -> float | None:
        if isinstance(value, int | float):
            return round(float(value) * 100.0, 4)
        return None

    @staticmethod
    def _mean_field(rows: list[dict[str, Any]], field: str) -> float | None:
        values = [
            float(row[field])
            for row in rows
            if isinstance(row.get(field), int | float)
        ]
        return round(fmean(values), 4) if values else None

    @staticmethod
    def _methodology() -> dict[str, Any]:
        return {
            "validation": (
                "Per-stock chronological 70/30 holdout. The earlier observations "
                "provide indicator warm-up/development history; only the final 30% "
                "is evaluated."
            ),
            "buy_entry": "Next recorded session open after a completed daily-close signal.",
            "buy_exit": "Fixed horizon close at 5, 10 or 20 sessions from the signal date.",
            "buy_costs": {
                "one_way_commission_rate": 0.005,
                "slippage_bps_per_side": 5.0,
            },
            "sell_interpretation": (
                "SELL signals are reviewed as potential exits for an existing long "
                "holding. Negative forward returns are favorable exit observations; "
                "no short selling is assumed."
            ),
            "sector_strength": (
                "Same-date median member-stock returns relative to NEPSE over "
                "20, 60 and 120 sessions."
            ),
            "benchmark_role": "NEPSE Index is a benchmark only and is never traded.",
            "portfolio_simulation": False,
            "corporate_action_adjusted": False,
            "survivorship_bias_controlled": False,
            "limitations": [
                "This ranks per-symbol event-study results; it does not simulate simultaneous positions, "
                "cash constraints, position sizing, stop/target execution or a portfolio equity curve.",
                "Historical source prices are raw and not corporate-action adjusted. "
                "Price discontinuities are flagged for review.",
                "The active-stock universe is used; delisted-stock survivorship bias is not removed.",
                "Historical outcomes are not a promise or probability of future returns.",
            ],
        }
