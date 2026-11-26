"""Automated tests for the key calculations.

Run with:  python -m pytest -v
Each test uses a tiny table where the right answer can be worked out by hand.
"""
from datetime import date

import pandas as pd
import pytest

import analysis
import fetch_actuals


def make(rows):
    """rows: (city, lead, f_tmax, a_tmax, f_tmin, a_tmin, f_precip, a_precip)"""
    return pd.DataFrame(
        [dict(city=c, run_id=1, target_date=date(2026, 11, 20), lead_days=l,
              f_tmax=ft, a_tmax=at, f_tmin=fn, a_tmin=an, f_prob=None,
              f_precip=fp, a_precip=ap)
         for c, l, ft, at, fn, an, fp, ap in rows])


def test_error_is_forecast_minus_actual():
    df = analysis.add_errors(make([("A", 1, 30, 28, 20, 21, 0, 0)]))
    assert df["err_tmax"].iloc[0] == 2     # too warm
    assert df["err_tmin"].iloc[0] == -1    # too cold


def test_mae_and_bias_by_hand():
    # errors: +1, +2, -2  ->  MAE = 5/3, bias = 1/3
    errors = pd.Series([1.0, 2.0, -2.0])
    assert analysis.mae(errors) == pytest.approx(5 / 3)
    assert analysis.bias(errors) == pytest.approx(1 / 3)


def test_mae_ignores_missing_values():
    errors = pd.Series([1.0, None, -3.0])
    assert analysis.mae(errors) == pytest.approx(2.0)


def test_mae_by_lead_groups_correctly():
    df = analysis.add_errors(make([
        ("A", 1, 30, 29, 0, 0, 0, 0),   # error +1
        ("A", 1, 28, 30, 0, 0, 0, 0),   # error -2
        ("A", 2, 33, 30, 0, 0, 0, 0),   # error +3
    ]))
    result = analysis.mae_by_lead(df, "err_tmax")
    assert result[1] == pytest.approx(1.5)
    assert result[2] == pytest.approx(3.0)


def test_accuracy_change_between_first_and_last_lead():
    change = analysis.accuracy_change(pd.Series({1: 1.0, 4: 2.0, 7: 3.0}))
    assert change["change"] == pytest.approx(2.0)
    assert change["percent"] == pytest.approx(200.0)
    assert (change["first_lead"], change["last_lead"]) == (1, 7)


def test_accuracy_change_needs_two_leads():
    assert analysis.accuracy_change(pd.Series({1: 1.0})) is None


def test_bias_sign_warm_and_cold_cities():
    df = analysis.add_errors(make([
        ("Warm", 1, 31, 29, 0, 0, 0, 0), ("Warm", 1, 30, 29, 0, 0, 0, 0),
        ("Cold", 1, 25, 28, 0, 0, 0, 0),
    ]))
    result = analysis.bias_by_city(df, "err_tmax")
    assert result["Warm"] == pytest.approx(1.5)    # consistently too warm
    assert result["Cold"] == pytest.approx(-3.0)   # consistently too cold


def test_rain_outcomes_by_hand():
    df = make([
        ("A", 1, 0, 0, 0, 0, 5.0, 3.0),   # hit
        ("A", 1, 0, 0, 0, 0, 0.0, 2.0),   # miss
        ("A", 1, 0, 0, 0, 0, 3.0, 0.0),   # false alarm
        ("A", 1, 0, 0, 0, 0, 0.0, 0.0),   # correct negative
    ])
    r = analysis.rain_table(df).iloc[0]
    assert (r["hits"], r["misses"], r["false_alarms"], r["correct_negatives"]) == (1, 1, 1, 1)
    assert r["hit_rate"] == pytest.approx(0.5)
    assert r["false_alarm_ratio"] == pytest.approx(0.5)
    assert r["accuracy"] == pytest.approx(0.5)


def test_rain_threshold_counts_exactly_one_mm_as_rain():
    df = make([("A", 1, 0, 0, 0, 0, 1.0, 1.0), ("A", 1, 0, 0, 0, 0, 0.9, 0.9)])
    r = analysis.rain_table(df).iloc[0]
    assert r["hits"] == 1 and r["correct_negatives"] == 1


def test_rain_rates_are_missing_when_there_is_nothing_to_divide():
    df = make([("A", 1, 0, 0, 0, 0, 0.0, 0.0)])     # no rain at all
    r = analysis.rain_table(df).iloc[0]
    assert pd.isna(r["hit_rate"]) and pd.isna(r["false_alarm_ratio"])


def test_rain_ignores_rows_with_missing_precipitation():
    df = make([("A", 1, 0, 0, 0, 0, None, 3.0), ("A", 1, 0, 0, 0, 0, 2.0, 2.0)])
    r = analysis.rain_table(df).iloc[0]
    assert r["hits"] == 1 and r["misses"] == 0


def test_hardest_city_has_largest_error():
    df = make([
        ("Easy", 1, 30, 30, 20, 20, 0, 0), ("Easy", 1, 30, 31, 20, 21, 0, 0),
        ("Hard", 1, 30, 36, 20, 26, 0, 0), ("Hard", 1, 30, 24, 20, 14, 0, 0),
    ])
    ranking = analysis.city_difficulty(df)
    assert ranking.index[0] == "Hard"


def test_parse_daily_skips_days_the_archive_has_not_reached():
    payload = {"daily": {
        "time": ["2026-11-19", "2026-11-20"],
        "temperature_2m_max": [31.2, None], "temperature_2m_min": [24.0, None],
        "precipitation_sum": [0.4, None]}}
    assert fetch_actuals.parse_daily(payload) == [(date(2026, 11, 19), 31.2, 24.0, 0.4)]


def test_date_window_keeps_only_days_that_have_ended():
    today = date(2026, 11, 26)
    assert fetch_actuals.date_window(date(2026, 11, 19), today) == (date(2026, 11, 19), date(2026, 11, 25))
    assert fetch_actuals.date_window(date(2026, 11, 26), today) is None   # today is not over yet
