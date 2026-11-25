from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots

from common import API_BASE, auth_headers, render_hero


def _event_markers(bars: pd.DataFrame) -> list[go.Scatter]:
    """Build visible price-chart markers for confirmed SMC events."""
    marker_specs = [
        ("smc_structure_event", "Structure break", "diamond"),
        ("smc_liquidity_sweep", "Liquidity sweep", "x"),
        ("smc_fvg_direction", "Fair value gap", "circle"),
        ("smc_order_block_direction", "Order-block proxy", "square"),
    ]
    traces: list[go.Scatter] = []
    for column, label, symbol in marker_specs:
        if column not in bars:
            continue
        selected = bars[bars[column].notna()]
        if selected.empty:
            continue
        traces.append(
            go.Scatter(
                x=selected["date"],
                y=selected["high"],
                mode="markers",
                name=label,
                marker={"symbol": symbol, "size": 10},
                text=selected[column].astype(str),
                hovertemplate="%{x|%Y-%m-%d}<br>" + label + ": %{text}<extra></extra>",
            )
        )
    return traces


def _build_chart(bars: pd.DataFrame) -> go.Figure:
    """Candles, moving averages, volatility bands, volume, MACD and RSI."""
    fig = make_subplots(
        rows=6,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.025,
        row_heights=[0.44, 0.12, 0.14, 0.10, 0.10, 0.10],
        subplot_titles=(
            "Price action · EMA/SMA · Bollinger Bands · SMC events",
            "Volume",
            "MACD (12, 26, 9)",
            "RSI (14)",
            "Stochastic (14, 3)",
            "ADX / Directional Index (14)",
        ),
    )

    fig.add_trace(
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

    for column, label in [
        ("ema_20", "EMA 20"),
        ("ema_50", "EMA 50"),
        ("ema_9", "EMA 9"),
        ("ema_21", "EMA 21"),
        ("sma_20", "SMA 20"),
        ("sma_50", "SMA 50"),
        ("sma_200", "SMA 200"),
        ("bb_upper", "BB Upper"),
        ("bb_lower", "BB Lower"),
        ("bollinger_upper", "Bollinger Upper"),
        ("bollinger_lower", "Bollinger Lower"),
    ]:
        if column in bars and bars[column].notna().any():
            fig.add_trace(
                go.Scatter(
                    x=bars["date"],
                    y=bars[column],
                    mode="lines",
                    name=label,
                    line={"width": 1.2},
                ),
                row=1,
                col=1,
            )

    for trace in _event_markers(bars):
        fig.add_trace(trace, row=1, col=1)

    volume_direction = bars["close"] >= bars["open"]
    volume_colors = ["#10B981" if is_up else "#EF4444" for is_up in volume_direction]
    fig.add_trace(
        go.Bar(
            x=bars["date"],
            y=bars["volume"],
            name="Volume",
            marker_color=volume_colors,
            opacity=0.75,
        ),
        row=2,
        col=1,
    )

    if "macd_hist" in bars:
        fig.add_trace(
            go.Bar(x=bars["date"], y=bars["macd_hist"], name="MACD Histogram", opacity=0.5),
            row=3,
            col=1,
        )
    for column, label in [("macd", "MACD"), ("macd_signal", "Signal")]:
        if column in bars:
            fig.add_trace(
                go.Scatter(x=bars["date"], y=bars[column], name=label, mode="lines"),
                row=3,
                col=1,
            )

    if "rsi_14" in bars:
        fig.add_trace(
            go.Scatter(x=bars["date"], y=bars["rsi_14"], name="RSI 14", mode="lines"),
            row=4,
            col=1,
        )
        fig.add_hline(y=70, line_dash="dash", opacity=0.6, row=4, col=1)
        fig.add_hline(y=30, line_dash="dash", opacity=0.6, row=4, col=1)

    for column, label in [("stoch_k_14", "Stochastic %K"), ("stoch_d_3", "Stochastic %D")]:
        if column in bars and bars[column].notna().any():
            fig.add_trace(
                go.Scatter(x=bars["date"], y=bars[column], name=label, mode="lines"),
                row=5,
                col=1,
            )
    if "stoch_k_14" in bars:
        fig.add_hline(y=80, line_dash="dash", opacity=0.5, row=5, col=1)
        fig.add_hline(y=20, line_dash="dash", opacity=0.5, row=5, col=1)

    for column, label in [("adx_14", "ADX 14"), ("plus_di_14", "+DI 14"), ("minus_di_14", "-DI 14")]:
        if column in bars and bars[column].notna().any():
            fig.add_trace(
                go.Scatter(x=bars["date"], y=bars[column], name=label, mode="lines"),
                row=6,
                col=1,
            )

    fig.update_layout(
        height=1250,
        template="plotly_dark+nepse",
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        margin={"l": 20, "r": 20, "t": 80, "b": 20},
        xaxis_rangeslider_visible=False,
    )
    fig.update_yaxes(title_text="Price (NPR)", row=1, col=1)
    fig.update_yaxes(title_text="Shares", row=2, col=1)
    fig.update_yaxes(title_text="MACD", row=3, col=1)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=4, col=1)
    fig.update_yaxes(title_text="Stochastic", range=[0, 100], row=5, col=1)
    fig.update_yaxes(title_text="ADX / DI", range=[0, 100], row=6, col=1)
    return fig


