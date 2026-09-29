"""Tests for the integrity checks.

The pattern for each: build a tiny in-memory database, insert one row that
is deliberately wrong, and assert the check catches exactly that row. A
check that never fires is worse than no check at all, so each one gets a
positive case and, where it matters, a clean case that must stay silent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from checks import CHECKS, has_errors, run_all, run_check  # noqa: E402
from db import connect, create_schema  # noqa: E402
from rotate import BranchState, plan_moves  # noqa: E402

BY_ID = {c.check_id: c for c in CHECKS}


@pytest.fixture()
def conn():
    connection = connect(":memory:")
    create_schema(connection)
    connection.execute("INSERT INTO branches VALUES ('BR-A', 'Alpha', 10)")
    connection.execute("INSERT INTO branches VALUES ('BR-B', 'Beta', 10)")
    yield connection
    connection.close()


def add_vehicle(conn, vehicle_id="VH-1", status="available", branch="BR-A",
                last_service="2026-01-01", updated="2026-09-27 08:00:00"):
    conn.execute(
        "INSERT INTO vehicles VALUES (?, 'ECON', 'Toyota', 'Corolla', 2024, ?, ?, 1000, ?, ?)",
        (vehicle_id, status, branch, last_service, updated),
    )


def add_rental(conn, rental_id, vehicle_id, start, end, out_branch="BR-A"):
    conn.execute(
        "INSERT INTO rentals VALUES (?, ?, ?, NULL, ?, ?)",
        (rental_id, vehicle_id, out_branch, start, end),
    )


def test_clean_database_passes_everything(conn):
    add_vehicle(conn, "VH-1", status="available")
    add_rental(conn, "RN-1", "VH-1", "2026-05-01 09:00:00", "2026-05-04 09:00:00")
    results = run_all(conn)
    assert not has_errors(results)
    assert all(not r.failed for r in results), [r.check.check_id for r in results if r.failed]


def test_available_vehicle_with_open_rental_is_caught(conn):
    add_vehicle(conn, "VH-1", status="available")
    add_rental(conn, "RN-1", "VH-1", "2026-09-20 09:00:00", None)
    result = run_check(conn, BY_ID["FLT-001"])
    assert result.count == 1
    assert result.rows[0]["vehicle_id"] == "VH-1"


def test_rented_vehicle_with_open_rental_is_fine(conn):
    add_vehicle(conn, "VH-1", status="rented")
    add_rental(conn, "RN-1", "VH-1", "2026-09-20 09:00:00", None)
    assert run_check(conn, BY_ID["FLT-001"]).count == 0


def test_overlapping_rentals_are_caught(conn):
    add_vehicle(conn, "VH-1", status="rented")
    add_rental(conn, "RN-1", "VH-1", "2026-05-01 09:00:00", "2026-05-10 09:00:00")
    add_rental(conn, "RN-2", "VH-1", "2026-05-08 09:00:00", "2026-05-12 09:00:00")
    result = run_check(conn, BY_ID["FLT-002"])
    assert result.count == 1


def test_back_to_back_rentals_do_not_overlap(conn):
    """Touching endpoints are not an overlap: one ends as the next starts."""
    add_vehicle(conn, "VH-1", status="rented")
    add_rental(conn, "RN-1", "VH-1", "2026-05-01 09:00:00", "2026-05-10 09:00:00")
    add_rental(conn, "RN-2", "VH-1", "2026-05-10 09:00:00", "2026-05-14 09:00:00")
    assert run_check(conn, BY_ID["FLT-002"]).count == 0


def test_open_rental_counts_as_running_forever(conn):
    add_vehicle(conn, "VH-1", status="rented")
    add_rental(conn, "RN-1", "VH-1", "2026-05-01 09:00:00", None)
    add_rental(conn, "RN-2", "VH-1", "2026-06-01 09:00:00", "2026-06-04 09:00:00")
    assert run_check(conn, BY_ID["FLT-002"]).count == 1


def test_future_service_date_is_caught(conn):
    add_vehicle(conn, "VH-1", last_service="2099-01-01")
    result = run_check(conn, BY_ID["FLT-004"])
    assert result.count == 1
    assert result.rows[0]["last_service_at"] == "2099-01-01"


def test_stale_record_is_caught(conn):
    add_vehicle(conn, "VH-1", updated="2020-01-01 00:00:00")
    assert run_check(conn, BY_ID["FLT-005"]).count == 1


def test_branch_over_capacity_is_caught(conn):
    for i in range(12):  # capacity is 10
        add_vehicle(conn, f"VH-{i}", branch="BR-A")
    result = run_check(conn, BY_ID["FLT-006"])
    assert result.count == 1
    assert result.rows[0]["assigned"] == 12


def test_rented_status_without_open_rental_is_caught(conn):
    add_vehicle(conn, "VH-1", status="rented")
    add_rental(conn, "RN-1", "VH-1", "2026-05-01 09:00:00", "2026-05-04 09:00:00")
    assert run_check(conn, BY_ID["FLT-007"]).count == 1


def test_schema_rejects_bad_status(conn):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        add_vehicle(conn, "VH-1", status="teleported")


def test_schema_rejects_end_before_start(conn):
    import sqlite3

    add_vehicle(conn, "VH-1")
    with pytest.raises(sqlite3.IntegrityError):
        add_rental(conn, "RN-1", "VH-1", "2026-05-10 09:00:00", "2026-05-01 09:00:00")


def test_rebalance_respects_receiver_capacity():
    """A receiver with room for 2 must never be sent more than 2 in total."""
    tight = BranchState("BR-A", "Alpha", capacity=10, available=1, assigned=8, recent_demand=90)
    loose1 = BranchState("BR-B", "Beta", capacity=50, available=30, assigned=30, recent_demand=5)
    loose2 = BranchState("BR-C", "Gamma", capacity=50, available=30, assigned=30, recent_demand=5)

    moves = plan_moves([tight, loose1, loose2], min_gap=0.5, max_moves=10)
    to_tight = sum(count for _s, receiver, count, _g in moves if receiver.branch_id == "BR-A")
    assert to_tight <= tight.headroom


def test_rebalance_keeps_a_local_buffer():
    """A sender never gives away its last few available vehicles."""
    busy = BranchState("BR-A", "Alpha", capacity=50, available=0, assigned=10, recent_demand=80)
    thin = BranchState("BR-B", "Beta", capacity=50, available=3, assigned=10, recent_demand=1)
    moves = plan_moves([busy, thin], min_gap=0.5, max_moves=10)
    assert moves == []


def test_balanced_fleet_suggests_nothing():
    a = BranchState("BR-A", "Alpha", capacity=50, available=20, assigned=25, recent_demand=20)
    b = BranchState("BR-B", "Beta", capacity=50, available=20, assigned=25, recent_demand=20)
    assert plan_moves([a, b], min_gap=0.5, max_moves=10) == []
