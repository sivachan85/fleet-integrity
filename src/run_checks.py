"""Run every integrity check and write a report.

Exit codes matter here: 0 means clean, 1 means at least one error-severity
check found something. That is what lets this run on a schedule and alert
instead of being read by hand.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime
from pathlib import Path

from checks import Result, has_errors, run_all
from db import configure_logging, connect

log = logging.getLogger("run_checks")


def write_markdown(results: list[Result], path: Path, db_path: str) -> None:
    lines = [
        "# Fleet data integrity report",
        "",
        f"Database: `{db_path}`  ",
        f"Run at: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "| Check | Name | Severity | Findings |",
        "| --- | --- | --- | --- |",
    ]
    for r in results:
        status = r.count if r.failed else "clean"
        lines.append(
            f"| {r.check.check_id} | {r.check.name} | {r.check.severity} | {status} |"
        )

    for r in results:
        if not r.failed:
            continue
        lines += ["", f"## {r.check.check_id} — {r.check.name}", "", r.check.description, ""]
        cols = r.rows[0].keys()
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join("---" for _ in cols) + " |")
        for row in r.rows[:20]:
            lines.append("| " + " | ".join(str(row[c]) for c in cols) + " |")
        if r.count > 20:
            lines.append("")
            lines.append(f"_{r.count - 20} more row(s) omitted._")

    path.write_text("\n".join(lines) + "\n")
    log.info("wrote %s", path)


def write_csv(results: list[Result], path: Path) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["check_id", "name", "severity", "detail"])
        for r in results:
            for row in r.rows:
                detail = "; ".join(f"{k}={row[k]}" for k in row.keys())
                writer.writerow([r.check.check_id, r.check.name, r.check.severity, detail])
    log.info("wrote %s", path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fleet data integrity checks")
    parser.add_argument("--db", default="fleet.db")
    parser.add_argument("--report", default="report.md", help="markdown report path")
    parser.add_argument("--csv", default=None, help="optional CSV of every finding")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)

    if not Path(args.db).exists():
        log.error("database not found: %s (run generate_data.py first)", args.db)
        return 2

    conn = connect(args.db)
    try:
        results = run_all(conn)
    finally:
        conn.close()

    write_markdown(results, Path(args.report), args.db)
    if args.csv:
        write_csv(results, Path(args.csv))

    total = sum(r.count for r in results)
    errors = sum(r.count for r in results if r.check.severity == "error")
    log.info("%d finding(s) total, %d at error severity", total, errors)

    return 1 if has_errors(results) else 0


if __name__ == "__main__":
    sys.exit(main())
