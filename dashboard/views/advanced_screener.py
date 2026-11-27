"""Advanced NEPSE stock screener built on the canonical explainable research model."""
from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

from common import API_BASE, auth_headers


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
        return number if pd.notna(number) else None
    except (TypeError, ValueError):
        return None


def _snapshot_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for item in rows:
        indicators = item.get("indicators")
        indicators = indicators if isinstance(indicators, dict) else {}
        smc = item.get("smart_money_concepts")
        smc = smc if isinstance(smc, dict) else {}
        sector = item.get("sector_context")
        sector = sector if isinstance(sector, dict) else {}
        freshness = item.get("data_freshness")
        freshness = freshness if isinstance(freshness, dict) else {}
        close = _number(indicators.get("close"))
        stop = _number(item.get("reference_stop_level"))
        target = _number(item.get("reference_target_level"))
        risk_pct = (
            abs(close - stop) / close * 100
            if close not in (None, 0) and stop is not None
            else None
        )
        reward_pct = (
            abs(target - close) / close * 100
            if close not in (None, 0) and target is not None
            else None
        )
        reward_risk = (
            reward_pct / risk_pct
            if reward_pct is not None and risk_pct not in (None, 0)
            else None
        )

        records.append(
            {
                "Symbol": item.get("symbol"),
                "Company": item.get("name"),
                "Sector": item.get("sector") or "Unclassified",
                "Signal": item.get("signal"),
                "BUY score": _number(item.get("buy_score")),
                "SELL score": _number(item.get("sell_score")),
                "Score margin": _number(item.get("score_margin")),
                "Close (NPR)": close,
                "Stop reference (NPR)": stop,
                "Target reference (NPR)": target,
                "Risk distance (%)": risk_pct,
                "Reward distance (%)": reward_pct,
                "Reference R/R": reward_risk,
                "RSI (14)": _number(indicators.get("rsi_14")),
                "ADX (14)": _number(indicators.get("adx_14")),
                "MACD histogram": _number(indicators.get("macd_histogram")),
                "Volume ratio": _number(indicators.get("volume_ratio_20d")),
                "Avg traded value (20D)": _number(indicators.get("average_traded_value_20d")),
                "Return 20D (%)": (
                    _number(indicators.get("return_20d")) * 100
                    if _number(indicators.get("return_20d")) is not None
                    else None
                ),
                "Return 60D (%)": (
                    _number(indicators.get("return_60d")) * 100
                    if _number(indicators.get("return_60d")) is not None
                    else None
                ),
                "Sector RS vs NEPSE (%)": (
                    _number(sector.get("relative_strength_vs_nepse_20d")) * 100
                    if _number(sector.get("relative_strength_vs_nepse_20d")) is not None
                    else None
                ),
                "ATR (14) %": (
                    _number(indicators.get("atr_14")) / _number(indicators.get("close")) * 100
                    if _number(indicators.get("atr_14")) is not None
                    and _number(indicators.get("close")) not in (None, 0)
                    else None
                ),
                "Above SMA20": (
                    indicators.get("sma_20") is not None
                    and indicators.get("close") is not None
                    and indicators.get("close") > indicators.get("sma_20")
                ),
                "Above SMA50": (
                    indicators.get("sma_50") is not None
                    and indicators.get("close") is not None
                    and indicators.get("close") > indicators.get("sma_50")
                ),
                "Breakout 20D": bool(indicators.get("breakout_20d")),
                "Bullish BOS": bool(smc.get("bullish_bos")),
                "Bearish BOS": bool(smc.get("bearish_bos")),
                "Bullish sweep": bool(smc.get("bullish_liquidity_sweep")),
                "Bearish sweep": bool(smc.get("bearish_liquidity_sweep")),
                "Bullish OB retest": bool(smc.get("bullish_order_block_retest")),
                "Bearish OB retest": bool(smc.get("bearish_order_block_retest")),
                "Data status": freshness.get("status", "unknown"),
                "As of": item.get("as_of"),
                "_raw": item,
            }
        )
    return pd.DataFrame(records)



