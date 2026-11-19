"""Collect 7-day forecasts and current observed weather for every city.

Each run is saved with its own run id and timestamp. Old data is never
overwritten. Failed API calls are logged in the api_failures table.
"""
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import psycopg2
import requests

API_URL = "https://api.open-meteo.com/v1/forecast"
SLOT_HOURS = 6  # must match the cron schedule
MAX_ATTEMPTS = 3  # tries per city before it is logged as a failure
RETRY_WAIT_SECONDS = 5  # wait grows each time: 5s, then 10s


def expected_slot(now):
    """Round a time down to its 6-hour slot (00:00, 06:00, 12:00, 18:00 UTC)."""
    hour = (now.hour // SLOT_HOURS) * SLOT_HOURS
    return now.replace(hour=hour, minute=0, second=0, microsecond=0)


def fetch_city(lat, lon):
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,"
                 "precipitation_sum,precipitation_probability_max",
        "current": "temperature_2m,relative_humidity_2m,precipitation",
        "forecast_days": 8,  # today (lead 0) plus the next 7 days
        "timezone": "auto",
    }
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(API_URL, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:  # timeout, 429, 5xx, network
            last_error = exc
            print(f"  attempt {attempt}/{MAX_ATTEMPTS} failed: {type(exc).__name__}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_WAIT_SECONDS * attempt)
    raise last_error  # all attempts failed: the caller logs it in api_failures


def save_city(cur, run_id, city_id, data):
    current = data["current"]
    local_now = datetime.fromisoformat(current["time"])  # city local time
    offset = timedelta(seconds=data["utc_offset_seconds"])
    observed_at = (local_now - offset).replace(tzinfo=timezone.utc)
    local_date = local_now.date()

    cur.execute(
        """INSERT INTO observations
               (run_id, city_id, observed_at, temperature, humidity, precipitation)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (run_id, city_id) DO NOTHING""",
        (run_id, city_id, observed_at, current["temperature_2m"],
         current["relative_humidity_2m"], current["precipitation"]),
    )

    daily = data["daily"]
    for i, day in enumerate(daily["time"]):
        target = date.fromisoformat(day)
        lead = (target - local_date).days
        cur.execute(
            """INSERT INTO forecasts
                   (run_id, city_id, target_date, lead_days,
                    temp_max, temp_min, precipitation, precip_prob)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (run_id, city_id, target_date) DO NOTHING""",
            (run_id, city_id, target, lead,
             daily["temperature_2m_max"][i], daily["temperature_2m_min"][i],
             daily["precipitation_sum"][i],
             daily["precipitation_probability_max"][i]),
        )


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()

    now = datetime.now(timezone.utc)
    cur.execute(
        "INSERT INTO runs (started_at, status, expected_slot) "
        "VALUES (%s, 'started', %s) RETURNING id",
        (now, expected_slot(now)),
    )
    run_id = cur.fetchone()[0]
    conn.commit()  # the run is recorded even if everything below fails

    cur.execute("SELECT id, name, latitude, longitude FROM cities ORDER BY id")
    cities = cur.fetchall()

    failures = 0
    for city_id, name, lat, lon in cities:
        try:
            data = fetch_city(lat, lon)
            save_city(cur, run_id, city_id, data)
            conn.commit()
            print(f"OK    {name}")
        except Exception as exc:  # network error, bad status, missing field
            conn.rollback()
            failures += 1
            message = f"{type(exc).__name__} (after {MAX_ATTEMPTS} attempts): {exc}"
            cur.execute(
                "INSERT INTO api_failures (run_id, city_id, error_message) "
                "VALUES (%s, %s, %s)",
                (run_id, city_id, message[:1000]),
            )
            conn.commit()
            print(f"FAIL  {name}: {message}")

    if failures == 0:
        status = "success"
    elif failures < len(cities):
        status = "partial"
    else:
        status = "failed"

    cur.execute(
        "UPDATE runs SET finished_at = %s, status = %s WHERE id = %s",
        (datetime.now(timezone.utc), status, run_id),
    )
    conn.commit()
    conn.close()
    print(f"Run {run_id} finished: {status}")

    if status == "failed":
        sys.exit(1)  # makes the GitHub Actions run show as failed


if __name__ == "__main__":
    main()
