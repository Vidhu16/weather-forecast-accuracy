"""Forecast accuracy analysis (Part 4).

Questions answered:
  1. Temperature accuracy: MAE for each lead time (1 to 7 days)
  2. Bias: are forecasts too warm or too cold, per city?
  3. Rain: how often was rain correctly predicted, and how often was it a false alarm?
  4. Hardest city to forecast

Terms used (be ready to explain them):
  error = forecast - actual            (positive means the forecast was too warm)
  MAE   = mean of |error|              (the typical size of a miss, in degrees C)
  bias  = mean of error                (the typical direction of a miss)
  rain  = a day with at least RAIN_THRESHOLD_MM of rain, for forecasts and actuals

Usage:
    export DATABASE_URL="postgresql://..."
    python analysis.py
"""
import os

import pandas as pd

RAIN_THRESHOLD_MM = 1.0   # a "wet day"; the same line is used for forecast and actual

QUERY = """
SELECT c.name AS city, f.run_id, f.target_date, f.lead_days,
       f.temp_max AS f_tmax, f.temp_min AS f_tmin,
       f.precipitation AS f_precip, f.precip_prob AS f_prob,
       a.temp_max AS a_tmax, a.temp_min AS a_tmin, a.precipitation AS a_precip
FROM forecasts f
JOIN cities c  ON c.id = f.city_id
JOIN actuals a ON a.city_id = f.city_id AND a.obs_date = f.target_date
WHERE f.lead_days BETWEEN 1 AND 7
"""


# ---------- calculations (pure functions on a DataFrame, easy to test) ----------

def add_errors(df):
    """Add err_tmax and err_tmin columns: forecast minus actual."""
    out = df.copy()
    out["err_tmax"] = out["f_tmax"] - out["a_tmax"]
    out["err_tmin"] = out["f_tmin"] - out["a_tmin"]
    return out


def mae(errors):
    """Mean absolute error of a column of errors. Missing values are ignored."""
    return errors.abs().mean()


def bias(errors):
    """Mean error of a column of errors. Missing values are ignored."""
    return errors.mean()


def mae_by_lead(df, err_col):
    return df.groupby("lead_days")[err_col].apply(mae)


def bias_by_city(df, err_col):
    return df.groupby("city")[err_col].apply(bias)


def accuracy_change(mae_series):
    """How much worse the error is at the longest lead than at the shortest.

    Returns None if fewer than two lead times have data.
    """
    s = mae_series.dropna().sort_index()
    if len(s) < 2:
        return None
    first, last = s.iloc[0], s.iloc[-1]
    return {
        "first_lead": int(s.index[0]), "last_lead": int(s.index[-1]),
        "first_mae": first, "last_mae": last,
        "change": last - first,
        "percent": (last - first) / first * 100 if first else float("nan"),
    }


def rain_table(df, threshold=RAIN_THRESHOLD_MM, by=None):
    """Treat 'will it rain?' as yes/no and count the four outcomes.

      hit              rain forecast, rain happened
      miss             no rain forecast, rain happened
      false alarm      rain forecast, no rain happened
      correct negative no rain forecast, no rain happened

      hit_rate          = hits / (hits + misses)            how often real rain was caught
      false_alarm_ratio = false alarms / (hits + false alarms)   how often a rain forecast was wrong
      accuracy          = (hits + correct negatives) / all days
    """
    d = df.dropna(subset=["f_precip", "a_precip"]).copy()
    d["pred"] = d["f_precip"] >= threshold
    d["real"] = d["a_precip"] >= threshold
    d["hit"] = d["pred"] & d["real"]
    d["miss"] = ~d["pred"] & d["real"]
    d["false_alarm"] = d["pred"] & ~d["real"]
    d["correct_neg"] = ~d["pred"] & ~d["real"]
    if by is None:
        d["all"] = "all"
        by = "all"
    g = d.groupby(by).agg(hits=("hit", "sum"), misses=("miss", "sum"),
                          false_alarms=("false_alarm", "sum"),
                          correct_negatives=("correct_neg", "sum"))
    caught = g["hits"] + g["misses"]
    called = g["hits"] + g["false_alarms"]
    total = caught + g["false_alarms"] + g["correct_negatives"]
    g["hit_rate"] = (g["hits"] / caught).where(caught > 0)
    g["false_alarm_ratio"] = (g["false_alarms"] / called).where(called > 0)
    g["accuracy"] = ((g["hits"] + g["correct_negatives"]) / total).where(total > 0)
    return g


def city_difficulty(df):
    """Rank cities by temperature forecast error, hardest first."""
    d = add_errors(df)
    out = pd.DataFrame({
        "mae_tmax": d.groupby("city")["err_tmax"].apply(mae),
        "mae_tmin": d.groupby("city")["err_tmin"].apply(mae),
    })
    out["mae_avg"] = out[["mae_tmax", "mae_tmin"]].mean(axis=1)
    days = df.drop_duplicates(["city", "target_date"])
    out["actual_tmax_spread"] = days.groupby("city")["a_tmax"].std()   # how much the weather itself varies
    out["rain_day_share"] = days.groupby("city")["a_precip"].apply(
        lambda s: (s >= RAIN_THRESHOLD_MM).mean())
    return out.sort_values("mae_avg", ascending=False)


# ---------- report ----------

def run_report(df):
    d = add_errors(df)
    print("FORECAST ACCURACY REPORT")
    print(f"Forecast-vs-actual pairs: {len(d)}  "
          f"(target dates {d['target_date'].min()} to {d['target_date'].max()})")
    print("error = forecast - actual; positive = forecast too warm\n")

    for label, col in (("1. DAILY MAX TEMPERATURE", "err_tmax"), ("   DAILY MIN TEMPERATURE", "err_tmin")):
        table = pd.DataFrame({"n": d.groupby("lead_days")[col].count(),
                              "MAE_C": mae_by_lead(d, col)}).round(2)
        print(f"{label}: MAE by lead time")
        print(table.to_string())
        change = accuracy_change(mae_by_lead(d, col))
        if change:
            print(f"   MAE goes from {change['first_mae']:.2f} C at {change['first_lead']} day(s) "
                  f"to {change['last_mae']:.2f} C at {change['last_lead']} day(s): "
                  f"{change['change']:+.2f} C ({change['percent']:+.0f}%)")
        else:
            print("   Not enough lead times with data yet to compare.")
        print()

    print("2. BIAS BY CITY (mean error, C)")
    bias_table = pd.DataFrame({"tmax_bias": bias_by_city(d, "err_tmax"),
                               "tmin_bias": bias_by_city(d, "err_tmin")}).round(2)
    print(bias_table.to_string(), "\n")

    print(f"3. RAIN (a rain day = at least {RAIN_THRESHOLD_MM} mm)")
    print(rain_table(df).round(3).to_string())
    print("   by lead time:")
    print(rain_table(df, by="lead_days").round(3).to_string(), "\n")

    print("4. HARDEST CITY (ranked by average MAE of max and min temperature)")
    print(city_difficulty(df).round(2).to_string())


def main():
    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute(QUERY)
    columns = [c[0] for c in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=columns)
    conn.close()
    if df.empty:
        print("No forecast-vs-actual pairs yet. Run fetch_actuals.py first, and wait until "
              "the archive has caught up with your forecast dates (about 5 days).")
        return
    run_report(df)


if __name__ == "__main__":
    main()
