from __future__ import annotations

import pandas as pd
import requests
import streamlit as st

from common import API_BASE, load_health_status


def _render_automation_history() -> None:
    """Show persisted market-data import and full-universe scan execution history."""
    st.subheader("⚙️ Automated Market Data & Scan Pipeline")
    st.caption(
        "Execution history is read from the production database. Each import batch and "
        "full-universe scan is recorded with its status, timestamps and processing counts."
    )

    try:
        response = requests.get(
            f"{API_BASE}/research/automation/history",
            params={"limit": 50},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        runs = payload.get("runs", []) if isinstance(payload, dict) else []
        if not isinstance(runs, list):
            runs = []
    except (requests.RequestException, ValueError, TypeError) as exc:
        st.error(f"Could not load automation history: {type(exc).__name__}: {exc}")
        return

    if not runs:
        st.info("No automated pipeline runs have been recorded yet.")
        st.markdown(
            "The scheduled GitHub Actions workflow refreshes market data and scans the "
            "eligible ordinary-share universe using a short-lived GitHub OIDC identity. "
            "No GitHub repository secrets are required; the API verifies the repository, "
            "main branch and exact workflow before allowing automation."
        )
        st.markdown(
            "[Open the NEPSE automated scan workflow](https://github.com/"
            "amritpandit/Nepse-AI-Trading-Platform/actions/workflows/"
            "nepse-automated-scan.yml)"
        )
        return

    success_count = sum(
        1 for run in runs if isinstance(run, dict) and run.get("status") == "success"
    )
    failed_count = sum(
        1 for run in runs if isinstance(run, dict) and run.get("status") == "failed"
    )
    running_count = sum(
        1 for run in runs if isinstance(run, dict) and run.get("status") == "running"
    )

    metric_cols = st.columns(4)
    metric_cols[0].metric("Recorded runs", len(runs))
    metric_cols[1].metric("Successful", success_count)
    metric_cols[2].metric("Failed", failed_count)
    metric_cols[3].metric("Running", running_count)

    latest = runs[0] if isinstance(runs[0], dict) else {}
    st.caption(
        "Latest recorded execution: "
        f"{latest.get('job_type', 'unknown')} · "
        f"{str(latest.get('status', 'unknown')).upper()} · "
        f"{latest.get('started_at') or 'time unavailable'} · "
        f"workflow run {latest.get('run_key') or 'not supplied'}"
    )

    table_rows: list[dict[str, object]] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        summary = run.get("summary", {})
        summary = summary if isinstance(summary, dict) else {}
        signal_counts = summary.get("signal_counts", {})
        signal_counts = signal_counts if isinstance(signal_counts, dict) else {}
        table_rows.append(
            {
                "Run ID": run.get("id"),
                "Job": run.get("job_type"),
                "Status": run.get("status"),
                "Workflow run": run.get("run_key"),
                "Batch offset": run.get("batch_offset"),
                "Started": run.get("started_at"),
                "Completed": run.get("completed_at"),
                "Records processed": run.get("records_processed", 0),
                "Inserted": run.get("records_inserted", 0),
                "Updated": run.get("records_updated", 0),
                "Symbols succeeded": summary.get("symbols_succeeded"),
                "Stocks analyzed": summary.get("stocks_analyzed"),
                "BUY triggers": signal_counts.get("BUY_TRIGGER"),
                "SELL triggers": signal_counts.get("SELL_TRIGGER"),
                "Error": run.get("error_category"),
            }
        )

    st.dataframe(
        pd.DataFrame(table_rows),
        width="stretch",
        hide_index=True,
        height=420,
    )
    st.caption(
        "Import batches and scan jobs are separate audit records. A workflow is complete "
        "only when all requested import batches and the subsequent full-universe scan succeed."
    )


def _render_operational_overview() -> None:
    """Display production archive coverage and forward-testing progress."""
    st.subheader("📊 Production Data & Forward-Test Health")
    try:
        response = requests.get(
            f"{API_BASE}/research/automation/overview",
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("The automation overview returned an unexpected response.")
    except (requests.RequestException, ValueError, TypeError) as exc:
        st.error(f"Could not load production data overview: {type(exc).__name__}: {exc}")
        return

    market_data = payload.get("market_data", {})
    market_data = market_data if isinstance(market_data, dict) else {}
    index_data = payload.get("nepse_index", {})
    index_data = index_data if isinstance(index_data, dict) else {}
    signal_data = payload.get("signals", {})
    signal_data = signal_data if isinstance(signal_data, dict) else {}
    forward_data = payload.get("forward_testing", {})
    forward_data = forward_data if isinstance(forward_data, dict) else {}
    automation_data = payload.get("automation", {})
    automation_data = automation_data if isinstance(automation_data, dict) else {}

    top_cols = st.columns(5)
    top_cols[0].metric(
        "Active ordinary shares",
        f"{market_data.get('active_ordinary_shares', 0):,}",
    )
    top_cols[1].metric(
        "Price-history coverage",
        f"{market_data.get('coverage_pct', 0):.2f}%",
        help=(
            f"{market_data.get('shares_with_price_history', 0):,} covered; "
            f"{market_data.get('shares_without_price_history', 0):,} have no historical rows"
        ),
    )
    top_cols[2].metric(
        "Latest-session coverage",
        f"{market_data.get('latest_session_coverage_pct', 0):.2f}%",
        help=(
            f"{market_data.get('shares_with_latest_session', 0):,} shares have a row on "
            "the latest stored session; "
            f"{market_data.get('shares_not_at_latest_session', 0):,} do not"
        ),
    )
    top_cols[3].metric(
        "Latest stock-price session",
        str(market_data.get("latest_price_date") or "Unavailable"),
        help=(
            f"{market_data.get('latest_price_age_calendar_days')} calendar days "
            "before the dashboard's Nepal-time date"
            if market_data.get("latest_price_age_calendar_days") is not None
            else "No stored ordinary-share price date"
        ),
    )
    top_cols[4].metric(
        "Latest NEPSE index session",
        str(index_data.get("latest_date") or "Unavailable"),
        help=(
            f"{index_data.get('latest_age_calendar_days')} calendar days "
            "before the dashboard's Nepal-time date"
            if index_data.get("latest_age_calendar_days") is not None
            else "No stored NEPSE index date"
        ),
    )

    detail_cols = st.columns(4)
    detail_cols[0].metric("Archived price rows", f"{market_data.get('price_rows', 0):,}")
    detail_cols[1].metric(
        "Saved signal snapshots",
        f"{signal_data.get('snapshot_count', 0):,}",
    )
    detail_cols[2].metric(
        "BUY / SELL triggers",
        f"{signal_data.get('buy_trigger_count', 0):,} / "
        f"{signal_data.get('sell_trigger_count', 0):,}",
    )
    detail_cols[3].metric(
        "Automated pipeline runs",
        f"{automation_data.get('run_count', 0):,}",
    )

    horizons = forward_data.get("horizons", {})
    horizons = horizons if isinstance(horizons, dict) else {}
    forward_rows: list[dict[str, object]] = []
    for horizon in ("1", "5", "10", "20"):
        result = horizons.get(horizon, {})
        result = result if isinstance(result, dict) else {}
        buy = result.get("buy", {})
        sell = result.get("sell", {})
        buy = buy if isinstance(buy, dict) else {}
        sell = sell if isinstance(sell, dict) else {}
        forward_rows.append(
            {
                "Horizon (sessions)": int(horizon),
                "BUY complete": buy.get("complete", 0),
                "BUY pending": buy.get("pending", 0),
                "BUY review": buy.get("review_required", 0),
                "SELL complete": sell.get("complete", 0),
                "SELL pending": sell.get("pending", 0),
                "SELL review": sell.get("review_required", 0),
            }
        )
    st.caption("Forward-testing maturity by signal horizon")
    st.dataframe(
        pd.DataFrame(forward_rows),
        width="stretch",
        hide_index=True,
    )
    if forward_data.get("methodology"):
        st.caption(str(forward_data["methodology"]))
    st.caption(
        "Dates show the latest records stored in the archive, not a guarantee that "
        "the provider has published every recent NEPSE session. Forward tests are "
        "hypothetical research measurements, not guaranteed results."
    )


def page_system_status():
    """System status, API health and automation pipeline monitoring."""  # type: ignore[annotation-unchecked]

    st.header("🖥️ System Status")

    health = load_health_status()
    if health:
        st.json(health)

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Status", str(health.get("status", "unknown")).upper())
        with col2:
            st.metric("Environment", health.get("environment", "unknown"))
    else:
        st.error("System unavailable.")

    st.divider()
    _render_operational_overview()
    st.divider()
    _render_automation_history()
