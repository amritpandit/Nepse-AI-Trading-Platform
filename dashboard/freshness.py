"""Pure data-freshness helpers shared by the Streamlit screener and tests."""

import pandas as pd


def summarize_snapshot_freshness(
    frame: pd.DataFrame,
) -> tuple[str | None, int, pd.DataFrame]:
    """Return the latest saved session, matching-row count, and older/missing rows."""
    date_values = frame["As of"] if "As of" in frame else pd.Series(index=frame.index, dtype="object")
    parsed_dates = pd.to_datetime(date_values, errors="coerce")
    latest_value = parsed_dates.max()

    if pd.isna(latest_value):
        latest_session = None
        is_latest = pd.Series(False, index=frame.index, dtype=bool)
    else:
        latest_session = pd.Timestamp(latest_value).date().isoformat()
        is_latest = parsed_dates.eq(latest_value)

    exception_columns = [
        column
        for column in ("Symbol", "Company", "Signal", "Data status", "As of")
        if column in frame.columns
    ]
    exceptions = frame.loc[~is_latest, exception_columns].copy()
    if "As of" in exceptions.columns:
        exceptions = exceptions.sort_values("As of", ascending=True, na_position="first")

    return latest_session, int(is_latest.sum()), exceptions
