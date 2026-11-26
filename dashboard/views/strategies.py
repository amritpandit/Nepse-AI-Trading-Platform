from __future__ import annotations

import pandas as pd
import requests
import streamlit as st
from common import (
    API_BASE,
    _safe_int,
    _strategy_label,
    _valid_strategies,
    auth_headers,
    load_strategies,
    render_table,
)


STRATEGY_STARTERS = {
    "SMA trend following": {
        "default_name": "NEPSE SMA Trend Following",
        "description": (
            "Enters when the 20-session simple moving average is above the 50-session "
            "average. This is a trend-state rule, not a prediction."
        ),
        "entry_rule": {"rule": "sma_cross_up"},
        "exit_rsi": 70.0,
        "take_profit_pct": 12.0,
        "trailing_stop_pct": 8.0,
    },
    "MACD momentum": {
        "default_name": "NEPSE MACD Momentum",
        "description": (
            "Enters when MACD is above its signal line. This is a momentum-state rule; "
            "it can remain true across multiple sessions."
        ),
        "entry_rule": {"rule": "macd_cross_up"},
        "exit_rsi": 70.0,
        "take_profit_pct": 10.0,
        "trailing_stop_pct": 8.0,
    },
    "RSI mean reversion": {
        "default_name": "NEPSE RSI Mean Reversion",
        "description": (
            "Enters when RSI(14) is below the selected oversold threshold and exits "
            "on recovery, stop-loss, take-profit or trailing stop."
        ),
        "entry_rule": {"rule": "rsi_oversold", "params": {"threshold": 30}},
        "exit_rsi": 60.0,
        "take_profit_pct": 8.0,
        "trailing_stop_pct": 8.0,
    },
}


def _create_strategy_panel() -> None:
    """Create an executable starter strategy using supported backtest rules."""
    with st.expander("＋ Create a strategy", expanded=False):
        template_name = st.selectbox(
            "Starter template",
            list(STRATEGY_STARTERS),
            key="strategy_template_choice",
        )
        template = STRATEGY_STARTERS[template_name]
        st.caption(template["description"])

        with st.form("create_strategy_form"):
            name = st.text_input("Strategy name", value=template["default_name"])
            version = st.text_input("Version", value="1.0.0")
            description = st.text_area(
                "Description",
                value=template["description"],
                height=80,
            )
            symbols_text = st.text_input(
                "NEPSE stock symbols (comma-separated)",
                value="NABIL",
                help=(
                    "Use listed ordinary-share symbols with imported daily OHLCV history. "
                    "Do not enter NEPSE Index as a tradable symbol."
                ),
            )

            st.markdown("**Exit and risk controls**")
            control_cols = st.columns(4)
            with control_cols[0]:
                stop_loss_pct = st.number_input(
                    "Stop loss (%)", min_value=1.0, max_value=30.0, value=5.0, step=0.5
                )
            with control_cols[1]:
                take_profit_pct = st.number_input(
                    "Take profit (%)",
                    min_value=1.0,
                    max_value=100.0,
                    value=float(template["take_profit_pct"]),
                    step=1.0,
                )
            with control_cols[2]:
                trailing_stop_pct = st.number_input(
                    "Trailing stop (%)",
                    min_value=1.0,
                    max_value=30.0,
                    value=float(template["trailing_stop_pct"]),
                    step=0.5,
                )
            with control_cols[3]:
                exit_rsi = st.number_input(
                    "RSI exit threshold",
                    min_value=40.0,
                    max_value=95.0,
                    value=float(template["exit_rsi"]),
                    step=1.0,
                )

            risk_cols = st.columns(3)
            with risk_cols[0]:
                max_position_pct = st.number_input(
                    "Max position (% equity)", min_value=1.0, max_value=50.0, value=10.0, step=1.0
                )
            with risk_cols[1]:
                max_open_positions = st.number_input(
                    "Max open positions", min_value=1, max_value=20, value=5, step=1
                )
            with risk_cols[2]:
                cash_reserve_pct = st.number_input(
                    "Cash reserve (%)", min_value=0.0, max_value=90.0, value=10.0, step=1.0
                )

            submitted = st.form_submit_button("Create strategy", type="primary")

        if submitted:
            symbols = list(
                dict.fromkeys(
                    item.strip().upper()
                    for item in symbols_text.split(",")
                    if item.strip()
                )
            )
            if not name.strip():
                st.warning("Enter a strategy name.")
            elif not symbols:
                st.warning("Enter at least one NEPSE ordinary-share symbol.")
            elif not auth_headers():
                st.warning("Log in from the sidebar before creating a strategy.")
            else:
                starter_config = {
                    "symbols": symbols,
                    "entry_rules": [template["entry_rule"]],
                    "exit_rules": [
                        {"rule": "rsi_exit_long", "params": {"threshold": float(exit_rsi)}},
                        {"rule": "stop_loss", "params": {"stop": float(stop_loss_pct) / 100.0}},
                        {
                            "rule": "take_profit",
                            "params": {"target": float(take_profit_pct) / 100.0},
                        },
                        {
                            "rule": "trailing_stop",
                            "params": {"trail_pct": float(trailing_stop_pct) / 100.0},
                        },
                    ],
                    "risk_rules": [
                        {
                            "rule": "max_position_size",
                            "params": {"max_pct": float(max_position_pct) / 100.0},
                        },
                        {
                            "rule": "single_stock_exposure",
                            "params": {"max_pct": 0.20},
                        },
                        {
                            "rule": "max_open_positions",
                            "params": {"max_count": int(max_open_positions)},
                        },
                        {
                            "rule": "cash_reserve",
                            "params": {"reserve_pct": float(cash_reserve_pct) / 100.0},
                        },
                    ],
                }
                payload = {
                    "name": name.strip(),
                    "version": version.strip() or "1.0.0",
                    "description": description.strip(),
                    "config": starter_config,
                }
                try:
                    response = requests.post(
                        f"{API_BASE}/strategies/",
                        json=payload,
                        headers=auth_headers(),
                        timeout=20,
                    )
                    if response.status_code in (200, 201):
                        created = response.json()
                        st.success(
                            f"Created {created.get('name', payload['name'])} "
                            f"(strategy #{created.get('id', '—')})."
                        )
                        st.rerun()
                    else:
                        try:
                            detail = response.json().get("detail", response.text)
                        except ValueError:
                            detail = response.text
                        st.error(f"Could not create strategy ({response.status_code}): {detail}")
                except requests.RequestException as exc:
                    st.error(f"Could not connect to the strategy API: {exc}")


