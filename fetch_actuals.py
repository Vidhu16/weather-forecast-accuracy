"""Save the actual daily weather for every city in the actuals table.

Source: the Open-Meteo historical weather API (ERA5 reanalysis). It is a
model-based estimate of what happened, not a weather-station reading, and it
trails real time by about 5 days. Only days not already saved are added, so
running it twice is safe.

Usage:
    export DATABASE_URL="postgresql://..."
    python fetch_actuals.py
"""
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DELAY_DAYS = 5          # the archive lags real time by about this many days
MAX_ATTEMPTS = 3
RETRY_WAIT_SECONDS = 10
TIMEOUT_SECONDS = 60
CITY_PAUSE_SECONDS = 3


# ---------- pure functions (no network, easy to test) ----------

def parse_daily(payload):
    """Turn an API answer into [(date, temp_max, temp_min, precipitation), ...].

    Days where every value is missing are skipped: the archive has not caught up.
    """
    daily = payload["daily"]
    rows = []
    for i, day in enumerate(daily["time"]):
        values = (daily["temperature_2m_max"][i],
                  daily["temperature_2m_min"][i],
                  daily["precipitation_sum"][i])
        if all(v is None for v in values):
            continue
        rows.append((date.fromisoformat(day),) + values)
    return rows


def date_window(first_target, today, delay_days=DELAY_DAYS):
    """First and last day to request, or None if the archive has nothing yet."""
    end = today - timedelta(days=delay_days)
    if first_target > end:
        return None
    return first_target, end


# ---------- network and database ----------

def fetch_city(lat, lon, start, end):
    import requests
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "auto",
    }
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(ARCHIVE_URL, params=params, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            last_error = exc
            print(f"  attempt {attempt}/{MAX_ATTEMPTS} failed: {type(exc).__name__}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_WAIT_SECONDS * attempt)
    raise last_error


def main():
    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    today = datetime.now(timezone.utc).date()

    cur.execute("SELECT id, name, latitude, longitude FROM cities ORDER BY id")
    cities = cur.fetchall()

    failures = 0
    for index, (city_id, name, lat, lon) in enumerate(cities):
        if index > 0:
            time.sleep(CITY_PAUSE_SECONDS)
        try:
            cur.execute("SELECT MIN(target_date) FROM forecasts WHERE city_id = %s", (city_id,))
            first_target = cur.fetchone()[0]
            window = date_window(first_target, today) if first_target else None
            if window is None:
                print(f"SKIP  {name}: no forecast days old enough yet")
                continue
            rows = parse_daily(fetch_city(lat, lon, *window))
            added = 0
            for obs_date, tmax, tmin, precip in rows:
                cur.execute(
                    "INSERT INTO actuals (city_id, obs_date, temp_max, temp_min, precipitation) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (city_id, obs_date) DO NOTHING",
                    (city_id, obs_date, tmax, tmin, precip))
                added += cur.rowcount
            conn.commit()
            print(f"OK    {name}: {added} new days ({window[0]} to {window[1]})")
        except Exception as exc:
            conn.rollback()
            failures += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")

    conn.close()
    if failures == len(cities):
        sys.exit(1)


if __name__ == "__main__":
    main()