def _latest_persisted_snapshot(history: dict[str, Any]) -> dict[str, Any]:
    """Collapse the append/upsert ledger to the latest saved daily snapshot per symbol."""
    raw_rows = history.get("signals", [])
    raw_rows = raw_rows if isinstance(raw_rows, list) else []
    latest_by_symbol: dict[str, dict[str, Any]] = {}

    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        snapshot = item.get("snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        row = dict(item)
        row["as_of"] = item.get("date")
        row["indicators"] = snapshot.get("indicators", {})
        row["smart_money_concepts"] = snapshot.get("smart_money_concepts", {})
        row["price_action"] = snapshot.get("price_action", {})
        row["confirmation_gates"] = snapshot.get("confirmation_gates", {})
        row["confirmation_checks"] = snapshot.get("confirmation_checks", {})
        previous = latest_by_symbol.get(symbol)
        if previous is None or (
            str(row.get("date") or ""),
            str(row.get("updated_at") or row.get("recorded_at") or ""),
            str(row.get("recorded_at") or ""),
        ) >= (
            str(previous.get("date") or ""),
            str(previous.get("updated_at") or previous.get("recorded_at") or ""),
            str(previous.get("recorded_at") or ""),
        ):
            latest_by_symbol[symbol] = row

    latest = list(latest_by_symbol.values())
    latest.sort(key=lambda row: str(row.get("symbol") or ""))
    latest_date = max((str(row.get("as_of")) for row in latest if row.get("as_of")), default=None)
    newest = max(latest, key=lambda row: str(row.get("updated_at") or row.get("recorded_at") or ""), default={})
    return {
        "as_of": latest_date,
        "stocks_analyzed": len(latest),
        "snapshots_saved": history.get("count", len(raw_rows)),
        "market_context": newest.get("market_context", {}),
        "signals": latest,
        "data_disclaimer": (
            "Loaded from the persistent signal ledger. Each displayed row is the latest "
            "saved model snapshot for that symbol."
        ),
    }

def page_advanced_screener() -> None:
    """Interactive multi-factor screener; scoring comes from the backend canonical model."""
    st.header("🔎 Advanced Market Screener")
    st.caption(
        "Rank NEPSE ordinary shares using the platform's canonical technical, sector, "
        "market-regime, price-action and rule-based SMC research model. Filters do not "
        "change model scores and are not trading instructions."
    )

    refresh_col, reload_col = st.columns([1.5, 1.2])
    with refresh_col:
        run_scan = st.button(
            "↻ Run & Record full-universe scan",
            type="primary",
            key="advanced_screener_refresh",
        )
    with reload_col:
        reload_history = st.button("Reload saved history", key="advanced_screener_reload")

    if run_scan:
        if not st.session_state.get("auth_token"):
            st.error("Sign in from the sidebar to run and record a new scan.")
        else:
            with st.spinner(
                "Scanning all eligible ordinary shares, saving snapshots and "
                "refreshing forward-testing outcomes…"
            ):
                try:
                    response = requests.post(
                        f"{API_BASE}/research/signals/refresh",
                        params={"limit": 300},
                        headers=auth_headers(),
                        timeout=240,
                    )
                    response.raise_for_status()
                    scan_result = response.json()
                    if not isinstance(scan_result, dict):
                        raise ValueError("Unexpected response from research API")
                    st.session_state["advanced_screener_last_scan"] = scan_result
                    st.session_state.pop("advanced_screener_payload", None)
                except (requests.RequestException, ValueError) as exc:
                    st.error(f"Recorded screener scan failed: {exc}")

    payload = st.session_state.get("advanced_screener_payload")
    if payload is None or reload_history or run_scan:
        with st.spinner("Loading the latest saved stock snapshots…"):
            try:
                response = requests.get(
                    f"{API_BASE}/research/signals/history",
                    params={"limit": 10000, "triggers_only": False},
                    timeout=60,
                )
                response.raise_for_status()
                history = response.json()
                if not isinstance(history, dict):
                    raise ValueError("Unexpected response from signal history API")
                candidate = _latest_persisted_snapshot(history)
                st.session_state["advanced_screener_payload"] = candidate
                payload = candidate
            except (requests.RequestException, ValueError) as exc:
                st.error(f"Could not load saved screener history: {exc}")
                payload = None

    if not isinstance(payload, dict):
        st.info("No saved scan data is available yet. Sign in and run the first recorded scan, or wait for the scheduled automation to complete its first cycle.")
        return

    rows = payload.get("signals", [])
    rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    if not rows:
        st.warning("The persistent signal ledger has no stock snapshots yet. Run a recorded scan or activate the scheduled pipeline.")
        return

    frame = _snapshot_frame(rows)
    market = payload.get("market_context")
    market = market if isinstance(market, dict) else {}
    metric_cols = st.columns(5)
    metric_cols[0].metric("Stocks analyzed", f"{payload.get('stocks_analyzed', len(frame)):,}")
    metric_cols[1].metric("BUY triggers", int((frame["Signal"] == "BUY_TRIGGER").sum()))
    metric_cols[2].metric("SELL reviews", int((frame["Signal"] == "SELL_TRIGGER").sum()))
    metric_cols[3].metric("NEPSE regime", str(market.get("regime", "unknown")).upper())
    metric_cols[4].metric("Analysis date", str(payload.get("as_of") or "N/A"))

    st.markdown("### Screener filters")
    sectors = sorted(frame["Sector"].dropna().astype(str).unique().tolist())
    available_signals = ["BUY_TRIGGER", "SELL_TRIGGER", "WATCH", "HOLD", "STALE_DATA"]
    f1, f2, f3 = st.columns(3)
    with f1:
        search = st.text_input("Company / symbol contains", key="advanced_screener_search").strip().lower()
        chosen_sectors = st.multiselect("Sectors", sectors, default=sectors, key="advanced_screener_sectors")
    with f2:
        chosen_signals = st.multiselect(
            "Model classification",
            available_signals,
            default=available_signals,
            key="advanced_screener_signals",
        )
        min_buy = st.slider("Minimum BUY score", 0, 100, 0, key="advanced_screener_min_buy")
    with f3:
        min_sell = st.slider("Minimum SELL score", 0, 100, 0, key="advanced_screener_min_sell")
        rsi_range = st.slider("RSI (14) range", 0, 100, (0, 100), key="advanced_screener_rsi")

    g1, g2, g3 = st.columns(3)
    with g1:
        min_volume_ratio = st.slider(
            "Minimum volume ratio (vs 20D avg)", 0.0, 5.0, 0.0, 0.1,
            key="advanced_screener_volume",
        )
        min_turnover = st.number_input(
            "Minimum average traded value, NPR", min_value=0, value=0, step=100000,
            key="advanced_screener_turnover",
        )
    with g2:
        min_adx = st.slider("Minimum ADX (trend strength)", 0, 60, 0, key="advanced_screener_adx")
        min_sector_rs = st.number_input(
            "Minimum sector relative strength vs NEPSE (%)",
            min_value=-100.0,
            max_value=100.0,
            value=-100.0,
            step=1.0,
            key="advanced_screener_sector_rs",
        )
    with g3:
        data_status = st.selectbox(
            "Data freshness",
            ["Any", "current", "stale"],
            key="advanced_screener_freshness",
        )
        smc_filter = st.selectbox(
            "Structure / liquidity evidence",
            [
                "Any",
                "Bullish BOS",
                "Bearish BOS",
                "Bullish liquidity sweep",
                "Bearish liquidity sweep",
                "Bullish order-block retest",
                "Bearish order-block retest",
                "20D breakout",
            ],
            key="advanced_screener_smc",
        )

    h1, h2 = st.columns(2)
    with h1:
        atr_range = st.slider(
            "ATR (14) as % of price — volatility band",
            0.0,
            100.0,
            (0.0, 100.0),
            0.5,
            key="advanced_screener_atr",
        )
    with h2:
        min_return_20d = st.number_input(
            "Minimum 20-session return (%)",
            min_value=-100.0,
            max_value=500.0,
            value=-100.0,
            step=1.0,
            key="advanced_screener_min_return",
        )

    min_reward_risk = st.slider(
        "Minimum reference reward / risk ratio",
        0.0,
        5.0,
        0.0,
        0.25,
        key="advanced_screener_min_rr",
    )

    sort_key = st.selectbox(
        "Rank results by",
        [
            "BUY score",
            "Score margin",
            "SELL score",
            "Reference R/R",
            "Return 20D (%)",
            "Sector RS vs NEPSE (%)",
            "Avg traded value (20D)",
        ],
        key="advanced_screener_sort",
    )
    sort_desc = st.checkbox("Highest first", value=True, key="advanced_screener_sort_desc")

    filtered = frame[
        frame["Sector"].isin(chosen_sectors)
        & frame["Signal"].isin(chosen_signals)
        & (pd.to_numeric(frame["BUY score"], errors="coerce").fillna(-1) >= min_buy)
        & (pd.to_numeric(frame["SELL score"], errors="coerce").fillna(-1) >= min_sell)
        & (pd.to_numeric(frame["Avg traded value (20D)"], errors="coerce").fillna(0) >= min_turnover)
    ].copy()

    # Full-range/default controls must not hide stocks simply because an
    # indicator or setup reference is unavailable (e.g. stale data or HOLD).
    if rsi_range != (0, 100):
        rsi_values = pd.to_numeric(filtered["RSI (14)"], errors="coerce")
        filtered = filtered[rsi_values.between(rsi_range[0], rsi_range[1])]
    if min_volume_ratio > 0:
        volume_values = pd.to_numeric(filtered["Volume ratio"], errors="coerce")
        filtered = filtered[volume_values >= min_volume_ratio]
    if min_adx > 0:
        adx_values = pd.to_numeric(filtered["ADX (14)"], errors="coerce")
        filtered = filtered[adx_values >= min_adx]
    if min_sector_rs > -100:
        sector_rs_values = pd.to_numeric(
            filtered["Sector RS vs NEPSE (%)"], errors="coerce"
        )
        filtered = filtered[sector_rs_values >= min_sector_rs]
    if atr_range != (0.0, 100.0):
        atr_values = pd.to_numeric(filtered["ATR (14) %"], errors="coerce")
        filtered = filtered[atr_values.between(atr_range[0], atr_range[1])]
    if min_return_20d > -100:
        return_values = pd.to_numeric(filtered["Return 20D (%)"], errors="coerce")
        filtered = filtered[return_values >= min_return_20d]
    if min_reward_risk > 0:
        rr_values = pd.to_numeric(filtered["Reference R/R"], errors="coerce")
        filtered = filtered[rr_values >= min_reward_risk]

    if search:
        filtered = filtered[
            filtered["Symbol"].astype(str).str.lower().str.contains(search, na=False)
            | filtered["Company"].astype(str).str.lower().str.contains(search, na=False)
        ]
    if data_status != "Any":
        filtered = filtered[filtered["Data status"] == data_status]
    smc_columns = {
        "Bullish BOS": "Bullish BOS",
        "Bearish BOS": "Bearish BOS",
        "Bullish liquidity sweep": "Bullish sweep",
        "Bearish liquidity sweep": "Bearish sweep",
        "Bullish order-block retest": "Bullish OB retest",
        "Bearish order-block retest": "Bearish OB retest",
        "20D breakout": "Breakout 20D",
    }
    if smc_filter != "Any":
        filtered = filtered[filtered[smc_columns[smc_filter]]]
    filtered = filtered.sort_values(sort_key, ascending=not sort_desc, na_position="last")

    st.markdown("### Ranked candidates")
    st.caption(f"{len(filtered):,} stocks match the selected filters out of {len(frame):,} analyzed.")
    display_columns = [
        "Symbol", "Company", "Sector", "Signal", "BUY score", "SELL score",
        "Score margin", "Close (NPR)", "Stop reference (NPR)", "Target reference (NPR)",
        "Risk distance (%)", "Reward distance (%)", "Reference R/R", "RSI (14)", "ADX (14)", "MACD histogram",
        "Volume ratio", "Avg traded value (20D)", "ATR (14) %", "Return 20D (%)",
        "Sector RS vs NEPSE (%)", "Above SMA20", "Above SMA50",
        "Breakout 20D", "Bullish BOS", "Bearish BOS", "Bullish sweep",
        "Bearish sweep", "Data status", "As of",
    ]
    display = filtered[display_columns].copy()
    st.dataframe(display, use_container_width=True, hide_index=True, height=520)
    st.download_button(
        "📥 Export filtered screener (CSV)",
        data=display.to_csv(index=False).encode("utf-8"),
        file_name=f"nepse_advanced_screener_{payload.get('as_of') or 'latest'}.csv",
        mime="text/csv",
        key="advanced_screener_export",
    )

    if not filtered.empty:
        chart_col1, chart_col2 = st.columns(2)
        with chart_col1:
            score_points = filtered.dropna(subset=["BUY score", "SELL score"])
            if not score_points.empty:
                score_fig = px.scatter(
                    score_points,
                    x="BUY score",
                    y="SELL score",
                    color="Signal",
                    hover_name="Symbol",
                    hover_data=["Company", "Sector", "Score margin", "RSI (14)", "Volume ratio"],
                    title="BUY vs SELL evidence map",
                )
                score_fig.update_layout(height=420, margin={"l": 15, "r": 15, "t": 55, "b": 15})
                st.plotly_chart(score_fig, use_container_width=True)
        with chart_col2:
            leaders = filtered.sort_values("BUY score", ascending=False).head(15).sort_values("BUY score")
            if not leaders.empty:
                leader_fig = px.bar(
                    leaders,
                    x="BUY score",
                    y="Symbol",
                    color="Signal",
                    orientation="h",
                    hover_data=["Sector", "SELL score", "Return 20D (%)"],
                    title="Highest BUY evidence scores",
                )
                leader_fig.update_layout(height=420, margin={"l": 15, "r": 15, "t": 55, "b": 15})
                st.plotly_chart(leader_fig, use_container_width=True)

    if not filtered.empty:
        st.markdown("### Candidate drill-down")
        selected_symbol = st.selectbox(
            "Select a stock for model evidence and chart",
            filtered["Symbol"].astype(str).tolist(),
            key="advanced_screener_detail_symbol",
        )
        selected = next((row for row in rows if row.get("symbol") == selected_symbol), None)
        if isinstance(selected, dict):
            evidence_cols = st.columns(3)
            evidence_cols[0].metric("BUY evidence", f"{selected.get('buy_score', 0)}/100")
            evidence_cols[1].metric("SELL evidence", f"{selected.get('sell_score', 0)}/100")
            evidence_cols[2].metric("Classification", str(selected.get("signal", "HOLD")))
            st.write(selected.get("signal_reason", "No model rationale supplied."))

            detail_key = f"advanced_screener_detail_{selected_symbol}"
            load_detail = st.button(
                "Load full chart, SMC levels and model validation",
                key=f"advanced_screener_load_detail_{selected_symbol}",
            )
            detailed = st.session_state.get(detail_key)
            if load_detail:
                with st.spinner(f"Loading detailed technical history for {selected_symbol}…"):
                    try:
                        detail_response = requests.get(
                            f"{API_BASE}/research/stocks/{selected_symbol}",
                            timeout=90,
                        )
                        detail_response.raise_for_status()
                        detail_candidate = detail_response.json()
                        if not isinstance(detail_candidate, dict):
                            raise ValueError("Unexpected detailed research response")
                        st.session_state[detail_key] = detail_candidate
                        detailed = detail_candidate
                    except (requests.RequestException, ValueError) as exc:
                        st.error(f"Could not load detailed stock research: {exc}")
                        detailed = None

            if not isinstance(detailed, dict):
                st.info("Load the detailed chart to inspect historical price action and model evidence.")

            chart_rows = detailed.get("price_history_for_chart", []) if isinstance(detailed, dict) else []
            if isinstance(chart_rows, list) and chart_rows:
                bars = pd.DataFrame(chart_rows)
                required = {"date", "open", "high", "low", "close", "volume"}
                if required.issubset(bars.columns):
                    for column in required - {"date"}:
                        bars[column] = pd.to_numeric(bars[column], errors="coerce")
                    bars["date"] = pd.to_datetime(bars["date"], errors="coerce")
                    bars = bars.dropna(subset=["date", "open", "high", "low", "close"])
                    if not bars.empty:
                        chart = make_subplots(
                            rows=5,
                            cols=1,
                            shared_xaxes=True,
                            vertical_spacing=0.025,
                            row_heights=[0.48, 0.13, 0.14, 0.13, 0.12],
                            subplot_titles=(
                                f"{selected_symbol} · Daily OHLC with moving averages and Bollinger Bands",
                                "Volume",
                                "MACD (12, 26, 9)",
                                "RSI (14)",
                                "ADX / Directional Index (14)",
                            ),
                        )
                        chart.add_trace(
                            go.Candlestick(
                                x=bars["date"],
                                open=bars["open"],
                                high=bars["high"],
                                low=bars["low"],
                                close=bars["close"],
                                name="OHLC",
                            ),
                            row=1,
                            col=1,
                        )
                        for column, label in (
                            ("ema_9", "EMA 9"),
                            ("ema_21", "EMA 21"),
                            ("sma_20", "SMA 20"),
                            ("sma_50", "SMA 50"),
                            ("sma_200", "SMA 200"),
                            ("bollinger_upper", "BB Upper"),
                            ("bollinger_lower", "BB Lower"),
                        ):
                            if column in bars and bars[column].notna().any():
                                chart.add_trace(
                                    go.Scatter(
                                        x=bars["date"],
                                        y=bars[column],
                                        name=label,
                                        mode="lines",
                                        line={"width": 1},
                                    ),
                                    row=1,
                                    col=1,
                                )
                        volume_colors = [
                            "#10B981" if close >= open_price else "#EF4444"
                            for open_price, close in zip(bars["open"], bars["close"], strict=True)
                        ]
                        chart.add_trace(
                            go.Bar(
                                x=bars["date"],
                                y=bars["volume"],
                                name="Volume",
                                marker_color=volume_colors,
                                opacity=0.7,
                            ),
                            row=2,
                            col=1,
                        )
                        if "macd_histogram" in bars:
                            chart.add_trace(
                                go.Bar(
                                    x=bars["date"],
                                    y=bars["macd_histogram"],
                                    name="MACD histogram",
                                    opacity=0.5,
                                ),
                                row=3,
                                col=1,
                            )
                        for column, label in (("macd", "MACD"), ("macd_signal", "Signal")):
                            if column in bars:
                                chart.add_trace(
                                    go.Scatter(x=bars["date"], y=bars[column], name=label),
                                    row=3,
                                    col=1,
                                )
                        if "rsi_14" in bars:
                            chart.add_trace(
                                go.Scatter(x=bars["date"], y=bars["rsi_14"], name="RSI 14"),
                                row=4,
                                col=1,
                            )
                            chart.add_hline(y=70, line_dash="dash", opacity=0.5, row=4, col=1)
                            chart.add_hline(y=30, line_dash="dash", opacity=0.5, row=4, col=1)
                        for column, label in (
                            ("adx_14", "ADX 14"),
                            ("plus_di_14", "+DI 14"),
                            ("minus_di_14", "-DI 14"),
                        ):
                            if column in bars and bars[column].notna().any():
                                chart.add_trace(
                                    go.Scatter(x=bars["date"], y=bars[column], name=label),
                                    row=5,
                                    col=1,
                                )
                        if isinstance(detailed, dict):
                            smc_detail = detailed.get("smart_money_concepts")
                            smc_detail = smc_detail if isinstance(smc_detail, dict) else {}
                            for zone_key, zone_label in (
                                ("bullish_order_block_proxy", "Bullish order-block proxy"),
                                ("bearish_order_block_proxy", "Bearish order-block proxy"),
                                ("bullish_fvg_zone", "Bullish FVG proxy"),
                                ("bearish_fvg_zone", "Bearish FVG proxy"),
                            ):
                                zone = smc_detail.get(zone_key)
                                if isinstance(zone, dict) and zone.get("low") is not None and zone.get("high") is not None:
                                    chart.add_hrect(
                                        y0=float(zone["low"]),
                                        y1=float(zone["high"]),
                                        row=1,
                                        col=1,
                                        opacity=0.12,
                                        annotation_text=zone_label,
                                        annotation_position="top left",
                                    )
                        chart.update_layout(
                            height=1120,
                            template="plotly_dark",
                            hovermode="x unified",
                            legend={"orientation": "h", "y": 1.02, "x": 0},
                            margin={"l": 15, "r": 15, "t": 85, "b": 20},
                            xaxis_rangeslider_visible=False,
                        )
                        chart.update_yaxes(title_text="NPR", row=1, col=1)
                        chart.update_yaxes(title_text="Volume", row=2, col=1)
                        chart.update_yaxes(title_text="MACD", row=3, col=1)
                        chart.update_yaxes(title_text="RSI", range=[0, 100], row=4, col=1)
                        chart.update_yaxes(title_text="ADX / DI", range=[0, 100], row=5, col=1)
                        st.plotly_chart(chart, use_container_width=True)
            elif isinstance(detailed, dict):
                st.info("No clean daily chart history is available for this stock.")

            if isinstance(detailed, dict):
                raw_indicators = detailed.get("indicators")
                raw_indicators = raw_indicators if isinstance(raw_indicators, dict) else {}
                raw_smc = detailed.get("smart_money_concepts")
                raw_smc = raw_smc if isinstance(raw_smc, dict) else {}
                left, right = st.columns(2)
                with left:
                    st.markdown("**Technical confirmation**")
                    st.json(detailed.get("confirmation_checks", {}))
                    st.markdown("**Market and sector gates**")
                    st.json(detailed.get("confirmation_gates", {}))
                with right:
                    st.markdown("**Smart Money Concepts evidence**")
                    st.json(raw_smc)
                    st.markdown("**Key indicators**")
                    st.dataframe(
                        pd.DataFrame(
                            [{"Indicator": key, "Value": value} for key, value in raw_indicators.items()]
                        ),
                        hide_index=True,
                        use_container_width=True,
                        height=320,
                    )

                score_model = detailed.get("score_model")
                score_model = score_model if isinstance(score_model, dict) else {}
                components = score_model.get("components")
                if isinstance(components, dict) and components:
                    component_rows = [
                        {
                            "Evidence component": name,
                            "Weight": component.get("weight"),
                            "BUY points": component.get("buy_points"),
                            "SELL points": component.get("sell_points"),
                            "Evidence": str(component.get("reason") or component.get("method") or ""),
                        }
                        for name, component in components.items()
                        if isinstance(component, dict)
                    ]
                    st.markdown("**Explainable score composition**")
                    st.dataframe(
                        pd.DataFrame(component_rows),
                        hide_index=True,
                        use_container_width=True,
                    )
                validation = detailed.get("historical_score_validation")
                if isinstance(validation, dict):
                    with st.expander("Chronological historical score validation"):
                        st.json(validation)

    st.divider()
    st.caption(
        "Methodology: RSI, MACD, SMA/EMA, Bollinger Bands, ATR, ADX, volume, market regime, "
        "sector relative strength, price action and daily-bar SMC proxies are calculated by "
        "the backend. BOS, liquidity sweeps, FVG and order blocks are rule-based approximations, "
        "not verified institutional order-flow data. Raw prices are not corporate-action adjusted. "
        "Scores are evidence points, not probabilities or guaranteed returns."
    )
