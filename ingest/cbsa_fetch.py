"""Fetch CBSA live border wait times and append to raw.cbsa_border_waits.

Source: https://www.cbsa-asfc.gc.ca/bwt-taf/bwt-eng.csv
(Open Government Licence - Canada)

Feed format (verified 2026): UTF-8 with BOM, ';;'-delimited, columns:
  Customs Office ;; Location ;; Last updated ;;
  Commercial Flow - Canada bound ;; Commercial Flow - U.S. bound ;;
  Travellers Flow - Canada bound ;; Travellers Flow - U.S. bound

Delay values are raw text ('No Delay', '15 minutes', 'Not Applicable', '--')
and are stored as-is; parsing happens in dbt staging.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

import requests

from db import get_connection

FEED_URL = "https://www.cbsa-asfc.gc.ca/bwt-taf/bwt-eng.csv"
DELIMITER = ";;"
EXPECTED_MIN_COLUMNS = 7

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cbsa_fetch")


def parse_feed(text: str) -> list[dict]:
    """Parse the ';;'-delimited CBSA feed into row dicts."""
    rows: list[dict] = []
    lines = [ln.strip() for ln in text.lstrip("\ufeff").splitlines() if ln.strip()]
    if not lines:
        raise ValueError("CBSA feed returned no lines")

    # First line is the header; validate it looks right so schema drift fails loudly.
    header = [c.strip() for c in lines[0].split(DELIMITER)]
    if not header or "customs office" not in header[0].lower():
        raise ValueError(f"Unexpected CBSA header: {lines[0]!r}")

    for line in lines[1:]:
        cells = [c.strip() for c in line.split(DELIMITER)]
        if len(cells) < EXPECTED_MIN_COLUMNS:
            log.warning("Skipping malformed line: %r", line)
            continue
        rows.append(
            {
                "customs_office": cells[0],
                "location": cells[1],
                "last_updated_text": cells[2],
                "commercial_canada_bound": cells[3],
                "commercial_us_bound": cells[4],
                "travellers_canada_bound": cells[5],
                "travellers_us_bound": cells[6],
            }
        )
    return rows


def main() -> int:
    fetched_at = datetime.now(timezone.utc)

    resp = requests.get(FEED_URL, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8-sig"
    rows = parse_feed(resp.text)

    if not rows:
        log.error("Parsed 0 rows from CBSA feed — aborting without writing.")
        return 1

    insert_sql = """
        insert into raw.cbsa_border_waits (
            fetched_at, customs_office, location, last_updated_text,
            commercial_canada_bound, commercial_us_bound,
            travellers_canada_bound, travellers_us_bound
        ) values (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                insert_sql,
                [
                    (
                        fetched_at,
                        r["customs_office"],
                        r["location"],
                        r["last_updated_text"],
                        r["commercial_canada_bound"],
                        r["commercial_us_bound"],
                        r["travellers_canada_bound"],
                        r["travellers_us_bound"],
                    )
                    for r in rows
                ],
            )
    log.info("Inserted %d border-wait observations at %s", len(rows), fetched_at.isoformat())
    return 0


if __name__ == "__main__":
    sys.exit(main())
