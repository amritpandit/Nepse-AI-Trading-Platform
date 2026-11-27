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
    _render_automation_history()
