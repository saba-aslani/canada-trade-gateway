"""Backfill historical CBSA border wait times into raw.cbsa_border_waits_historical.

Resource URLs are discovered from the Open Government CKAN API rather than
constructed by hand, because the quarterly file naming is not perfectly regular
(the first 2014 file starts on 05 April, and the pre-2015 data ships as a single
combined file). Asking the catalogue avoids guessing and silently missing
quarters.

The load is idempotent: every file that finishes loading is recorded in
raw.cbsa_backfill_files and skipped on the next run, so an interrupted backfill
can simply be restarted.

Usage:
    python cbsa_backfill.py                 # everything the catalogue offers
    python cbsa_backfill.py --from-year 2022
    python cbsa_backfill.py --from-year 2022 --to-year 2025 --dry-run

Storage note: the full archive is several million rows. On a free-tier database
start with recent years, check consumption, then extend.
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import re
import sys

import requests

from db import get_connection

PACKAGE_ID = "000fe5aa-1d77-42d1-bfe7-458c51dacfef"
CKAN_URL = f"https://open.canada.ca/data/api/action/package_show?id={PACKAGE_ID}"

COPY_BATCH_ROWS = 50_000
EXPECTED_COLUMNS = 5

# The same catalogue entry also ships a monthly service-standard summary
# (Port of Entry / Month / Fiscal Year / Percentage Met Service Standard).
# It is a different dataset with a different grain, so it is identified by its
# header and skipped rather than being coerced into the wait-times table.
WAIT_TIME_HEADER_MARKERS = ("office", "location", "updated")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cbsa_backfill")


def discover_resources() -> list[tuple[str, int | None]]:
    """Return [(url, representative_year)] for English CSV resources."""
    resp = requests.get(CKAN_URL, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("success"):
        raise RuntimeError("CKAN did not return a successful response")

    found: list[tuple[str, int | None]] = []
    for resource in payload["result"].get("resources", []):
        url = (resource.get("url") or "").strip()
        if not url.lower().endswith(".csv"):
            continue
        # Skip French editions: '-fra.csv' and '-fr.csv'.
        if re.search(r"-(fra|fr)\.csv$", url, re.IGNORECASE):
            continue
        years = [int(y) for y in re.findall(r"(20\d{2})", url)]
        found.append((url, min(years) if years else None))

    # Deterministic order makes an interrupted run resume predictably.
    return sorted(found, key=lambda item: (item[1] or 0, item[0]))


def already_loaded(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("select source_file from raw.cbsa_backfill_files")
        return {row[0] for row in cur.fetchall()}


def load_file(conn, url: str) -> int:
    """Stream one CSV into Postgres with COPY. Returns rows written."""
    source_file = url.rsplit("/", 1)[-1]

    with requests.get(url, timeout=300, stream=True) as resp:
        resp.raise_for_status()
        resp.encoding = "utf-8-sig"
        reader = csv.reader(io.StringIO(resp.text))

        header = next(reader, None)
        if header is None:
            raise ValueError(f"{source_file} is empty")

        normalised = [h.strip().lower() for h in header]
        looks_right = (
            len(normalised) >= EXPECTED_COLUMNS
            and all(
                any(marker in column for column in normalised)
                for marker in WAIT_TIME_HEADER_MARKERS
            )
        )
        if not looks_right:
            log.warning(
                "skip %s: not the wait-times schema (header: %s)",
                source_file, header,
            )
            return -1

        total = 0
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        batch = 0

        with conn.cursor() as cur:
            for row in reader:
                if len(row) < EXPECTED_COLUMNS:
                    continue
                writer.writerow([
                    source_file,
                    row[0].strip(),
                    row[1].strip(),
                    row[2].strip(),
                    row[3].strip(),
                    row[4].strip(),
                ])
                batch += 1

                if batch >= COPY_BATCH_ROWS:
                    buffer.seek(0)
                    cur.copy_expert(
                        "copy raw.cbsa_border_waits_historical "
                        "(source_file, customs_office, location, updated_text, "
                        "commercial_flow, travellers_flow) from stdin with csv",
                        buffer,
                    )
                    total += batch
                    log.info("  %s: %d rows", source_file, total)
                    buffer = io.StringIO()
                    writer = csv.writer(buffer)
                    batch = 0

            if batch:
                buffer.seek(0)
                cur.copy_expert(
                    "copy raw.cbsa_border_waits_historical "
                    "(source_file, customs_office, location, updated_text, "
                    "commercial_flow, travellers_flow) from stdin with csv",
                    buffer,
                )
                total += batch

            cur.execute(
                "insert into raw.cbsa_backfill_files (source_file, row_count) "
                "values (%s, %s) on conflict (source_file) do update "
                "set row_count = excluded.row_count, loaded_at = now()",
                (source_file, total),
            )

    # One commit per file: a failure mid-backfill leaves whole files, never
    # half of one, and the ledger stays consistent with the data.
    conn.commit()
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-year", type=int, default=None)
    parser.add_argument("--to-year", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be loaded and exit")
    args = parser.parse_args()

    resources = discover_resources()
    # An undated filename cannot be placed in a range, so it is dropped when a
    # range is requested rather than silently slipping through every filter.
    if args.from_year:
        resources = [(u, y) for u, y in resources if y is not None and y >= args.from_year]
    if args.to_year:
        resources = [(u, y) for u, y in resources if y is not None and y <= args.to_year]

    if not resources:
        log.error("No matching resources found — check the year range.")
        return 1

    if args.dry_run:
        for url, year in resources:
            log.info("would load [%s] %s", year, url)
        log.info("%d file(s) selected.", len(resources))
        return 0

    conn = get_connection()
    conn.autocommit = False
    try:
        done = already_loaded(conn)
        grand_total = 0
        skipped = 0
        for url, year in resources:
            source_file = url.rsplit("/", 1)[-1]
            if source_file in done:
                log.info("skip %s (already loaded)", source_file)
                continue
            log.info("loading [%s] %s", year, source_file)
            try:
                written = load_file(conn, url)
                if written < 0:
                    skipped += 1
                    continue
                grand_total += written
            except Exception as exc:
                conn.rollback()
                log.error("failed on %s: %s", source_file, exc)
                log.error("Rerun the script to resume; loaded files are skipped.")
                return 1
        log.info(
            "Backfill complete. %d new rows, %d file(s) skipped as a different schema.",
            grand_total, skipped,
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
