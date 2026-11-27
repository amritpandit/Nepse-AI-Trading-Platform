"""Advanced NEPSE stock screener built on the canonical explainable research model."""
from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.express as px
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
        action = item.get("price_action")
        action = action if isinstance(action, dict) else {}

        records.append(
            {
                "Symbol": item.get("symbol"),
                "Company": item.get("name"),
                "Sector": item.get("sector") or "Unclassified",
                "Signal": item.get("signal"),
                "BUY score": _number(item.get("buy_score")),
                "SELL score": _number(item.get("sell_score")),
                "Score margin": _number(item.get("score_margin")),
                "Close (NPR)": _number(indicators.get("close")),
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
            str(row.get("recorded_at") or ""),
        ) >= (
            str(previous.get("date") or ""),
            str(previous.get("recorded_at") or ""),
        ):
            latest_by_symbol[symbol] = row

    latest = list(latest_by_symbol.values())
    latest.sort(key=lambda row: str(row.get("symbol") or ""))
    latest_date = max((str(row.get("as_of")) for row in latest if row.get("as_of")), default=None)
    newest = max(latest, key=lambda row: str(row.get("recorded_at") or ""), default={})
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

    refresh_col, reload_col, status_col = st.columns([1.5, 1.2, 2.3])
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
            30.0,
            (0.0, 30.0),
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

    sort_key = st.selectbox(
        "Rank results by",
        ["BUY score", "Score margin", "SELL score", "Return 20D (%)", "Sector RS vs NEPSE (%)", "Avg traded value (20D)"],
        key="advanced_screener_sort",
    )
    sort_desc = st.checkbox("Highest first", value=True, key="advanced_screener_sort_desc")

    filtered = frame[
        frame["Sector"].isin(chosen_sectors)
        & frame["Signal"].isin(chosen_signals)
        & (pd.to_numeric(frame["BUY score"], errors="coerce").fillna(-1) >= min_buy)
        & (pd.to_numeric(frame["SELL score"], errors="coerce").fillna(-1) >= min_sell)
        & (pd.to_numeric(frame["RSI (14)"], errors="coerce").between(rsi_range[0], rsi_range[1]))
        & (pd.to_numeric(frame["Volume ratio"], errors="coerce").fillna(-1) >= min_volume_ratio)
        & (pd.to_numeric(frame["Avg traded value (20D)"], errors="coerce").fillna(0) >= min_turnover)
        & (pd.to_numeric(frame["ADX (14)"], errors="coerce").fillna(-1) >= min_adx)
        & (pd.to_numeric(frame["Sector RS vs NEPSE (%)"], errors="coerce").fillna(-999) >= min_sector_rs)
        & (pd.to_numeric(frame["ATR (14) %"], errors="coerce").between(atr_range[0], atr_range[1]))
        & (pd.to_numeric(frame["Return 20D (%)"], errors="coerce").fillna(-999) >= min_return_20d)
    ].copy()

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
        "Score margin", "Close (NPR)", "RSI (14)", "ADX (14)", "MACD histogram",
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
            "Select a stock for model evidence",
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
            raw_indicators = selected.get("indicators")
            raw_indicators = raw_indicators if isinstance(raw_indicators, dict) else {}
            raw_smc = selected.get("smart_money_concepts")
            raw_smc = raw_smc if isinstance(raw_smc, dict) else {}
            left, right = st.columns(2)
            with left:
                st.markdown("**Technical confirmation**")
                st.json(selected.get("confirmation_checks", {}))
                st.markdown("**Market and sector gates**")
                st.json(selected.get("confirmation_gates", {}))
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

    st.divider()
    st.caption(
        "Methodology: RSI, MACD, SMA/EMA, Bollinger Bands, ATR, ADX, volume, market regime, "
        "sector relative strength, price action and daily-bar SMC proxies are calculated by "
        "the backend. BOS, liquidity sweeps, FVG and order blocks are rule-based approximations, "
        "not verified institutional order-flow data. Raw prices are not corporate-action adjusted. "
        "Scores are evidence points, not probabilities or guaranteed returns."
    )
