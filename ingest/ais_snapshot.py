"""Take a short real-time AIS snapshot around Canada's Pacific gateway ports.

Connects to the free aisstream.io WebSocket API, listens for LISTEN_SECONDS,
keeps the latest position per vessel (MMSI), and writes:
  - raw.ais_position_snapshots : one row per vessel seen in the window
  - raw.ais_ship_static        : upserted vessel attributes (name, type, ETA...)

Vessel-type filtering (e.g. cargo only) deliberately happens downstream in dbt,
because PositionReport messages don't carry ship type — only ShipStaticData does.

Env vars:
  AISSTREAM_API_KEY  (free key from https://aisstream.io)
  DATABASE_URL       (Postgres connection string)
  AIS_LISTEN_SECONDS (optional, default 240)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

import websockets

from db import get_connection

AIS_WS_URL = "wss://stream.aisstream.io/v0/stream"
LISTEN_SECONDS = int(os.environ.get("AIS_LISTEN_SECONDS", "240"))

# Bounding boxes as [[lat_sw, lon_sw], [lat_ne, lon_ne]]
REGIONS = {
    # Vancouver harbour + English Bay anchorages + Roberts Bank (Deltaport)
    "vancouver": [[48.95, -123.65], [49.45, -122.85]],
    # Prince Rupert harbour + Fairview terminal approaches
    "prince_rupert": [[54.05, -130.60], [54.45, -130.15]],
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ais_snapshot")


def region_for(lat: float, lon: float) -> str | None:
    for name, ((lat_sw, lon_sw), (lat_ne, lon_ne)) in REGIONS.items():
        if lat_sw <= lat <= lat_ne and lon_sw <= lon <= lon_ne:
            return name
    return None


def parse_message_ts(meta: dict) -> datetime | None:
    """MetaData.time_utc looks like '2026-06-16 20:15:03.123456789 +0000 UTC'."""
    raw = meta.get("time_utc")
    if not raw:
        return None
    try:
        head = raw.split(" +")[0]           # '2026-06-16 20:15:03.123456789'
        date_part, time_part = head.split(" ")
        if "." in time_part:
            hms, frac = time_part.split(".")
            time_part = f"{hms}.{frac[:6]}"  # trim to microseconds
        return datetime.fromisoformat(f"{date_part} {time_part}").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, IndexError):
        return None


async def collect() -> tuple[dict, dict]:
    """Listen for LISTEN_SECONDS; return (positions_by_mmsi, statics_by_mmsi)."""
    positions: dict[int, dict] = {}
    statics: dict[int, dict] = {}

    subscription = {
        "APIKey": os.environ["AISSTREAM_API_KEY"],
        "BoundingBoxes": [REGIONS["vancouver"], REGIONS["prince_rupert"]],
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }

    async with websockets.connect(AIS_WS_URL) as ws:
        await ws.send(json.dumps(subscription))
        log.info("Subscribed; listening for %ds ...", LISTEN_SECONDS)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + LISTEN_SECONDS

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("MessageType")
            meta = msg.get("MetaData", {}) or {}
            mmsi = meta.get("MMSI")
            if mmsi is None:
                continue

            if msg_type == "PositionReport":
                body = (msg.get("Message") or {}).get("PositionReport") or {}
                lat = body.get("Latitude", meta.get("latitude"))
                lon = body.get("Longitude", meta.get("longitude"))
                if lat is None or lon is None:
                    continue
                region = region_for(lat, lon)
                if region is None:
                    continue  # stream can deliver edge messages just outside boxes
                positions[mmsi] = {
                    "region": region,
                    "ship_name": (meta.get("ShipName") or "").strip() or None,
                    "latitude": lat,
                    "longitude": lon,
                    "sog": body.get("Sog"),
                    "cog": body.get("Cog"),
                    "true_heading": body.get("TrueHeading"),
                    "navigational_status": body.get("NavigationalStatus"),
                    "message_ts": parse_message_ts(meta),
                }
            elif msg_type == "ShipStaticData":
                body = (msg.get("Message") or {}).get("ShipStaticData") or {}
                dim = body.get("Dimension") or {}
                eta = body.get("Eta") or {}
                eta_text = None
                if eta:
                    eta_text = (
                        f"{eta.get('Month', 0):02d}-{eta.get('Day', 0):02d} "
                        f"{eta.get('Hour', 0):02d}:{eta.get('Minute', 0):02d}"
                    )
                statics[mmsi] = {
                    "ship_name": (body.get("Name") or "").strip() or None,
                    "ship_type": body.get("Type"),
                    "imo_number": body.get("ImoNumber"),
                    "call_sign": (body.get("CallSign") or "").strip() or None,
                    "destination": (body.get("Destination") or "").strip() or None,
                    "eta_text": eta_text,
                    "dim_a": dim.get("A"),
                    "dim_b": dim.get("B"),
                    "dim_c": dim.get("C"),
                    "dim_d": dim.get("D"),
                    "max_draught": body.get("MaximumStaticDraught"),
                }

    return positions, statics


def write(snapshot_ts: datetime, positions: dict, statics: dict) -> None:
    pos_sql = """
        insert into raw.ais_position_snapshots (
            snapshot_ts, region, mmsi, ship_name, latitude, longitude,
            sog, cog, true_heading, navigational_status, message_ts
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    static_sql = """
        insert into raw.ais_ship_static (
            mmsi, ship_name, ship_type, imo_number, call_sign, destination,
            eta_text, dim_a, dim_b, dim_c, dim_d, max_draught, last_seen_at
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        on conflict (mmsi) do update set
            ship_name    = coalesce(excluded.ship_name,   raw.ais_ship_static.ship_name),
            ship_type    = coalesce(excluded.ship_type,   raw.ais_ship_static.ship_type),
            imo_number   = coalesce(excluded.imo_number,  raw.ais_ship_static.imo_number),
            call_sign    = coalesce(excluded.call_sign,   raw.ais_ship_static.call_sign),
            destination  = coalesce(excluded.destination, raw.ais_ship_static.destination),
            eta_text     = coalesce(excluded.eta_text,    raw.ais_ship_static.eta_text),
            dim_a        = coalesce(excluded.dim_a,       raw.ais_ship_static.dim_a),
            dim_b        = coalesce(excluded.dim_b,       raw.ais_ship_static.dim_b),
            dim_c        = coalesce(excluded.dim_c,       raw.ais_ship_static.dim_c),
            dim_d        = coalesce(excluded.dim_d,       raw.ais_ship_static.dim_d),
            max_draught  = coalesce(excluded.max_draught, raw.ais_ship_static.max_draught),
            last_seen_at = now()
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                pos_sql,
                [
                    (
                        snapshot_ts, p["region"], mmsi, p["ship_name"],
                        p["latitude"], p["longitude"], p["sog"], p["cog"],
                        p["true_heading"], p["navigational_status"], p["message_ts"],
                    )
                    for mmsi, p in positions.items()
                ],
            )
            cur.executemany(
                static_sql,
                [
                    (
                        mmsi, s["ship_name"], s["ship_type"], s["imo_number"],
                        s["call_sign"], s["destination"], s["eta_text"],
                        s["dim_a"], s["dim_b"], s["dim_c"], s["dim_d"],
                        s["max_draught"],
                    )
                    for mmsi, s in statics.items()
                ],
            )


def main() -> int:
    snapshot_ts = datetime.now(timezone.utc)
    positions, statics = asyncio.run(collect())
    if not positions and not statics:
        log.error(
            "No AIS messages received in %ds. Usually an invalid or revoked "
            "API key, or a stream outage. Failing loudly so the run does not "
            "report success with no data.",
            LISTEN_SECONDS,
        )
        return 1
    write(snapshot_ts, positions, statics)
    log.info(
        "Snapshot %s: %d vessel positions, %d static records.",
        snapshot_ts.isoformat(), len(positions), len(statics),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
