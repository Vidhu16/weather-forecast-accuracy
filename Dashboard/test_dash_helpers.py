import pandas as pd
import pytest

import dash_helpers as h


def sample():
    return pd.DataFrame({
        "city": ["A", "A", "A", "B"],
        "run_id": [1, 2, 3, 1],
        "target_date": pd.to_datetime(["2026-11-20", "2026-11-20", "2026-11-21", "2026-11-20"]),
        "lead_days": [1, 3, 1, 1],
        "f_tmax": [30.0, 28.0, 31.0, 10.0], "f_tmin": [24.0, 23.0, 25.0, 2.0],
        "a_tmax": [29.0, 29.0, 33.0, 12.0], "a_tmin": [24.0, 24.0, 24.0, 3.0],
    })


def test_error_is_forecast_minus_actual():
    d = h.add_errors(sample())
    assert d["err_tmax"].tolist() == [1.0, -1.0, -2.0, -2.0]


def test_mae_by_lead_values_and_counts():
    d = h.add_errors(sample())
    t = h.mae_by_lead(d, "err_tmax").set_index("lead_days")
    assert t.loc[1, "mae"] == pytest.approx((1 + 2 + 2) / 3)
    assert t.loc[1, "n"] == 3 and t.loc[3, "n"] == 1


def test_bias_sign_positive_means_too_warm():
    d = h.add_errors(sample())
    b = h.bias_by_city(d, "err_tmax").set_index("city")
    assert b.loc["A", "bias"] == pytest.approx((1 - 1 - 2) / 3)
    assert b.loc["B", "bias"] == -2.0


def test_missing_values_are_ignored():
    raw = sample()
    raw.loc[0, "f_tmax"] = None
    d = h.add_errors(raw)
    t = h.mae_by_lead(d, "err_tmax").set_index("lead_days")
    assert t.loc[1, "n"] == 2


def test_filter_by_city_and_date():
    d = h.filter_data(sample(), ["A"], "2026-11-21", "2026-11-22")
    assert len(d) == 1 and d.iloc[0]["city"] == "A"


def test_filter_empty_selection_gives_empty_table():
    assert len(h.filter_data(sample(), [], "2026-11-01", "2026-12-01")) == 0


def test_forecast_vs_actual_uses_latest_run_per_date():
    df = sample()
    df.loc[len(df)] = ["A", 9, pd.Timestamp("2026-11-20"), 1, 99.0, 0.0, 29.0, 24.0]
    t = h.forecast_vs_actual(df, "A", 1, "f_tmax", "a_tmax")
    assert t.loc[pd.Timestamp("2026-11-20"), "Forecast"] == 99.0
    assert list(t.columns) == ["Forecast", "Actual"] and len(t) == 2


def test_freshness_labels():
    assert h.freshness_label(None) == "No data"
    assert h.freshness_label(5) == "Fresh"
    assert h.freshness_label(18) == "Delayed"
    assert h.freshness_label(30) == "Stale"


def test_health_summary_counts_and_last_success():
    runs = pd.DataFrame({
        "started_at": pd.to_datetime(["2026-11-25 06:00", "2026-11-25 12:00", "2026-11-25 18:00", "2026-11-25 19:00"], utc=True),
        "status": ["success", "success", "partial", "failed"]})
    fails = pd.DataFrame({"id": [1, 2]})
    s = h.health_summary(runs, fails, pd.Timestamp("2026-11-25 20:00", tz="UTC"))
    assert s["last_success"] == pd.Timestamp("2026-11-25 12:00", tz="UTC")
    assert s["hours_since_success"] == pytest.approx(8.0)
    assert s["freshness"] == "Fresh"
    assert (s["runs_total"], s["runs_success"], s["runs_partial"], s["runs_failed"]) == (4, 2, 1, 1)
    assert s["api_failures"] == 2


def test_health_summary_with_no_successful_run():
    runs = pd.DataFrame({"started_at": pd.to_datetime(["2026-11-25 06:00"], utc=True), "status": ["failed"]})
    s = h.health_summary(runs, pd.DataFrame({"id": []}), pd.Timestamp("2026-11-25 20:00", tz="UTC"))
    assert s["last_success"] is None and s["freshness"] == "No data"