def page_technical_analysis() -> None:
    """Show per-bar technical/SMC history alongside the platform's canonical score."""
    render_hero(
        "Technical Analysis & Smart Money Concepts",
        "Historical indicator evidence and confirmed SMC events, paired with the platform's sector-stock-score-v1 research decision.",
    )
    st.info(
        "NEPSE Index is used only as market context. BUY/SELL triggers are for eligible "
        "listed ordinary shares; SELL is an existing-holding review, not a short-sale instruction. "
        "No broker orders are placed."
    )

    col_symbol, col_date, col_history = st.columns([1.1, 1.1, 1.2])
    with col_symbol:
        symbol = st.text_input("NEPSE symbol", value="NABIL", max_chars=20).strip().upper()
    with col_date:
        as_of = st.date_input("Analyse through", value=date.today())
    with col_history:
        history = st.select_slider("History", options=[60, 90, 120, 180, 250, 365, 500], value=250)

    if st.button("Run technical analysis", type="primary", use_container_width=True):
        if not symbol:
            st.warning("Enter a valid NEPSE symbol.")
            return

        with st.spinner(f"Analysing {symbol} daily OHLCV history…"):
            try:
                response = requests.get(
                    f"{API_BASE}/analysis/technical/{symbol}",
                    params={"as_of": as_of.isoformat(), "limit": int(history)},
                    headers=auth_headers(),
                    timeout=45,
                )
                if response.status_code >= 400:
                    try:
                        detail = response.json().get("detail", response.text)
                    except (ValueError, AttributeError):
                        detail = response.text
                    st.error(f"Analysis unavailable ({response.status_code}): {detail}")
                    if response.status_code == 404:
                        st.info(
                            "Check that the symbol exists in the platform and that historical "
                            "daily OHLCV prices have been imported. No sample or fabricated "
                            "prices are substituted."
                        )
                    return
                payload = response.json()
                if not isinstance(payload, dict):
                    st.error("The API returned an unexpected response.")
                    return
                st.session_state["technical_analysis_payload"] = payload
                st.session_state["technical_analysis_symbol"] = symbol
            except requests.RequestException as exc:
                st.error(f"Could not contact the analysis API: {exc}")
                return

    payload = st.session_state.get("technical_analysis_payload")
    loaded_symbol = st.session_state.get("technical_analysis_symbol")
    if not isinstance(payload, dict) or loaded_symbol != symbol:
        st.info("Select a symbol and run the analysis to load indicators and the chart.")
        return

    research = payload.get("composite_research_model")
    research = research if isinstance(research, dict) else None
    research_status = str(payload.get("composite_research_status", "unavailable"))
    indicators = payload.get("indicators", {})
    indicators = indicators if isinstance(indicators, dict) else {}
    research_quality = research.get("data_quality", {}) if research else {}
    research_quality = research_quality if isinstance(research_quality, dict) else {}
    freshness = research.get("data_freshness", {}) if research else {}
    freshness = freshness if isinstance(freshness, dict) else {}
    market = research.get("market_context", {}) if research else {}
    market = market if isinstance(market, dict) else {}
    sector = research.get("sector_context", {}) if research else {}
    sector = sector if isinstance(sector, dict) else {}

    st.caption(
        f"Symbol: {payload.get('symbol')} · Indicator history through: {payload.get('as_of')} · "
        f"Daily bars: {payload.get('observation_count', 0)} · "
        f"Invalid/duplicate excluded: {payload.get('excluded_or_duplicate_rows', 0)} · "
        f"Pre-discontinuity excluded: {payload.get('excluded_pre_discontinuity_rows', 0)}"
    )

    discontinuity = payload.get("price_discontinuity", {})
    discontinuity = discontinuity if isinstance(discontinuity, dict) else {}
    indicator_quality = payload.get("data_quality", {})
    indicator_quality = indicator_quality if isinstance(indicator_quality, dict) else {}
    if discontinuity.get("flagged"):
        st.warning(
            "A raw-price discontinuity of "
            f"{discontinuity.get('absolute_change_pct')}% was detected on "
            f"{discontinuity.get('event_date')}. Earlier raw observations have been "
            "excluded from the displayed indicator calculations."
        )
    elif indicator_quality.get("warning"):
        st.caption(str(indicator_quality["warning"]))

    if research is not None:
        signal = str(research.get("signal", "HOLD")).upper()
        buy_score = int(research.get("buy_score") or 0)
        sell_score = int(research.get("sell_score") or 0)
        margin = int(research.get("score_margin") or 0)
        market_regime = str(market.get("regime", "unavailable")).upper()
        data_status = str(freshness.get("status", "unknown")).upper()

        score_col, buy_col, sell_col, regime_col, freshness_col = st.columns(5)
        score_col.metric("Research decision", signal)
        buy_col.metric("BUY evidence", f"{buy_score}/100")
        sell_col.metric("SELL evidence", f"{sell_score}/100")
        regime_col.metric("NEPSE regime", market_regime)
        freshness_col.metric("Price freshness", data_status)

        st.caption(
            f"Composite model: {payload.get('composite_research_version', 'unknown')} · "
            f"BUY − SELL evidence margin: {margin:+d} points · "
            f"Latest scored session: {research.get('as_of') or '—'}"
        )
        warnings = research_quality.get("warnings", [])
        if research_quality.get("status") == "warning" or warnings:
            st.warning(
                "Data-quality review is required. " + " ".join(str(item) for item in warnings)
            )
        if data_status == "STALE":
            st.warning(
                "The latest stored stock session is more than seven calendar days old; "
                "the existing research engine suppresses new triggers."
            )
        if research_status != "available":
            st.warning(
                "The composite score is not shown for this historical cut-off because a "
                "current-session score must not be backdated onto older price bars."
            )
        st.info(
            str(payload.get("research_model_note", "The canonical platform research score is shown above."))
            + " Evidence points are not calibrated probabilities or guaranteed returns."
        )

        components = research.get("score_model", {})
        components = components.get("components", {}) if isinstance(components, dict) else {}
        if isinstance(components, dict) and components:
            st.subheader("Canonical score evidence")
            component_rows = []
            for name, item in components.items():
                if not isinstance(item, dict):
                    continue
                component_rows.append(
                    {
                        "Factor": name.replace("_", " ").title(),
                        "Weight": item.get("weight"),
                        "BUY points": item.get("buy_points", 0),
                        "SELL points": item.get("sell_points", 0),
                        "Evidence": item.get("reason") or item.get("method") or item.get("methodology") or "",
                    }
                )
            if component_rows:
                component_frame = pd.DataFrame(component_rows)
                st.dataframe(component_frame, hide_index=True, use_container_width=True)
    elif research_status == "historical_composite_snapshot_not_available":
        st.warning(
            "Historical indicators and SMC events are available for this cut-off, but the "
            "platform does not store a dated historical composite score for that session. "
            "A current BUY/SELL result is intentionally not substituted."
        )
    elif research_status == "benchmark_history_ahead_of_stock_history":
        st.warning(
            "The latest NEPSE Index observation is newer than this stock's latest OHLCV bar. "
            "The composite score is withheld to prevent benchmark look-ahead bias."
        )
    elif research_status == "benchmark_history_behind_stock_history":
        st.warning(
            "The latest stock OHLCV bar is newer than the NEPSE Index benchmark. "
            "The composite score is withheld until both histories are aligned to the same session."
        )
    else:
        st.warning(
            "The canonical composite research score is unavailable. Indicator charts remain "
            "available, but this page will not invent a replacement BUY/SELL score."
        )

    chart_bars = payload.get("bars", [])
    bars = pd.DataFrame(chart_bars if isinstance(chart_bars, list) else [])
    if bars.empty:
        st.warning("No chart bars were returned.")
        return

    bars["date"] = pd.to_datetime(bars["date"], errors="coerce")
    bars = bars.dropna(subset=["date"]).sort_values("date")
    # Add the canonical stock-research overlays where dates match. The new
    # per-bar data remains responsible for event chronology and SMC markers.
    research_bars = research.get("price_history_for_chart", []) if research else []
    if isinstance(research_bars, list) and research_bars:
        extra = pd.DataFrame(research_bars)
        if not extra.empty and "date" in extra:
            extra["date"] = pd.to_datetime(extra["date"], errors="coerce")
            extra = extra.dropna(subset=["date"])
            bars = bars.merge(
                extra.drop(columns=[column for column in ["open", "high", "low", "close", "volume"] if column in extra]),
                on="date",
                how="left",
                suffixes=("", "_research"),
            )
            canonical_columns = [
                "sma_20",
                "sma_50",
                "sma_200",
                "ema_9",
                "ema_21",
                "bollinger_upper",
                "bollinger_lower",
                "rsi_14",
                "macd",
                "macd_signal",
                "atr_14",
                "adx_14",
                "plus_di_14",
                "minus_di_14",
                "stoch_k_14",
                "stoch_d_3",
            ]
            for column in canonical_columns:
                research_column = f"{column}_research"
                if research_column in bars:
                    current = (
                        bars[column]
                        if column in bars
                        else pd.Series(index=bars.index, dtype=float)
                    )
                    bars[column] = bars[research_column].combine_first(current)
            if "macd_histogram_research" in bars:
                bars["macd_hist"] = bars["macd_histogram_research"].combine_first(
                    bars["macd_hist"] if "macd_hist" in bars else pd.Series(index=bars.index, dtype=float)
                )

    st.subheader("Price action, indicators and confirmed SMC events")
    st.plotly_chart(_build_chart(bars), use_container_width=True)

    event_col, detail_col = st.columns([1.1, 1])
    with event_col:
        st.subheader("Recent per-bar SMC events")
        events = payload.get("recent_smc_events", [])
        event_frame = pd.DataFrame(events if isinstance(events, list) else [])
        if not event_frame.empty:
            st.dataframe(event_frame, hide_index=True, use_container_width=True, height=340)
        else:
            st.caption("No confirmed BOS/CHoCH, FVG, liquidity-sweep or order-block events in the displayed history.")
    with detail_col:
        st.subheader("Latest indicators")
        st.dataframe(
            pd.DataFrame(
                [{"Indicator": name.replace("_", " ").upper(), "Latest value": value}
                 for name, value in indicators.items()]
            ),
            hide_index=True,
            use_container_width=True,
            height=340,
        )

    with st.expander("SMC interpretation and methodology"):
        st.write(payload.get("explanation", []))
        st.json(payload.get("methodology", {}))
        st.caption(
            "BOS/CHoCH pivots use a three-left/three-right confirmation window; an event "
            "is published only on the date when the right-side confirmation is complete. "
            "FVG, liquidity sweeps and order blocks are deterministic daily-OHLC proxies, "
            "not verified institutional order-flow data. The platform's BUY/SELL decision "
            "continues to come from sector-stock-score-v1, not from this indicator overlay."
        )
