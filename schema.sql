-- Weather forecast accuracy project: database schema (PostgreSQL)
-- Run this once in the Neon SQL Editor.

CREATE TABLE cities (
    id        SERIAL PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE,
    latitude  DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    climate   TEXT NOT NULL
);

-- One row per collector run. Never updated except to close the run.
CREATE TABLE runs (
    id            SERIAL PRIMARY KEY,
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ,
    status        TEXT NOT NULL DEFAULT 'started'
                  CHECK (status IN ('started', 'success', 'partial', 'failed')),
    expected_slot TIMESTAMPTZ NOT NULL  -- the 6-hour slot this run belongs to
);

-- Every forecast ever collected. One row per run, city and target date.
-- No CHECK constraints on values on purpose: bad values must be stored so
-- the data quality checks can find and report them.
CREATE TABLE forecasts (
    id            SERIAL PRIMARY KEY,
    run_id        INTEGER NOT NULL REFERENCES runs(id),
    city_id       INTEGER NOT NULL REFERENCES cities(id),
    target_date   DATE NOT NULL,     -- the day being forecast (city local date)
    lead_days     INTEGER NOT NULL,  -- target_date minus the run's local date
    temp_max      DOUBLE PRECISION,
    temp_min      DOUBLE PRECISION,
    precipitation DOUBLE PRECISION,  -- mm for the day
    precip_prob   DOUBLE PRECISION,  -- max probability of rain, percent
    UNIQUE (run_id, city_id, target_date)
);

-- Weather observed at the time of each run.
CREATE TABLE observations (
    id            SERIAL PRIMARY KEY,
    run_id        INTEGER NOT NULL REFERENCES runs(id),
    city_id       INTEGER NOT NULL REFERENCES cities(id),
    observed_at   TIMESTAMPTZ NOT NULL,
    temperature   DOUBLE PRECISION,
    humidity      DOUBLE PRECISION,
    precipitation DOUBLE PRECISION,  -- mm in the last hour
    UNIQUE (run_id, city_id)
);

-- One row per failed API call, with the reason.
CREATE TABLE api_failures (
    id            SERIAL PRIMARY KEY,
    run_id        INTEGER NOT NULL REFERENCES runs(id),
    city_id       INTEGER REFERENCES cities(id),
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    error_message TEXT NOT NULL
);

CREATE INDEX idx_forecasts_city_target ON forecasts (city_id, target_date);
CREATE INDEX idx_observations_city_time ON observations (city_id, observed_at);

INSERT INTO cities (name, latitude, longitude, climate) VALUES
    ('Colombo',   6.9271,   79.8612, 'Tropical, monsoon'),
    ('Dubai',     25.2048,  55.2708, 'Hot desert'),
    ('Reykjavik', 64.1466, -21.9426, 'Cold, rainy, oceanic'),
    ('Singapore', 1.3521,  103.8198, 'Equatorial, humid'),
    ('Moscow',    55.7558,  37.6173, 'Continental, cold winters');
