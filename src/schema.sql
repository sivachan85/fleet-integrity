-- Fleet integrity checker: schema
-- SQLite dialect. Constraints are deliberate: each one is a rule the
-- database enforces so bad data cannot be written in the first place.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS rentals;
DROP TABLE IF EXISTS vehicles;
DROP TABLE IF EXISTS branches;

CREATE TABLE branches (
    branch_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    capacity    INTEGER NOT NULL CHECK (capacity > 0)
);

CREATE TABLE vehicles (
    vehicle_id      TEXT PRIMARY KEY,
    class_code      TEXT NOT NULL,
    make            TEXT,
    model           TEXT,
    model_year      INTEGER CHECK (model_year BETWEEN 1990 AND 2100),
    status          TEXT NOT NULL CHECK (status IN
                      ('available', 'rented', 'maintenance', 'transit', 'out_of_service')),
    branch_id       TEXT NOT NULL REFERENCES branches(branch_id),
    mileage         INTEGER NOT NULL CHECK (mileage >= 0),
    last_service_at TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE rentals (
    rental_id   TEXT PRIMARY KEY,
    vehicle_id  TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    out_branch  TEXT NOT NULL REFERENCES branches(branch_id),
    in_branch   TEXT REFERENCES branches(branch_id),
    start_at    TEXT NOT NULL,
    end_at      TEXT,
    CHECK (end_at IS NULL OR end_at > start_at)
);

-- Indexes exist for specific query patterns, not by default:
--   idx_rentals_vehicle  -> joining rentals back to a vehicle, and the
--                           open-rental and overlap checks
--   idx_rentals_start    -> date-range scans in the utilization report
--   idx_vehicles_branch_status -> "what is available at branch X", the
--                           most common operational question. Column order
--                           matters: branch_id first because it is always
--                           filtered, status second because it is sometimes.
CREATE INDEX idx_rentals_vehicle ON rentals(vehicle_id);
CREATE INDEX idx_rentals_start ON rentals(start_at);
CREATE INDEX idx_vehicles_branch_status ON vehicles(branch_id, status);
