"""Data quality report for the weather forecast project.

Checks four things and prints a short report:
  1. Missed runs        - expected 6-hour slots with no scheduled run, or no successful one
  2. Duplicate records  - repeated rows, and slots with more than one run
  3. Impossible values  - for example humidity above 100 or negative rain
  4. Failed API calls   - from the api_failures table, by city and reason

Only runs started by the schedule count towards a slot. Manual runs
(workflow_dispatch) and laptop runs (local) are reported but never hide a
missed scheduled run.

Usage:
    export DATABASE_URL="postgresql://..."
    python quality.py                      # print the report
    python quality.py > quality_report.txt # save it for your screenshots
"""
import os
from datetime import datetime, timedelta, timezone

import psycopg2

SLOT_HOURS = 6        # must match the cron schedule in collect.yml
GRACE_MINUTES = 60    # GitHub can start a run late, so a slot is only "due" 1h after it begins
STUCK_MINUTES = 30    # a run still 'started' after this long was probably killed

# First scheduled slot after the workflow went live: Nov 19 2026, 06:00 UTC
# (11:30 Sri Lanka time). Slots before this had no schedule, so they are not "missed".
MONITORING_START = datetime(2026, 11, 19, 6, 0, tzinfo=timezone.utc)


# ---------- pure functions (no database, easy to test) ----------

def expected_slots(first_slot, now, hours=SLOT_HOURS, grace_minutes=GRACE_MINUTES):
    """Every slot from first_slot up to the last one that is already due."""
    slots = []
    slot = first_slot
    while slot + timedelta(minutes=grace_minutes) <= now:
        slots.append(slot)
        slot += timedelta(hours=hours)
    return slots


def classify_slots(slots, runs):
    """Sort slots into missed / incomplete / succeeded.

    runs is a list of (expected_slot, status) pairs.
      missed     - no run at all in the slot
      incomplete - runs happened but none had status 'success'
      succeeded  - at least one run had status 'success'
    """
    statuses = {}
    for slot, status in runs:
        statuses.setdefault(slot, []).append(status)
    missed = [s for s in slots if s not in statuses]
    incomplete = [s for s in slots if s in statuses and "success" not in statuses[s]]
    succeeded = [s for s in slots if s in statuses and "success" in statuses[s]]
    return missed, incomplete, succeeded


def scheduled_runs(rows):
    """Keep only scheduled runs.

    rows are (expected_slot, status, started_at, trigger_type) tuples; the result
    is a list of (expected_slot, status, started_at).
    """
    return [(slot, status, started) for slot, status, started, trigger in rows
            if trigger == "schedule"]


def minutes_after_slot(slot, started_at):
    """How long after its slot began a run actually started."""
    return (started_at - slot).total_seconds() / 60


# ---------- impossible-value rules ----------
# (label, table, SQL condition that is TRUE when the row is wrong)
IMPOSSIBLE_RULES = [
    ("Humidity below 0 or above 100", "observations",
     "humidity < 0 OR humidity > 100"),
    ("Negative rainfall (observed)", "observations",
     "precipitation < 0"),
    ("Observed temperature outside -90 to 60 C", "observations",
     "temperature < -90 OR temperature > 60"),
    ("Negative rainfall (forecast)", "forecasts",
     "precipitation < 0"),
    ("Rain probability outside 0 to 100", "forecasts",
     "precip_prob < 0 OR precip_prob > 100"),
    ("Forecast temperature outside -90 to 60 C", "forecasts",
     "temp_max < -90 OR temp_max > 60 OR temp_min < -90 OR temp_min > 60"),
    ("Minimum temperature above maximum", "forecasts",
     "temp_min > temp_max"),
    ("Lead time outside 0 to 7 days", "forecasts",
     "lead_days < 0 OR lead_days > 7"),
]