def page_strategies():
    """Create, inspect and manage executable NEPSE backtest strategies."""  # type: ignore[annotation-unchecked]

    st.header("🎯 Trading Strategies")
    st.caption(
        "Starter templates use the rule types currently supported by the backtest engine. "
        "They are research hypotheses—not proven return guarantees. Backtest them with "
        "NEPSE session data, trading costs and slippage before relying on them."
    )

    strategies = _valid_strategies(load_strategies())
    _create_strategy_panel()

    if not strategies:
        st.info(
            "No strategies have been saved yet. Create a starter strategy above; it will "
            "then become available in the Backtesting page."
        )
        return

    strategy_ids = [_safe_int(strategy.get("id"), -1) for strategy in strategies]

    selected_id = st.selectbox(
        "Select Strategy",
        options=strategy_ids,
        format_func=lambda x: next(
            (_strategy_label(s) for s in strategies if _safe_int(s.get("id"), -1) == x),
            "Unknown",
        ),
        key="strategy_selector",
    )

    strategy = next(
        (s for s in strategies if _safe_int(s.get("id"), -1) == selected_id),
        None,
    )
    if strategy is None:
        st.warning("The selected strategy is no longer available.")
        return

    try:
        response = requests.get(
            f"{API_BASE}/{'' if API_BASE.endswith('/') else ''}strategies/{selected_id}",
            timeout=15,
        )
        response.raise_for_status()
        details = response.json()
    except (requests.RequestException, ValueError):
        details = strategy

    col1, col2, col3 = st.columns(3)
    col1.metric("Strategy ID", strategy.get("id"))
    col2.metric("Version", f"v{strategy.get('version', '0.0.0')}")
    col3.metric("Created", strategy.get("created_at", "N/A"))

    description = details.get("description")
    if description:
        st.write(description)

    st.subheader("⚙️ Executable configuration")
    config = details.get("config")
    if isinstance(config, dict):
        st.json(config)
        st.caption(
            f"Configured symbols: {', '.join(config.get('symbols', [])) or 'none'} · "
            f"Entry rules: {len(config.get('entry_rules', []))} · "
            f"Exit rules: {len(config.get('exit_rules', []))} · "
            f"Risk rules: {len(config.get('risk_rules', []))}"
        )
    else:
        st.warning("Strategy configuration could not be loaded.")

    st.divider()
    st.subheader("📈 Recent Backtests")
    st.info("Backtest history will appear here after you run the strategy from the Backtesting page.")
