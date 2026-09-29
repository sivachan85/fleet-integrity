"""Data integrity checks.

Each check is a single SQL query that returns the rows that are *wrong*.
An empty result means the check passed. Keeping the logic in SQL rather
than in Python loops means the database does the work, the checks stay
readable, and they can be run against any copy of the data.

Severity decides the exit code: any 'error' finding fails the run, which
is what makes this usable in a scheduled job or a CI pipeline.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("checks")


@dataclass(frozen=True)
class Check:
    check_id: str
    name: str
    severity: str  # 'error' or 'warning'
    description: str
    sql: str
    params: tuple[Any, ...] = ()


@dataclass
class Result:
    check: Check
    rows: list[sqlite3.Row] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.rows)

    @property
    def count(self) -> int:
        return len(self.rows)


CHECKS: list[Check] = [
    Check(
        check_id="FLT-001",
        name="Available vehicle with an open rental",
        severity="error",
        description=(
            "The vehicle is marked available but a rental is still open "
            "(no end date). Someone could rent a car that is not on the lot."
        ),
        sql="""
            SELECT v.vehicle_id, v.branch_id, r.rental_id, r.start_at
            FROM vehicles v
            JOIN rentals r ON r.vehicle_id = v.vehicle_id
            WHERE v.status = 'available'
              AND r.end_at IS NULL
            ORDER BY r.start_at
        """,
    ),
    Check(
        check_id="FLT-002",
        name="Double-booked vehicle",
        severity="error",
        description=(
            "Two rentals for the same vehicle overlap in time. The classic "
            "overlap test: a starts before b ends, and b starts before a ends."
        ),
        sql="""
            SELECT a.vehicle_id,
                   a.rental_id AS rental_a, a.start_at AS start_a, a.end_at AS end_a,
                   b.rental_id AS rental_b, b.start_at AS start_b, b.end_at AS end_b
            FROM rentals a
            JOIN rentals b
              ON a.vehicle_id = b.vehicle_id
             AND a.rental_id < b.rental_id
            WHERE a.start_at < COALESCE(b.end_at, '9999-12-31')
              AND b.start_at < COALESCE(a.end_at, '9999-12-31')
            ORDER BY a.vehicle_id
        """,
    ),
    Check(
        check_id="FLT-003",
        name="Rental referencing a missing vehicle",
        severity="error",
        description=(
            "An orphaned rental. The foreign key should prevent this, so a "
            "hit here means data arrived by a path that bypassed constraints."
        ),
        sql="""
            SELECT r.rental_id, r.vehicle_id
            FROM rentals r
            LEFT JOIN vehicles v ON v.vehicle_id = r.vehicle_id
            WHERE v.vehicle_id IS NULL
        """,
    ),
    Check(
        check_id="FLT-004",
        name="Service date in the future",
        severity="error",
        description="last_service_at is later than today, so the date is wrong.",
        sql="""
            SELECT vehicle_id, branch_id, last_service_at
            FROM vehicles
            WHERE last_service_at IS NOT NULL
              AND last_service_at > date('now')
        """,
    ),
    Check(
        check_id="FLT-005",
        name="Stale vehicle record",
        severity="warning",
        description=(
            "The record has not been updated in the configured window. Not "
            "wrong on its own, but usually means a vehicle nobody is tracking."
        ),
        sql="""
            SELECT vehicle_id, branch_id, status, updated_at
            FROM vehicles
            WHERE updated_at < datetime('now', ?)
            ORDER BY updated_at
        """,
        params=("-60 days",),
    ),
    Check(
        check_id="FLT-006",
        name="Branch over capacity",
        severity="warning",
        description=(
            "More vehicles assigned to a branch than it can hold. Points at "
            "either a bad branch assignment or a rebalance that never happened."
        ),
        sql="""
            SELECT b.branch_id, b.name, b.capacity, COUNT(v.vehicle_id) AS assigned
            FROM branches b
            JOIN vehicles v ON v.branch_id = b.branch_id
            GROUP BY b.branch_id, b.name, b.capacity
            HAVING COUNT(v.vehicle_id) > b.capacity
            ORDER BY assigned - b.capacity DESC
        """,
    ),
    Check(
        check_id="FLT-007",
        name="Rented vehicle with no open rental",
        severity="warning",
        description=(
            "The mirror of FLT-001: status says rented, but every rental for "
            "the vehicle is closed. Usually a status that was never reset."
        ),
        sql="""
            SELECT v.vehicle_id, v.branch_id, v.updated_at
            FROM vehicles v
            WHERE v.status = 'rented'
              AND NOT EXISTS (
                  SELECT 1 FROM rentals r
                  WHERE r.vehicle_id = v.vehicle_id
                    AND r.end_at IS NULL
              )
            ORDER BY v.updated_at
        """,
    ),
]


def run_check(conn: sqlite3.Connection, check: Check) -> Result:
    cur = conn.execute(check.sql, check.params)
    rows = cur.fetchall()
    level = logging.ERROR if (rows and check.severity == "error") else logging.INFO
    log.log(
        level,
        "%s %s: %d finding(s)",
        check.check_id,
        check.name,
        len(rows),
    )
    return Result(check=check, rows=rows)


def run_all(conn: sqlite3.Connection) -> list[Result]:
    return [run_check(conn, check) for check in CHECKS]


def has_errors(results: list[Result]) -> bool:
    return any(r.failed and r.check.severity == "error" for r in results)
