"""Generate a synthetic fleet dataset.

No real company data is used anywhere in this project. Everything below is
randomly generated with the same *shape* as a rental fleet: branches,
vehicles, and a rental history.

The --faults flag deliberately injects the kinds of integrity problems the
checker is built to find, so the checks can be demonstrated and tested.
"""

from __future__ import annotations

import argparse
import logging
import random
from datetime import datetime, timedelta

from db import configure_logging, connect, create_schema

log = logging.getLogger("generate")

BRANCHES = [
    ("BR-ASE", "Mountain Regional Airport", 120),
    ("BR-GWS", "Valley Downtown", 60),
    ("BR-EGE", "County Airport", 90),
    ("BR-DEN", "Metro Airport", 300),
    ("BR-GJT", "Western Regional", 45),
]

CLASSES = [
    ("ECON", "Compact", ["Corolla", "Civic", "Elantra"]),
    ("MIDS", "Midsize", ["Camry", "Accord", "Malibu"]),
    ("SUVI", "Intermediate SUV", ["RAV4", "CR-V", "Equinox"]),
    ("SUVF", "Full-size SUV", ["Tahoe", "Expedition", "Suburban"]),
    ("LUXY", "Luxury", ["5 Series", "E-Class", "A6"]),
]

MAKES = ["Toyota", "Honda", "Hyundai", "Chevrolet", "Ford", "BMW", "Audi"]
STATUSES = ["available", "rented", "maintenance", "transit", "out_of_service"]
STATUS_WEIGHTS = [50, 33, 8, 6, 3]


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def generate(conn, n_vehicles: int, n_rentals: int, faults: bool, seed: int) -> None:
    rng = random.Random(seed)
    now = datetime(2026, 9, 28, 12, 0, 0)

    conn.executemany("INSERT INTO branches VALUES (?, ?, ?)", BRANCHES)

    vehicles = []
    for i in range(n_vehicles):
        class_code, _label, models = rng.choice(CLASSES)
        vehicle_id = f"VH-{i + 1:05d}"
        status = rng.choices(STATUSES, weights=STATUS_WEIGHTS, k=1)[0]
        branch_id = rng.choice(BRANCHES)[0]
        vehicles.append(
            (
                vehicle_id,
                class_code,
                rng.choice(MAKES),
                rng.choice(models),
                rng.randint(2021, 2026),
                status,
                branch_id,
                rng.randint(500, 95_000),
                (now - timedelta(days=rng.randint(1, 400))).strftime("%Y-%m-%d"),
                iso(now - timedelta(hours=rng.randint(0, 720))),
            )
        )
    conn.executemany(
        "INSERT INTO vehicles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", vehicles
    )
    log.info("inserted %d vehicles across %d branches", len(vehicles), len(BRANCHES))

    # Rentals are built per vehicle as a forward walk through time, so a
    # vehicle is never in two places at once. Clean data by construction:
    # every overlap the checker finds later is one we injected on purpose.
    rentals = []
    per_vehicle = max(1, n_rentals // n_vehicles)
    counter = 0
    for vehicle_id, *_rest in vehicles:
        status = _rest[4]
        cursor_dt = now - timedelta(days=730)
        for _ in range(rng.randint(max(1, per_vehicle - 8), per_vehicle + 8)):
            cursor_dt += timedelta(days=rng.randint(1, 9), hours=rng.randint(0, 23))
            duration = timedelta(days=rng.randint(1, 14))
            if cursor_dt + duration >= now:
                break
            counter += 1
            rentals.append(
                (
                    f"RN-{counter:07d}",
                    vehicle_id,
                    rng.choice(BRANCHES)[0],
                    rng.choice(BRANCHES)[0],
                    iso(cursor_dt),
                    iso(cursor_dt + duration),
                )
            )
            cursor_dt += duration

        # A vehicle that is currently rented gets one open rental on the end.
        if status == "rented":
            counter += 1
            rentals.append(
                (
                    f"RN-{counter:07d}",
                    vehicle_id,
                    rng.choice(BRANCHES)[0],
                    None,
                    iso(now - timedelta(days=rng.randint(1, 10))),
                    None,
                )
            )
    conn.executemany("INSERT INTO rentals VALUES (?, ?, ?, ?, ?, ?)", rentals)
    log.info("inserted %d rentals", len(rentals))

    if faults:
        inject_faults(conn, rng, n_vehicles, n_rentals, now)

    conn.commit()


def inject_faults(conn, rng, n_vehicles: int, n_rentals: int, now: datetime) -> None:
    """Write the specific problems the checker looks for.

    These go in on purpose. Each one maps to a check in checks.py, which is
    what makes the checks testable: we know the answer before we run them.
    """
    cur = conn.cursor()
    faults = 0

    # 1. Vehicles marked available while an open rental exists.
    ids = [f"VH-{i:05d}" for i in rng.sample(range(1, n_vehicles + 1), 4)]
    for n, vid in enumerate(ids):
        cur.execute("UPDATE vehicles SET status = 'available' WHERE vehicle_id = ?", (vid,))
        cur.execute(
            "INSERT INTO rentals VALUES (?, ?, 'BR-DEN', NULL, ?, NULL)",
            (f"RN-F{n:06d}", vid, iso(now - timedelta(days=3))),
        )
        faults += 1

    # 2. Double-booked: two rentals whose periods overlap.
    vid = f"VH-{rng.randint(1, n_vehicles):05d}"
    base = now - timedelta(days=40)
    cur.execute(
        "INSERT INTO rentals VALUES (?, ?, 'BR-ASE', 'BR-ASE', ?, ?)",
        (f"RN-F1{0:05d}", vid, iso(base), iso(base + timedelta(days=6))),
    )
    cur.execute(
        "INSERT INTO rentals VALUES (?, ?, 'BR-GWS', 'BR-GWS', ?, ?)",
        (f"RN-F1{1:05d}", vid, iso(base + timedelta(days=2)), iso(base + timedelta(days=9))),
    )
    faults += 1

    # 3. Service date in the future.
    vid = f"VH-{rng.randint(1, n_vehicles):05d}"
    cur.execute(
        "UPDATE vehicles SET last_service_at = ? WHERE vehicle_id = ?",
        ((now + timedelta(days=30)).strftime("%Y-%m-%d"), vid),
    )
    faults += 1

    # 4. Stale records: not touched in a long time.
    stale = [f"VH-{i:05d}" for i in rng.sample(range(1, n_vehicles + 1), 3)]
    for vid in stale:
        cur.execute(
            "UPDATE vehicles SET updated_at = ? WHERE vehicle_id = ?",
            (iso(now - timedelta(days=120)), vid),
        )
        faults += 1

    # 5. Branch over capacity: pile vehicles into the smallest branch.
    small = min(BRANCHES, key=lambda b: b[2])
    cur.execute(
        """UPDATE vehicles SET branch_id = ?
           WHERE vehicle_id IN (SELECT vehicle_id FROM vehicles LIMIT ?)""",
        (small[0], small[2] + 15),
    )
    faults += 1

    log.warning("injected %d fault groups for demonstration", faults)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic fleet data")
    parser.add_argument("--db", default="fleet.db")
    parser.add_argument("--vehicles", type=int, default=400)
    parser.add_argument("--rentals", type=int, default=20_000)
    parser.add_argument("--faults", action="store_true", help="inject known data problems")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)
    conn = connect(args.db)
    create_schema(conn)
    generate(conn, args.vehicles, args.rentals, args.faults, args.seed)
    conn.close()
    log.info("done: %s", args.db)


if __name__ == "__main__":
    main()
