"""Measure one query before and after adding an index.

This exists to produce a number you can quote. It builds a large rentals
table, runs a realistic query with no supporting index, reads the query
plan, adds the index, and runs it again.

    python3 benchmark.py --db /tmp/bench.db --rentals 300000
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import time
from datetime import datetime, timedelta

QUERY = """
    SELECT vehicle_id, COUNT(*) AS rentals, MAX(end_at) AS last_return
    FROM rentals_bench
    WHERE out_branch = ?
      AND start_at >= ?
    GROUP BY vehicle_id
    ORDER BY rentals DESC
    LIMIT 20
"""

BRANCHES = ["BR-ASE", "BR-GWS", "BR-EGE", "BR-DEN", "BR-GJT"]


def build(conn: sqlite3.Connection, n_rows: int, seed: int = 7) -> None:
    rng = random.Random(seed)
    conn.executescript(
        """
        DROP TABLE IF EXISTS rentals_bench;
        CREATE TABLE rentals_bench (
            rental_id  INTEGER PRIMARY KEY,
            vehicle_id TEXT NOT NULL,
            out_branch TEXT NOT NULL,
            start_at   TEXT NOT NULL,
            end_at     TEXT
        );
        """
    )
    base = datetime(2024, 1, 1)
    rows = []
    for i in range(n_rows):
        start = base + timedelta(hours=rng.randint(0, 24 * 700))
        rows.append(
            (
                i + 1,
                f"VH-{rng.randint(1, 5000):05d}",
                rng.choice(BRANCHES),
                start.strftime("%Y-%m-%d %H:%M:%S"),
                (start + timedelta(days=rng.randint(1, 10))).strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
    conn.executemany("INSERT INTO rentals_bench VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()


def time_query(conn: sqlite3.Connection, runs: int = 5) -> float:
    params = ("BR-DEN", "2025-10-01 00:00:00")
    best = float("inf")
    for _ in range(runs):
        t0 = time.perf_counter()
        conn.execute(QUERY, params).fetchall()
        best = min(best, time.perf_counter() - t0)
    return best * 1000  # milliseconds


def explain(conn: sqlite3.Connection) -> str:
    params = ("BR-DEN", "2025-10-01 00:00:00")
    rows = conn.execute("EXPLAIN QUERY PLAN " + QUERY, params).fetchall()
    return "\n".join("    " + row[-1] for row in rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Index before/after benchmark")
    parser.add_argument("--db", default=":memory:")
    parser.add_argument("--rentals", type=int, default=300_000)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    print(f"building {args.rentals:,} rows...")
    build(conn, args.rentals)
    conn.execute("ANALYZE")

    print("\nBEFORE (no supporting index)")
    print(explain(conn))
    before = time_query(conn)
    print(f"    best of 5 runs: {before:.2f} ms")

    conn.execute("CREATE INDEX idx_bench_branch_start ON rentals_bench(out_branch, start_at)")
    conn.execute("ANALYZE")

    print("\nAFTER (composite index on out_branch, start_at)")
    print(explain(conn))
    after = time_query(conn)
    print(f"    best of 5 runs: {after:.2f} ms")

    speedup = before / after if after else float("inf")
    print(f"\n{before:.2f} ms -> {after:.2f} ms  ({speedup:.1f}x faster)")
    print(
        "Column order matters: out_branch is an equality filter so it goes first, "
        "start_at is a range filter so it goes second."
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
