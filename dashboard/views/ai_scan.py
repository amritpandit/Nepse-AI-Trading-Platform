"""Dedicated AI BUY/SELL scan, persistent signal ledger and forward-test view."""
from __future__ import annotations

import pandas as pd
import requests
import streamlit as st

from common import API_BASE, auth_headers


def render_ai_signal_centre() -> None:
    # The authenticated scan action persists the current universe snapshot and
    # refreshes the outcome ledger. Repeated scans for the same stock/session
    # update the same record rather than creating duplicate rows.
    st.subheader("🎯 AI BUY / SELL Signal Centre")
    st.caption(
        "Explainable daily research signals from sector-stock-score-v1. "
        "Pressing the scan button records the full eligible ordinary-share universe "
        "and updates forward tests. Scores are evidence points, not probabilities of profit. "
        "SELL is an exit/reduce review for an existing holding, never a short-sale instruction."
    )

    scan_limit = st.selectbox(
        "Number of prioritized results",
        options=[20, 50, 100, 200],
        index=2,
        key="ai_scan_signal_limit",
    )
    if st.button("Run & Record AI Stock Scan", type="primary", key="ai_scan_run_ai_scan"):
        if not st.session_state.get("auth_token"):
            st.error("Please sign in from the sidebar before recording BUY/SELL triggers.")
        else:
            with st.spinner(
                "Scanning all eligible ordinary shares, saving signal snapshots and "
                "refreshing 1/5/10/20-session forward outcomes…"
            ):
                try:
                    response = requests.post(
                        f"{API_BASE}/research/signals/refresh",
                        params={"limit": 300},
                        headers=auth_headers(),
                        timeout=180,
                    )
                    response.raise_for_status()
                    scan_payload = response.json()
                    if not isinstance(scan_payload, dict):
                        st.error("The research API returned an unexpected response.")
                    else:
                        st.session_state["ai_scan_payload"] = scan_payload
                except requests.RequestException as exc:
                    st.error(f"AI stock scan or ledger update failed: {exc}")

    scan_payload = st.session_state.get("ai_scan_payload")
    if isinstance(scan_payload, dict):
        scan_rows = scan_payload.get("signals", [])
        scan_rows = scan_rows if isinstance(scan_rows, list) else []
        scan_summary = scan_payload.get("market_context", {})
        scan_summary = scan_summary if isinstance(scan_summary, dict) else {}

        st.caption(
            f"Latest analyzed session: {scan_payload.get('as_of') or 'not available'} · "
            f"Eligible stocks analyzed: {scan_payload.get('stocks_analyzed', 0):,} · "
            f"Signal snapshots upserted: {scan_payload.get('snapshots_saved', 0):,} · "
            f"BUY triggers recorded: {scan_payload.get('buy_triggers_recorded', 0):,} · "
            f"SELL triggers recorded: {scan_payload.get('sell_triggers_recorded', 0):,} · "
            f"NEPSE regime: {str(scan_summary.get('regime', 'unavailable')).upper()}"
        )

        signal_counts = {
            name: sum(1 for item in scan_rows if item.get("signal") == name)
            for name in ("BUY_TRIGGER", "SELL_TRIGGER", "WATCH", "STALE_DATA", "HOLD")
        }
        summary_cols = st.columns(5)
        summary_cols[0].metric("BUY triggers", signal_counts["BUY_TRIGGER"])
        summary_cols[1].metric("SELL reviews", signal_counts["SELL_TRIGGER"])
        summary_cols[2].metric("Watch", signal_counts["WATCH"])
        summary_cols[3].metric("Stale data", signal_counts["STALE_DATA"])
        summary_cols[4].metric("Hold", signal_counts["HOLD"])

        signal_filter = st.selectbox(
            "Filter signal type",
            options=["ALL", "BUY_TRIGGER", "SELL_TRIGGER", "WATCH", "STALE_DATA", "HOLD"],
            key="market_overview_signal_filter",
        )
        filtered_scan = (
            scan_rows
            if signal_filter == "ALL"
            else [item for item in scan_rows if item.get("signal") == signal_filter]
        )[: int(scan_limit)]
        signal_table_rows = [
            {
                "Symbol": item.get("symbol"),
                "Company": item.get("name"),
                "Sector": item.get("sector"),
                "Signal": item.get("signal"),
                "BUY / 100": item.get("buy_score"),
                "SELL / 100": item.get("sell_score"),
                "BUY − SELL": item.get("score_margin"),
                "As of": item.get("as_of"),
                "Close (NPR)": (item.get("indicators") or {}).get("close"),
                "Stop reference": item.get("reference_stop_level"),
                "Target reference": item.get("reference_target_level"),
                "Data": (item.get("data_freshness") or {}).get("status"),
                "Reason": item.get("signal_reason"),
            }
            for item in filtered_scan
            if isinstance(item, dict)
        ]
        st.dataframe(
            pd.DataFrame(signal_table_rows),
            width="stretch",
            hide_index=True,
            height=480,
        )
        if scan_payload.get("data_disclaimer"):
            st.caption(str(scan_payload["data_disclaimer"]))
        st.caption(
            "Only BUY_TRIGGER / SELL_TRIGGER rows passed the model's explicit evidence thresholds "
            "and confirmation gates. WATCH/HOLD rows are not trade instructions. For factor-by-factor "
            "evidence, candlestick charts, MACD/RSI/ADX and SMC overlays, open Sector & Stock Research "
            "or Technical Analysis from the navigation menu."
        )
    else:
        st.info(
            "Sign in and run the scan to save the current BUY/SELL trigger snapshots, "
            "their supporting evidence and forward-performance test records."
        )

    st.divider()
    st.subheader("📚 Signal Ledger & Strategy Test Results")
    st.caption(
        "BUY is tested from the next session's open; SELL assumes an existing holder exits "
        "at the next session's open and compares with continued holding. "
        "Results use raw, non-corporate-action-adjusted OHLC and are gross of fees, "
        "slippage, taxes and liquidity effects; review flagged price gaps."
    )
    try:
        performance_response = requests.get(
            f"{API_BASE}/research/signals/performance",
            params={"limit": 10000},
            timeout=30,
        )
        performance_response.raise_for_status()
        performance_payload = performance_response.json()
        horizon_payload = (
            performance_payload.get("horizons", {})
            if isinstance(performance_payload, dict)
            else {}
        )
        performance_rows = []
        if isinstance(horizon_payload, dict):
            for horizon in (1, 5, 10, 20):
                result = horizon_payload.get(str(horizon), {})
                result = result if isinstance(result, dict) else {}
                buy_result = result.get("buy", {})
                sell_result = result.get("sell", {})
                buy_result = buy_result if isinstance(buy_result, dict) else {}
                sell_result = sell_result if isinstance(sell_result, dict) else {}
                performance_rows.append(
                    {
                        "Horizon (sessions)": horizon,
                        "BUY samples": buy_result.get("samples", 0),
                        "BUY avg. gross return (%)": buy_result.get("average_gross_return_pct"),
                        "BUY positive rate (%)": buy_result.get("positive_return_rate_pct"),
                        "BUY gap-review samples": buy_result.get("review_required_samples", 0),
                        "SELL samples": sell_result.get("samples", 0),
                        "SELL avg. subsequent hold return (%)": sell_result.get(
                            "average_subsequent_holding_return_pct"
                        ),
                        "SELL favorable exit rate (%)": sell_result.get("favorable_exit_rate_pct"),
                        "SELL gap-review samples": sell_result.get("review_required_samples", 0),
                    }
                )
        if performance_rows:
            st.dataframe(
                pd.DataFrame(performance_rows),
                use_container_width=True,
                hide_index=True,
            )
        if isinstance(performance_payload, dict):
            st.caption(
                f"BUY records: {performance_payload.get('buy_trigger_count', 0):,} · "
                f"SELL records: {performance_payload.get('sell_trigger_count', 0):,} · "
                f"Scorecard records reviewed: {performance_payload.get('records_included', 0):,} · "
                f"Model: {performance_payload.get('model_version', 'unavailable')}"
            )

        history_response = requests.get(
            f"{API_BASE}/research/signals/history",
            params={"limit": 10000, "triggers_only": True},
            timeout=30,
        )
        history_response.raise_for_status()
        history_payload = history_response.json()
        history_rows = history_payload.get("signals", []) if isinstance(history_payload, dict) else []
        ledger_display = []
        for item in history_rows if isinstance(history_rows, list) else []:
            if not isinstance(item, dict):
                continue
            outcomes = item.get("forward_performance", {})
            outcomes = outcomes.get("outcomes", {}) if isinstance(outcomes, dict) else {}
            one_session = outcomes.get("1", {}) if isinstance(outcomes, dict) else {}
            five_session = outcomes.get("5", {}) if isinstance(outcomes, dict) else {}
            forward_meta = item.get("forward_performance", {})
            forward_meta = forward_meta if isinstance(forward_meta, dict) else {}
            gap_events = forward_meta.get("flagged_price_gaps", [])
            gap_events = gap_events if isinstance(gap_events, list) else []
            gap_review = ", ".join(
                f"{gap.get('date')}: {gap.get('change_pct'):+.2f}%"
                for gap in gap_events
                if isinstance(gap, dict)
                and isinstance(gap.get("change_pct"), int | float)
            )
            one_value = (
                one_session.get("gross_return_pct")
                if item.get("signal") == "BUY_TRIGGER"
                else one_session.get("holding_return_pct")
            ) if isinstance(one_session, dict) and one_session.get("status") == "complete" else None
            five_value = (
                five_session.get("gross_return_pct")
                if item.get("signal") == "BUY_TRIGGER"
                else five_session.get("holding_return_pct")
            ) if isinstance(five_session, dict) and five_session.get("status") == "complete" else None
            ledger_display.append(
                {
                    "Signal date": item.get("date"),
                    "Recorded at": item.get("recorded_at"),
                    "Last updated": item.get("updated_at"),
                    "Symbol": item.get("symbol"),
                    "Company": item.get("name"),
                    "Signal": item.get("signal"),
                    "BUY score": item.get("buy_score"),
                    "SELL score": item.get("sell_score"),
                    "Price at trigger": item.get("price_at_signal"),
                    "1-session status": one_session.get("status") if isinstance(one_session, dict) else None,
                    "1-session test (%)": one_value,
                    "5-session status": five_session.get("status") if isinstance(five_session, dict) else None,
                    "5-session test (%)": five_value,
                    "Price-gap review": gap_review,
                    "Reason": item.get("signal_reason"),
                }
            )
        if ledger_display:
            ledger_df = pd.DataFrame(ledger_display)
            st.dataframe(
                ledger_df,
                use_container_width=True,
                hide_index=True,
                height=360,
            )
            st.download_button(
                "📥 Export signal ledger (CSV)",
                data=ledger_df.to_csv(index=False).encode("utf-8"),
                file_name="nepse_signal_ledger.csv",
                mime="text/csv",
                key="signal_ledger_export",
            )
        else:
            st.info("No persisted signal snapshots yet. Sign in and run the AI scan to begin the ledger.")
    except requests.RequestException as exc:
        st.warning(f"Signal history/performance is temporarily unavailable: {exc}")

    st.divider()

 


def page_ai_scan() -> None:
    """Top-level AI scan workspace."""
    st.header("🎯 AI Stock Scan & Forward Testing")
    st.caption(
        "Run an explainable research scan across the eligible ordinary-share universe, "
        "record BUY/SELL triggers, and monitor 1/5/10/20-session forward outcomes."
    )
    render_ai_signal_centre()
