# Fleet Integrity Checker

Automated data integrity checks and rebalancing suggestions for a vehicle rental fleet.

## Why I built it

I work with fleet data every day as a fleet and sales analyst: vehicle records,
statuses, branch assignments, rental history. Records and physical reality drift
apart, and catching that by hand means comparing screens. A car marked available
that is actually still out becomes a bad reservation; a status that never got
reset skews the planning numbers.

This project models that data properly and runs the checks automatically. Every
check is one SQL query that returns the rows that are wrong, so the database does
the work and the logic stays readable.

**All data here is synthetic.** The generator produces randomly created records
with the same shape as a rental fleet. No employer data, customer data, or
internal system details are used anywhere in this repository.

## Quick start

```bash
# 1. Build a database with synthetic data (--faults injects known problems)
python3 src/generate_data.py --db fleet.db --vehicles 400 --rentals 20000 --faults

# 2. Run every check and write a report
python3 src/run_checks.py --db fleet.db --report report.md --csv findings.csv

# 3. See suggested fleet moves between branches
python3 src/rotate.py --db fleet.db

# 4. Measure a query before and after an index
python3 src/benchmark.py --rentals 300000

# 5. Run the tests
python3 -m pytest tests/ -q
```

Python 3.10+ and the standard library. `pytest` only for the tests.

## The schema

Three tables: `branches`, `vehicles`, `rentals`. The constraints are the point.
Each one is a rule the database enforces so bad data cannot be written at all:

- `CHECK (status IN (...))` — a vehicle cannot hold a status that does not exist
- `CHECK (end_at IS NULL OR end_at > start_at)` — a rental cannot end before it starts
- `CHECK (mileage >= 0)`, `CHECK (capacity > 0)` — values that cannot be negative
- foreign keys from `rentals` to `vehicles` and `branches` — no orphaned rentals

Three indexes, each for a specific query pattern rather than by default:

| Index | Serves |
| --- | --- |
| `idx_rentals_vehicle` | joining rentals to a vehicle; the open-rental and overlap checks |
| `idx_rentals_start` | date-range scans |
| `idx_vehicles_branch_status` | "what is available at branch X", the most common question |

Column order in the composite index is deliberate: `branch_id` first because it
is always filtered, `status` second because it is sometimes.

## The checks

| ID | Check | Severity | What it means |
| --- | --- | --- | --- |
| FLT-001 | Available vehicle with an open rental | error | Rentable car that is not on the lot |
| FLT-002 | Double-booked vehicle | error | Two rentals overlap in time |
| FLT-003 | Rental referencing a missing vehicle | error | Orphan row; data bypassed the constraints |
| FLT-004 | Service date in the future | error | Impossible date |
| FLT-005 | Stale vehicle record | warning | Not updated in 60 days |
| FLT-006 | Branch over capacity | warning | More vehicles assigned than the branch holds |
| FLT-007 | Rented vehicle with no open rental | warning | Status never reset after return |

`run_checks.py` exits 1 when any error-severity check finds something, so it can
run on a schedule or in a pipeline and alert rather than be read by hand.

The overlap check (FLT-002) is the interesting one. Two periods overlap when each
starts before the other ends, and `COALESCE(end_at, '9999-12-31')` treats an open
rental as running forever. The self-join uses `a.rental_id < b.rental_id` so each
pair is reported once instead of twice.

## Rebalancing

`rotate.py` scores each branch:

```
pressure = rentals started in the last N days / (vehicles available + 1)
```

High pressure means a branch is renting more than it can cover and should receive
vehicles; low pressure with idle vehicles means it should send them. Moves are
capped by what a sender can spare (keeping a local buffer) and what a receiver
can physically hold, tracked across the whole plan so several valid-looking moves
cannot together overfill one branch.

This is the piece to swap for real allocation logic. The structure is what matters:
pull state with SQL, score in Python, print a plan a person can act on.

## Query optimization

`benchmark.py` runs a realistic query against 300,000 rentals, reads the plan,
adds a composite index, and runs it again:

```
BEFORE   SCAN rentals_bench                                    15.49 ms
AFTER    SEARCH rentals_bench USING INDEX (out_branch=? AND start_at>?)   4.34 ms
         3.6x faster
```

`out_branch` goes first in the index because it is an equality filter; `start_at`
second because it is a range filter. Reversing that order gives up most of the gain.

## Tests

15 tests. Each check gets a case with one deliberately wrong row, asserting it is
caught, plus clean cases that must stay silent — back-to-back rentals that touch
at the endpoints are not an overlap, and a rented vehicle with an open rental is
correct, not a finding. Two tests confirm the schema itself rejects an invalid
status and an end date before a start date.

```
15 passed in 0.03s
```

## Layout

```
src/schema.sql         tables, constraints, indexes
src/db.py              connection handling, schema creation, logging
src/generate_data.py   synthetic fleet generator (--faults injects known problems)
src/checks.py          the checks: one SQL query each, with severity
src/run_checks.py      CLI runner; markdown + CSV reports; exit codes
src/rotate.py          branch pressure scoring and move suggestions
src/benchmark.py       index before/after timing with query plans
tests/test_checks.py   pytest suite
```