def fmt(ts):
    return ts.strftime("%Y-%m-%d %H:%M UTC")


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    now = datetime.now(timezone.utc)

    print("DATA QUALITY REPORT")
    print(f"Generated: {fmt(now)}")
    print("=" * 60)

    # ---- 1. Runs and missed slots ----
    cur.execute("SELECT expected_slot, status, started_at, trigger_type FROM runs")
    rows = cur.fetchall()
    cur.execute("SELECT status, COUNT(*) FROM runs GROUP BY status")
    by_status = dict(cur.fetchall())
    cur.execute(
        "SELECT COUNT(*) FROM runs WHERE status = 'started' "
        "AND started_at < %s", (now - timedelta(minutes=STUCK_MINUTES),))
    stuck = cur.fetchone()[0]

    slots, succeeded = [], []
    print("\n1. RUNS")
    if not rows:
        print("   No runs recorded yet.")
    else:
        scheduled = [r for r in scheduled_runs(rows) if r[0] >= MONITORING_START]
        slots = expected_slots(MONITORING_START, now)
        missed, incomplete, succeeded = classify_slots(
            slots, [(slot, status) for slot, status, _ in scheduled])
        ran = len(slots) - len(missed)
        print(f"   Scheduled slots expected (every {SLOT_HOURS}h since {fmt(MONITORING_START)}): {len(slots)}")
        print(f"   Slots with a scheduled run:       {ran}")
        print(f"   Slots with a successful run:      {len(succeeded)}")
        print(f"   Missed slots (no scheduled run):  {len(missed)}")
        for s in missed:
            print(f"      - {fmt(s)}")
        print(f"   Incomplete slots (no success):    {len(incomplete)}")
        for s in incomplete:
            print(f"      - {fmt(s)}")
        print(f"   Runs recorded in total:           {len(rows)}")
        print(f"      scheduled {len(scheduled_runs(rows))}, "
              f"manual or local {len(rows) - len(scheduled_runs(rows))}")
        for status in ("success", "partial", "failed", "started"):
            print(f"      {status:<8} {by_status.get(status, 0)}")
        print(f"   Runs stuck on 'started' (>{STUCK_MINUTES} min): {stuck}")
        print("   Scheduled run delay (minutes after the slot began):")
        if not scheduled:
            print("      none yet")
        for slot, _, started in sorted(scheduled, key=lambda r: r[2]):
            print(f"      slot {fmt(slot)}: started {minutes_after_slot(slot, started):.0f} min late")

    # ---- 2. Duplicates ----
    print("\n2. DUPLICATES")
    cur.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM forecasts "
        "GROUP BY run_id, city_id, target_date HAVING COUNT(*) > 1) d")
    dup_forecasts = cur.fetchone()[0]
    cur.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM observations "
        "GROUP BY run_id, city_id HAVING COUNT(*) > 1) d")
    dup_obs = cur.fetchone()[0]
    cur.execute(
        "SELECT expected_slot, COUNT(*), "
        "COUNT(*) FILTER (WHERE trigger_type = 'schedule') FROM runs "
        "GROUP BY expected_slot HAVING COUNT(*) > 1 ORDER BY expected_slot")
    repeat_slots = cur.fetchall()
    print(f"   Duplicate forecast rows (same run, city, date): {dup_forecasts}")
    print(f"   Duplicate observation rows (same run, city):    {dup_obs}")
    print(f"   Slots with more than one run (manual runs included): {len(repeat_slots)}")
    for slot, n, n_sched in repeat_slots:
        print(f"      - {fmt(slot)}: {n} runs ({n_sched} scheduled, {n - n_sched} manual or local)")

    # ---- 3. Impossible values ----
    print("\n3. IMPOSSIBLE VALUES")
    cur.execute("SELECT (SELECT COUNT(*) FROM forecasts), (SELECT COUNT(*) FROM observations)")
    n_forecasts, n_obs = cur.fetchone()
    bad_total = 0
    for label, table, condition in IMPOSSIBLE_RULES:
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {condition}")
        count = cur.fetchone()[0]
        bad_total += count
        print(f"   {label:<45} {count}")
    print(f"   Records checked: {n_forecasts} forecasts + {n_obs} observations")
    print(f"   Problem records found: {bad_total}")

    # ---- 4. Failed API calls ----
    print("\n4. FAILED API CALLS")
    cur.execute("SELECT COUNT(*) FROM api_failures")
    total_failures = cur.fetchone()[0]
    print(f"   Failed calls in total: {total_failures}")
    cur.execute(
        "SELECT COALESCE(c.name, 'unknown'), COUNT(*) FROM api_failures f "
        "LEFT JOIN cities c ON c.id = f.city_id GROUP BY 1 ORDER BY 2 DESC, 1")
    for name, n in cur.fetchall():
        print(f"      {name:<12} {n}")
    cur.execute(
        "SELECT split_part(error_message, ':', 1) AS reason, COUNT(*) "
        "FROM api_failures GROUP BY 1 ORDER BY 2 DESC")
    print("   By reason:")
    for reason, n in cur.fetchall():
        print(f"      {reason:<45} {n}")

    # ---- Summary ----
    print("\n" + "=" * 60)
    print("SUMMARY")
    print(f"   Scheduled runs expected:   {len(slots)}")
    print(f"   Scheduled runs succeeded:  {len(succeeded)}")
    print(f"   Failed API calls:          {total_failures}")
    print(f"   Records with problems:     {bad_total + dup_forecasts + dup_obs}")
    conn.close()


if __name__ == "__main__":
    main()
