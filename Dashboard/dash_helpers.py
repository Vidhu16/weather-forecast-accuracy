"""Calculations for the dashboard. Pure functions on DataFrames, so they can be tested
without Streamlit or a database.

error = forecast - actual (positive = forecast too warm). MAE = mean |error|. bias = mean error.
"""
import pandas as pd

VARIABLES = {
    "Daily max temperature (°C)": ("f_tmax", "a_tmax", "err_tmax"),
    "Daily min temperature (°C)": ("f_tmin", "a_tmin", "err_tmin"),
}

FRESH_HOURS = 12     # scheduled runs are every 6 h, but GitHub often delays or skips some
STALE_HOURS = 24


def add_errors(df):
    out = df.copy()
    out["err_tmax"] = out["f_tmax"] - out["a_tmax"]
    out["err_tmin"] = out["f_tmin"] - out["a_tmin"]
    return out


def filter_data(df, cities, start, end):
    """Keep chosen cities and target dates start..end (inclusive)."""
    d = df[df["city"].isin(cities)]
    d = d[(d["target_date"] >= pd.Timestamp(start)) & (d["target_date"] <= pd.Timestamp(end))]
    return d


def mae_by_lead(df, err_col):
    """Table with lead_days, mae and n (how many forecasts it is based on)."""
    g = df.dropna(subset=[err_col]).groupby("lead_days")[err_col]
    out = pd.DataFrame({"mae": g.apply(lambda s: s.abs().mean()), "n": g.size()})
    return out.reset_index()


def bias_by_city(df, err_col):
    g = df.dropna(subset=[err_col]).groupby("city")[err_col]
    out = pd.DataFrame({"bias": g.mean(), "n": g.size()})
    return out.reset_index()


def forecast_vs_actual(df, city, lead, f_col, a_col):
    """One city: what was forecast `lead` days ahead vs what happened, per target date.

    If several runs forecast the same date at the same lead (4 per day), the latest run is used.
    """
    d = df[(df["city"] == city) & (df["lead_days"] == lead)]
    d = d.sort_values("run_id").drop_duplicates("target_date", keep="last").sort_values("target_date")
    return d.set_index("target_date")[[f_col, a_col]].rename(
        columns={f_col: "Forecast", a_col: "Actual"})


def freshness_label(hours):
    if hours is None:
        return "No data"
    if hours <= FRESH_HOURS:
        return "Fresh"
    if hours <= STALE_HOURS:
        return "Delayed"
    return "Stale"


def health_summary(runs, failures, now):
    """runs: DataFrame (id, started_at, finished_at, status, trigger_type).
    failures: DataFrame of api_failures. now: timezone-aware Timestamp.
    """
    ok = runs[runs["status"] == "success"]
    last_success = ok["started_at"].max() if len(ok) else None
    hours = None
    if last_success is not None and pd.notna(last_success):
        hours = (now - last_success).total_seconds() / 3600
    else:
        last_success = None
    return {
        "last_success": last_success,
        "hours_since_success": hours,
        "freshness": freshness_label(hours),
        "runs_total": int(len(runs)),
        "runs_success": int((runs["status"] == "success").sum()),
        "runs_partial": int((runs["status"] == "partial").sum()),
        "runs_failed": int((runs["status"] == "failed").sum()),
        "runs_stuck": int((runs["status"] == "started").sum()),
        "api_failures": int(len(failures)),
    }
