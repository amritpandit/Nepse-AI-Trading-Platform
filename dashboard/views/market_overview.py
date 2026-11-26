from __future__ import annotations

import datetime as dt

import pandas as pd
import requests
import plotly.express as px
import streamlit as st
from common import (
    API_BASE,
    _safe_dict,
    _safe_float,
    get_daily_quality_report,
    get_trust_score,
    load_live_market,
    load_market_overview,
)


def page_market_overview():
    """Market overview with data quality metrics."""  # type: ignore[annotation-unchecked]

    st.header("📊 Market Overview")

    # Load market data
    overview = load_market_overview()
    quality_report = get_daily_quality_report()
    overview_data = _safe_dict(overview)
    quality_data = _safe_dict(quality_report)

    # Key metrics row
    if overview_data:
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            nse_value = overview_data.get("nse_value")
            nse_change = overview_data.get("nse_change")
            st.metric(
                "NEPSE Index",
                f"{nse_value:,.2f}" if isinstance(nse_value, int | float) else "N/A",
                delta=f"{nse_change:+.2f}%" if isinstance(nse_change, int | float) else None,
                delta_color="normal",
                help="Index snapshot from the attributed YONEPSE third-party feed; cross-check important figures with NEPSE.",
            )

        with col2:
            st.metric(
                "Tracked Symbols",
                overview_data.get("total_symbols", 0),
                delta=overview_data.get("new_symbols_today", 0),
            )

        with col3:
            vol_change = overview_data.get("volume_change")
            st.metric(
                "Total Volume",
                f"{_safe_float(overview_data.get('total_volume', 0)):,.0f}",
                delta=f"{vol_change:+.1f}%" if isinstance(vol_change, int | float) else None,
                help=f"Aggregated from the market feed for {overview_data.get('market_data_date') or 'the latest available trading date'}.",
            )

        with col4:
            st.metric(
                "Active Signals",
                overview_data.get("active_signals", 0),
                delta=overview_data.get("new_signals_today", 0),
                delta_color="off",
            )
    else:
        st.info("Market overview unavailable. Ensure backend is running.")

    source = overview_data.get("market_data_source")
    freshness = overview_data.get("market_data_freshness", "unavailable")
    as_of = overview_data.get("market_data_date") or "timestamp unavailable"
    if source:
        st.caption(
            f"Market source: {source} · Data as of: {as_of} · Feed status: {str(freshness).upper()}. "
            "Unofficial third-party feed; verify important prices independently."
        )
        market_open = overview_data.get("market_is_open")
        market_state = (
            "OPEN" if market_open is True else "CLOSED" if market_open is False else "UNKNOWN"
        )
        status_freshness = overview_data.get("market_status_freshness", "unknown")
        status_age = overview_data.get("market_status_age_minutes")
        status_age_label = (
            f"{status_age:.0f} minutes old"
            if isinstance(status_age, int | float)
            else "age unknown"
        )
        st.caption(
            f"Provider market status: {market_state} · "
            f"Last checked: {overview_data.get('market_status_checked_at') or 'not reported'} · "
            f"status feed: {str(status_freshness).upper()} ({status_age_label})"
        )
        st.markdown("[YONEPSE source repository](https://github.com/Shubhamnpk/yonepse)")
        if freshness == "stale":
            st.warning("Market data is stale. Do not use these prices as current quotes.")
        elif freshness == "delayed":
            st.info(
                "The latest available market snapshot is delayed; "
                "verify prices before relying on them."
            )
        elif freshness == "market_closed":
            st.info(
                "The exchange is closed. This is the latest available session snapshot, "
                "not a live market quote."
            )
    else:
        st.warning("Live market feed is unavailable. The index is shown as N/A rather than estimated.")

    st.subheader("🔎 Market Screener")
    screener_col1, screener_col2 = st.columns([2, 1])
    with screener_col1:
        market_search = st.text_input(
            "Search by company or symbol",
            placeholder="e.g. NABIL, Hydro, Bank",
            key="market_screener_search",
        )
    with screener_col2:
        sort_labels = {
            "Turnover": "turnover",
            "Trading Volume": "volume",
            "Daily Change (%)": "percent_change",
            "Market Capitalization": "market_cap",
            "Last Traded Price": "ltp",
        }
        selected_sort = st.selectbox(
            "Rank stocks by", list(sort_labels), key="market_screener_sort"
        )

    live_market = load_live_market(
        search=market_search,
        sort_by=sort_labels[selected_sort],
        order="desc",
        limit=500,
    )
    live_rows = live_market.get("stocks", []) if isinstance(live_market, dict) else []
    if live_rows:
        live_df = pd.DataFrame(live_rows)
        display_columns = [
            column
            for column in [
                "symbol",
                "name",
                "ltp",
                "change",
                "percent_change",
                "volume",
                "turnover",
                "trades",
                "last_updated",
                "freshness",
                "age_minutes",
            ]
            if column in live_df.columns
        ]
        st.dataframe(
            live_df[display_columns],
            use_container_width=True,
            hide_index=True,
            height=420,
        )
        st.caption(
            f"Showing {len(live_rows)} of {live_market.get('total_results', len(live_rows))} matching "
            "listed stocks. Sort values are based on the latest available provider snapshot."
        )

        # Calculate breadth and top movers from the exact provider snapshot
        # displayed above. Null price changes are excluded rather than assumed flat.
        mover_df = live_df.copy()
        if "percent_change" in mover_df.columns:
            mover_df["percent_change"] = pd.to_numeric(
                mover_df["percent_change"], errors="coerce"
            )
            mover_df = mover_df.dropna(subset=["percent_change"])

            advancers = int((mover_df["percent_change"] > 0).sum())
            decliners = int((mover_df["percent_change"] < 0).sum())
            unchanged = int((mover_df["percent_change"] == 0).sum())

            st.subheader("📊 Market Breadth")
            breadth_cols = st.columns(3)
            breadth_cols[0].metric("Advancers", f"{advancers:,}")
            breadth_cols[1].metric("Decliners", f"{decliners:,}")
            breadth_cols[2].metric("Unchanged", f"{unchanged:,}")

            gainers = mover_df.nlargest(10, "percent_change").sort_values("percent_change")
            losers = mover_df.nsmallest(10, "percent_change").sort_values("percent_change")
            gain_col, loss_col = st.columns(2)
            with gain_col:
                st.subheader("Top Gainers")
                if not gainers.empty:
                    gain_fig = px.bar(
                        gainers,
                        x="percent_change",
                        y="symbol",
                        orientation="h",
                        labels={"percent_change": "Change (%)", "symbol": "Symbol"},
                    )
                    gain_fig.update_layout(height=360, margin={"l": 10, "r": 10, "t": 15, "b": 15})
                    st.plotly_chart(gain_fig, width="stretch")
            with loss_col:
                st.subheader("Top Losers")
                if not losers.empty:
                    loss_fig = px.bar(
                        losers,
                        x="percent_change",
                        y="symbol",
                        orientation="h",
                        labels={"percent_change": "Change (%)", "symbol": "Symbol"},
                    )
                    loss_fig.update_layout(height=360, margin={"l": 10, "r": 10, "t": 15, "b": 15})
                    st.plotly_chart(loss_fig, width="stretch")
    elif isinstance(live_market, dict):
        st.info("No stocks matched the search.")
    else:
        st.warning("Stock screener data is unavailable. Check the backend market feed.")

    st.divider()

    # Read-only AI research scan on the landing page. The model analyzes the
    # full eligible ordinary-share universe and returns its highest-priority
    # results; this endpoint does not write signal snapshots to the database.
    st.subheader("🎯 AI BUY / SELL Signal Centre")
    st.caption(
        "Explainable daily research signals from sector-stock-score-v1. "
        "Scores are weighted evidence points, not probabilities of profit. "
        "SELL is an exit/reduce review for an existing holding, never a short-sale instruction."
    )

    scan_limit = st.selectbox(
        "Number of prioritized results",
        options=[20, 50, 100, 200],
        index=2,
        key="market_overview_signal_limit",
    )
    if st.button("Run AI Stock Scan", type="primary", key="market_overview_run_ai_scan"):
        with st.spinner(
            "Scanning eligible ordinary shares against NEPSE regime, sector strength, "
            "trend, momentum, volume, price action, SMC and data-quality gates…"
        ):
            try:
                response = requests.get(
                    f"{API_BASE}/research/signals",
                    params={"limit": int(scan_limit)},
                    timeout=120,
                )
                response.raise_for_status()
                scan_payload = response.json()
                if not isinstance(scan_payload, dict):
                    st.error("The research API returned an unexpected response.")
                else:
                    st.session_state["market_overview_ai_scan"] = scan_payload
            except requests.RequestException as exc:
                st.error(f"AI stock scan failed: {exc}")

    scan_payload = st.session_state.get("market_overview_ai_scan")
    if isinstance(scan_payload, dict):
        scan_rows = scan_payload.get("signals", [])
        scan_rows = scan_rows if isinstance(scan_rows, list) else []
        scan_summary = scan_payload.get("market_context", {})
        scan_summary = scan_summary if isinstance(scan_summary, dict) else {}

        st.caption(
            f"Latest analyzed session: {scan_payload.get('as_of') or 'not available'} · "
            f"Eligible stocks analyzed: {scan_payload.get('stocks_analyzed', 0):,} · "
            f"Prioritized rows returned: {len(scan_rows):,} · "
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
        )
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
            "Run the scan to populate this panel with prioritized stock triggers, directional scores, "
            "risk references and data-freshness checks. No signal scan is run automatically."
        )

    st.divider()

    # Data quality report. The summary endpoint deliberately returns null
    # for avg_trust_score until a report has actually been generated. Never
    # format that null as a number or imply that missing metrics are zero.
    st.subheader("🔍 Data Quality Report")

    if quality_data:
        report_id = quality_data.get("report_id")
        avg_trust_score = quality_data.get("avg_trust_score")
        trust_label = (
            f"{avg_trust_score:.2f}/1.0"
            if isinstance(avg_trust_score, int | float)
            else "N/A"
        )

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Symbols in database", quality_data.get("total_symbols", "N/A"))
        with col2:
            st.metric(
                "Symbols passed",
                quality_data.get("symbols_passed", "N/A") if report_id is not None else "N/A",
            )
        with col3:
            st.metric(
                "Symbols failed",
                quality_data.get("symbols_failed", "N/A") if report_id is not None else "N/A",
            )
        with col4:
            st.metric("Average trust score", trust_label)

        if report_id is None:
            st.info(
                "A daily data-quality report has not been generated yet. "
                "Quality pass rates and average trust scores are therefore unavailable, "
                "not zero. Market prices and screener data are shown separately above."
            )
        else:
            st.caption(
                f"Latest report #{report_id} · "
                f"Report date: {quality_data.get('report_date') or 'not reported'}"
            )
            detail_cols = st.columns(3)
            detail_cols[0].metric(
                "Rejected records", quality_data.get("total_rejected_records", "N/A")
            )
            detail_cols[1].metric(
                "Missing dates", quality_data.get("total_missing_dates", "N/A")
            )
            detail_cols[2].metric(
                "Volume anomalies", quality_data.get("total_volume_anomalies", "N/A")
            )

        # Optional per-symbol details are present only when the API supplies them.
        quality_by_symbol = quality_data.get("quality_by_symbol")
        if isinstance(quality_by_symbol, list) and quality_by_symbol:
            df_quality = pd.DataFrame(quality_by_symbol)
            if not df_quality.empty:
                st.subheader("Quality by Symbol")
                score_cols = [
                    c for c in df_quality.columns if "score" in c.lower() or "rate" in c.lower()
                ]
                col_config = {
                    c: st.column_config.ProgressColumn(
                        c, min_value=0.0, max_value=1.0, format="%.2f"
                    )
                    for c in score_cols
                    if pd.api.types.is_numeric_dtype(df_quality[c])
                    and df_quality[c].dropna().between(0, 1).all()
                }
                st.dataframe(
                    df_quality, use_container_width=True, height=300, column_config=col_config
                )
    else:
        st.warning("Quality report unavailable. Market and screener data remain available above.")

    st.divider()

    st.session_state.setdefault("trust_symbol", "NABIL")
    st.session_state.setdefault("trust_recent", [])
    recent = st.session_state["trust_recent"]
    if recent:
        picked = st.selectbox("Recent lookups", ["—", *recent], key="trust_recent_pick")
        if picked != "—":
            st.session_state["trust_symbol"] = picked

    col1, col2 = st.columns(2)
    with col1:
        lookup_symbol = st.text_input("Symbol", key="trust_symbol")
    with col2:
        _lookup_date = st.date_input("Date", value=dt.date.today(), key="trust_date")
        lookup_date = _lookup_date.isoformat() if isinstance(_lookup_date, dt.date) else ""

    if st.button("Fetch Trust Score", key="fetch_trust"):
        sym = lookup_symbol.strip().upper()
        if not sym:
            st.warning("Enter a symbol before fetching a trust score.")
            return
        if sym in recent:
            recent.remove(sym)
        recent.insert(0, sym)
        del recent[8:]  # keep the 8 most recent
        trust = get_trust_score(sym, lookup_date)
        trust_data = _safe_dict(trust)

        if trust_data:
            col1, col2, col3 = st.columns(3)
            with col1:
                trust_score = trust_data.get("trust_score")
                trust_label = (
                    f"{trust_score:.2f}/1.0"
                    if isinstance(trust_score, int | float)
                    else "N/A"
                )
                st.metric("Trust Score", trust_label)
            with col2:
                status = str(trust_data.get("status", "unknown")).upper()
                color = "🟢" if status == "SAFE" else "🟡" if status == "CAUTION" else "🔴"
                st.metric("Status", f"{color} {status}")
            with col3:
                st.metric("Safe?", "✅ Yes" if trust_data.get("safe") else "❌ No")

            if "components" in trust_data:
                st.caption("**Quality Components:**")
                comp_df = pd.DataFrame([trust_data["components"]])
                st.dataframe(comp_df, use_container_width=True)
        else:
            st.error(
                "Failed to fetch trust score. Check symbol/date and ensure backend is running."
            )
