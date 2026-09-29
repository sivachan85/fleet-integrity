"""Suggest fleet moves between branches.

This is the piece to replace with the logic you actually use day to day.
The structure is what matters: pull the current state with SQL, score it in
Python, and print a plan a person can act on.

The default scoring is deliberately simple and explainable:

    demand_score = rentals started at the branch in the last N days
    supply       = vehicles currently available at the branch
    pressure     = demand_score / (supply + 1)

High pressure means the branch is renting more than it can cover, so it
should receive vehicles. Low pressure with vehicles sitting idle means it
should send them. Moves are only suggested when the gap is worth the drive.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass

from db import configure_logging, connect

log = logging.getLogger("rotate")

STATE_SQL = """
    SELECT b.branch_id,
           b.name,
           b.capacity,
           COUNT(DISTINCT CASE WHEN v.status = 'available' THEN v.vehicle_id END) AS available,
           COUNT(DISTINCT v.vehicle_id) AS assigned,
           (SELECT COUNT(*)
              FROM rentals r
             WHERE r.out_branch = b.branch_id
               AND r.start_at >= datetime('now', ?)) AS recent_demand
    FROM branches b
    LEFT JOIN vehicles v ON v.branch_id = b.branch_id
    GROUP BY b.branch_id, b.name, b.capacity
    ORDER BY b.branch_id
"""


@dataclass
class BranchState:
    branch_id: str
    name: str
    capacity: int
    available: int
    assigned: int
    recent_demand: int

    @property
    def pressure(self) -> float:
        return self.recent_demand / (self.available + 1)

    @property
    def headroom(self) -> int:
        return self.capacity - self.assigned


def load_state(conn, window_days: int) -> list[BranchState]:
    rows = conn.execute(STATE_SQL, (f"-{window_days} days",)).fetchall()
    return [BranchState(**dict(row)) for row in rows]


def plan_moves(states: list[BranchState], min_gap: float, max_moves: int) -> list[tuple]:
    """Greedy pairing: highest-pressure branch pulls from the lowest."""
    moves: list[tuple] = []
    senders = sorted(states, key=lambda s: s.pressure)
    receivers = sorted(states, key=lambda s: s.pressure, reverse=True)

    # Track both sides as we go: vehicles a sender can still spare, and
    # space a receiver can still take. Without this, three separate moves
    # can each look valid and together overfill the same branch.
    spare_at = {s.branch_id: s.available for s in states}
    room_at = {s.branch_id: max(0, s.headroom) for s in states}

    for receiver in receivers:
        for sender in senders:
            if len(moves) >= max_moves:
                return moves
            if sender.branch_id == receiver.branch_id:
                continue
            gap = receiver.pressure - sender.pressure
            if gap < min_gap:
                continue
            # Never strip a branch below a small local buffer, and never
            # exceed what the receiving branch can physically hold.
            spare = max(0, spare_at[sender.branch_id] - 3)
            count = min(spare, room_at[receiver.branch_id], 5)
            if count <= 0:
                continue
            moves.append((sender, receiver, count, gap))
            spare_at[sender.branch_id] -= count
            room_at[receiver.branch_id] -= count
    return moves


def main() -> int:
    parser = argparse.ArgumentParser(description="Suggest fleet rebalancing moves")
    parser.add_argument("--db", default="fleet.db")
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--min-gap", type=float, default=0.5)
    parser.add_argument("--max-moves", type=int, default=5)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)
    conn = connect(args.db)
    try:
        states = load_state(conn, args.window_days)
    finally:
        conn.close()

    print(f"\nBranch state (last {args.window_days} days)")
    print(f"{'branch':<9} {'name':<28} {'avail':>6} {'assigned':>9} {'cap':>5} {'demand':>7} {'pressure':>9}")
    for s in sorted(states, key=lambda s: s.pressure, reverse=True):
        print(
            f"{s.branch_id:<9} {s.name:<28} {s.available:>6} {s.assigned:>9} "
            f"{s.capacity:>5} {s.recent_demand:>7} {s.pressure:>9.2f}"
        )

    moves = plan_moves(states, args.min_gap, args.max_moves)
    print("\nSuggested moves")
    if not moves:
        print("  none: the fleet is balanced within the threshold")
    for sender, receiver, count, gap in moves:
        print(
            f"  move {count} vehicle(s): {sender.branch_id} -> {receiver.branch_id} "
            f"(pressure gap {gap:.2f})"
        )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
