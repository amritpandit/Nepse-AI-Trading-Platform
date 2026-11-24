from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from common import API_BASE, auth_headers, render_hero
from plotly.subplots import make_subplots


def _event_markers(bars: pd.DataFrame) -> list[go.Scatter]:
    """Build visible price-chart markers for confirmed SMC events."""
    marker_specs = [
        ("smc_structure_event", "Structure break", "diamond"),
        ("smc_liquidity_sweep", "Liquidity sweep", "x"),
        ("smc_fvg_direction", "Fair value gap", "circle"),
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
        rows=4,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.035,
        row_heights=[0.52, 0.16, 0.18, 0.14],
        subplot_titles=(
            "Price action · EMA/SMA · Bollinger Bands · SMC events",
            "Volume",
            "MACD (12, 26, 9)",
            "RSI (14)",
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
        ("sma_50", "SMA 50"),
        ("bb_upper", "BB Upper"),
        ("bb_lower", "BB Lower"),
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

    fig.update_layout(
        height=900,
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
    return fig


def page_technical_analysis() -> None:
    """Interactive, data-backed technical and Smart Money Concepts research."""
    render_hero(
        "Technical Analysis & Smart Money Concepts",
        "Causal daily indicators, market structure and transparent advisory scoring. Research only; not an execution signal.",
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
                    timeout=30,
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
            except requests.RequestException as exc:
                st.error(f"Could not contact the analysis API: {exc}")
                return

    payload = st.session_state.get("technical_analysis_payload")
    if not isinstance(payload, dict):
        st.info("Select a symbol and run the analysis to load indicators and the chart.")
        return

    if payload.get("symbol") != symbol:
        st.info("The displayed analysis belongs to a previously selected symbol. Run analysis to refresh it.")
        return

    score = float(payload.get("technical_score", 0.0))
    signal = str(payload.get("signal", "HOLD")).upper()
    eligible = bool(payload.get("signal_eligible", False))
    quality = payload.get("data_quality", {})
    quality = quality if isinstance(quality, dict) else {}
    quality_passed = bool(quality.get("passed", False))

    score_col, signal_col, data_col, quality_col = st.columns(4)
    score_col.metric("Technical score", f"{score:.1f}/100")
    signal_col.metric("Advisory", signal)
    data_col.metric("Daily observations", int(payload.get("observation_count", 0)))
    quality_col.metric(
        "Data quality",
        "PASS" if quality_passed else "UNVERIFIED",
        help=f"Trust score: {quality.get('trust_score')}; threshold: 0.70",
    )

    st.caption(
        f"Symbol: {payload.get('symbol')} · As of: {payload.get('as_of')} · "
        f"History exclusions/duplicates: {payload.get('excluded_or_duplicate_rows', 0)}"
    )
    if not quality_passed:
        st.warning(
            "The latest bar has no existing passing data-trust record (minimum 0.70). "
            "The platform has therefore forced the advisory to HOLD. Run the established "
            "data-quality workflow and reanalyse; this page does not write trust records."
        )
    elif not eligible:
        st.warning("Insufficient daily history for an eligible advisory.")
    else:
        st.info(
            "The displayed BUY/SELL/HOLD is a research classification, not a calibrated "
            "probability, financial advice or an instruction to place an order."
        )

    chart_bars = payload.get("bars", [])
    bars = pd.DataFrame(chart_bars if isinstance(chart_bars, list) else [])
    if bars.empty:
        st.warning("No chart bars were returned.")
        return

    bars["date"] = pd.to_datetime(bars["date"], errors="coerce")
    bars = bars.dropna(subset=["date"]).sort_values("date")
    st.plotly_chart(_build_chart(bars), use_container_width=True)

    component_col, event_col = st.columns([1, 1])
    with component_col:
        st.subheader("Score contribution")
        components = payload.get("score_components", {})
        if isinstance(components, dict) and components:
            component_frame = pd.DataFrame(
                [{"Component": name.replace("_", " ").title(), "Points": value}
                 for name, value in components.items()]
            )
            st.dataframe(component_frame, hide_index=True, use_container_width=True)
    with event_col:
        st.subheader("Recent SMC events")
        events = payload.get("recent_smc_events", [])
        event_frame = pd.DataFrame(events if isinstance(events, list) else [])
        if not event_frame.empty:
            st.dataframe(event_frame, hide_index=True, use_container_width=True, height=300)
        else:
            st.caption("No confirmed SMC events in the displayed lookback.")

    with st.expander("Indicator values and methodology"):
        st.json(payload.get("indicators", {}))
        st.write(payload.get("explanation", []))
        st.json(payload.get("methodology", {}))
        st.caption(
            "SMC labels are rule-based approximations. A BOS/CHoCH requires a confirmed "
            "three-bar swing pivot; the event is dated only when confirmation is available. "
            "The score is heuristic and must be validated with out-of-sample, cost-aware "
            "backtests before any strategy promotion."
        )
