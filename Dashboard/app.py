"""Weather forecast accuracy dashboard (Streamlit).

Reads the Neon database. The connection string is in Streamlit secrets
(DATABASE_URL) or in the DATABASE_URL environment variable. Never put it in the code.
"""
import os

import pandas as pd
import psycopg2
import streamlit as st

import dash_helpers as h

st.set_page_config(page_title="Forecast accuracy", layout="wide")

PAIRS_SQL = """
SELECT c.name AS city, f.run_id, f.target_date, f.lead_days,
       f.temp_max AS f_tmax, f.temp_min AS f_tmin,
       a.temp_max AS a_tmax, a.temp_min AS a_tmin
FROM forecasts f
JOIN cities c  ON c.id = f.city_id
JOIN actuals a ON a.city_id = f.city_id AND a.obs_date = f.target_date
WHERE f.lead_days BETWEEN 1 AND 7
"""
RUNS_SQL = "SELECT id, started_at, finished_at, status, trigger_type FROM runs ORDER BY started_at DESC"
FAIL_SQL = """
SELECT r.started_at AS run_started, COALESCE(c.name, '-') AS city, f.error_message
FROM api_failures f
JOIN runs r ON r.id = f.run_id
LEFT JOIN cities c ON c.id = f.city_id
ORDER BY f.occurred_at DESC
"""


def database_url():
    try:
        return st.secrets["DATABASE_URL"]
    except Exception:
        return os.environ.get("DATABASE_URL")


@st.cache_data(ttl=300, show_spinner="Loading data...")   # refreshes at most every 5 minutes
def query(sql):
    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set (Streamlit secrets or environment).")
    conn = psycopg2.connect(url)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        columns = [c[0] for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)
    finally:
        conn.close()


st.title("How accurate are weather forecasts?")
st.caption("Open-Meteo 7-day forecasts for 5 cities, collected every 6 hours and compared with what happened. "
           "Error = forecast − actual, so a positive error means the forecast was too warm.")

try:
    pairs = query(PAIRS_SQL)
    runs = query(RUNS_SQL)
    failures = query(FAIL_SQL)
except Exception as exc:
    st.error(f"Could not load data from the database: {type(exc).__name__}. "
             "Check the DATABASE_URL secret and that the Neon project is awake.")
    st.stop()

# ---------------- health panel ----------------
st.header("Pipeline health")
now = pd.Timestamp.now(tz="UTC")
runs["started_at"] = pd.to_datetime(runs["started_at"], utc=True)
health = h.health_summary(runs, failures, now)

c1, c2, c3, c4 = st.columns(4)
if health["last_success"] is not None:
    c1.metric("Last successful run", health["last_success"].strftime("%d %b %H:%M UTC"))
    c2.metric("Data freshness", health["freshness"], f"{health['hours_since_success']:.1f} h since last success",
              delta_color="off")
else:
    c1.metric("Last successful run", "none yet")
    c2.metric("Data freshness", health["freshness"])
c3.metric("Failed or partial runs", health["runs_failed"] + health["runs_partial"],
          f"of {health['runs_total']} runs", delta_color="off")
c4.metric("Failed API calls", health["api_failures"])
if health["freshness"] in ("Delayed", "Stale", "No data"):
    st.warning("Data is not fresh. GitHub scheduled runs are sometimes delayed or skipped; "
               "check the Actions tab of the repository.")

with st.expander("Run history and failed API calls"):
    st.dataframe(runs.head(30), use_container_width=True, hide_index=True)
    if len(failures):
        st.dataframe(failures.head(50), use_container_width=True, hide_index=True)
    else:
        st.write("No failed API calls recorded.")

# ---------------- filters ----------------
st.header("Forecast accuracy")
if pairs.empty:
    st.info("No forecast-vs-actual pairs yet. They appear once forecast target dates have passed "
            "and `fetch_actuals.py` has stored what happened.")
    st.stop()

pairs["target_date"] = pd.to_datetime(pairs["target_date"])
pairs = h.add_errors(pairs)

st.sidebar.header("Filters")
all_cities = sorted(pairs["city"].unique())
cities = st.sidebar.multiselect("Cities", all_cities, default=all_cities)
variable = st.sidebar.selectbox("Variable", list(h.VARIABLES))
f_col, a_col, err_col = h.VARIABLES[variable]
lo, hi = pairs["target_date"].min().date(), pairs["target_date"].max().date()
picked = st.sidebar.date_input("Target dates", value=(lo, hi), min_value=lo, max_value=hi)
start, end = (picked[0], picked[-1]) if isinstance(picked, (tuple, list)) and len(picked) else (lo, hi)

data = h.filter_data(pairs, cities, start, end)
if data.empty:
    st.warning("No data for these filters. Choose at least one city and a wider date range.")
    st.stop()

st.write(f"{len(data):,} forecast-vs-actual pairs selected.")

# ---------------- chart 1: error by lead time ----------------
left, right = st.columns(2)
with left:
    st.subheader("Error by lead time")
    by_lead = h.mae_by_lead(data, err_col)
    st.bar_chart(by_lead.set_index("lead_days")["mae"], x_label="Lead time (days ahead)", y_label="MAE (°C)")
    st.caption("MAE = typical size of a miss. Check the sample sizes below: long leads have fewer forecasts.")
    st.dataframe(by_lead.rename(columns={"lead_days": "Lead (days)", "mae": "MAE (°C)", "n": "Forecasts"}),
                 hide_index=True, use_container_width=True)

# ---------------- chart 3: bias by city ----------------
with right:
    st.subheader("Bias by city")
    by_city = h.bias_by_city(data, err_col)
    st.bar_chart(by_city.set_index("city")["bias"], x_label="City", y_label="Bias (°C)")
    st.caption("Above 0 = forecasts too warm on average. Below 0 = too cold.")
    st.dataframe(by_city.rename(columns={"city": "City", "bias": "Bias (°C)", "n": "Forecasts"}),
                 hide_index=True, use_container_width=True)

# ---------------- chart 2: forecast vs actual over time ----------------
st.subheader("Forecast vs actual over time")
col_a, col_b = st.columns(2)
city = col_a.selectbox("City", cities)
available_leads = sorted(data["lead_days"].unique())
lead = col_b.selectbox("Lead time (days ahead)", available_leads)
series = h.forecast_vs_actual(data, city, lead, f_col, a_col)
if series.empty:
    st.info("No data for this city and lead time in the selected dates.")
else:
    st.line_chart(series, y_label=variable)

st.caption("Limits: 'actual' is Open-Meteo's own analysis of the day (a model estimate, not a weather station), "
           "data collection began on 19 Nov 2026, and GitHub skips some scheduled runs. "
           "Long-lead results are based on few forecasts.")
