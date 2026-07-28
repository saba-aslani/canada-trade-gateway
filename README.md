# Canada Trade Gateway Live

**[View the live dashboard →](https://canada-trade-gateway-dashboard.streamlit.app/)**

A real-time analytics pipeline watching the two arteries most Canadian import
freight moves through: the Pacific container ports and the commercial land
border. Vessel positions and border delays are collected every 15 minutes,
modelled into a tested warehouse, and published to a public dashboard.

Everything below runs on live, freely licensed data. Nothing is simulated.

---

## The problem

A ship sitting at anchor outside Vancouver and a truck queue at Pacific Highway
are the same problem wearing different clothes: cargo that has arrived but
cannot move. Both cost money by the hour — demurrage and detention on the ocean
side, missed delivery windows and driver hours on the land side.

Large forwarders buy visibility into this from vendors like project44 or Kpler.
Small and mid-size importers, brokers, and forwarders generally cannot justify
that spend, so they find out a lane is congested after the invoice arrives.

This project shows how far public data and an open-source stack get you toward
the same picture.

---

## What it measures

**Pacific gateway.** Vessels inside the Vancouver and Prince Rupert port
approaches, with the share currently holding position rather than making way.
A rising holding share across consecutive readings is the earliest public signal
of berth and anchorage pressure.

**Land border.** Commercial delay, Canada-bound, across the 29 busiest CBSA land
crossings, banded from clear to severe and tracked by hour.

---

## Architecture

```
AISstream (WebSocket)  ──┐
                         ├──►  Python ingestion  ──►  Neon Postgres (raw)
CBSA open data (CSV)  ───┘         │                        │
                                   │                        ▼
                    GitHub Actions │                 dbt (staging → marts)
                    every 15 min ──┘                        │
                                                            ▼
                                              Streamlit dashboard (public,
                                              read-only database role)
```

| Layer | Choice | Why |
|---|---|---|
| Ingestion | Python, GitHub Actions cron | No always-on server needed; scheduling is versioned with the code |
| Storage | Neon Postgres | Free tier, region-matched to BC (`us-west-2`) to keep dashboard latency low |
| Modelling | dbt, incremental marts, 28 data tests | Transformations are reviewable, tested, and rebuilt in CI |
| Serving | Streamlit Community Cloud | Public URL with no hosting cost |

The dbt build runs **inside the same scheduled job as ingestion**. Landing rows
without rebuilding the marts would leave a "live" dashboard quietly frozen at
the last manual build — a failure mode that looks healthy from the outside.

---

## Modelling decisions worth explaining

**Border delays are unpivoted to long format.** The CBSA feed publishes four
flow columns per crossing (commercial and traveller, each direction). Staging
turns these into one row per crossing per flow per fetch, so a single dashboard
filter switches between commercial and traveller traffic without duplicating
measures.

**"Not Applicable" is null, not zero.** A crossing with no commercial lane
reports `Not Applicable`; a crossing CBSA is not currently reporting shows `--`.
Both become null with the reason preserved in `delay_status`. Treating them as
zero would silently understate congestion in every average.

**Vessel stillness comes from two signals.** AIS navigational status is
self-reported by the crew and is frequently stale or left on the wrong value.
Speed over ground is objective. A vessel counts as holding if either the
reported status says anchored or moored, or speed is under 0.5 knots. Both
columns are kept, plus a `status_speed_conflict` flag that measures how often
the two disagree — a genuine data-quality signal rather than a hidden
assumption.

**The vessel dimension is driven by observed positions, not static data.**
`ShipStaticData` messages broadcast far less often than position reports, so at
any moment most tracked vessels have no ship type yet. A left join keeps those
vessels visible as `unknown` instead of dropping them or, worse, letting them
default into the cargo counts. Coverage improves as the stream accumulates.

**Snapshot window length biases the sample.** Vessels under way transmit every
few seconds; vessels at anchor transmit as slowly as every three minutes. A
short listening window therefore over-samples moving vessels — precisely the
opposite of what a congestion metric wants. The scheduled job listens for 240
seconds for this reason.

---

## Engineering decisions worth explaining

**The public dashboard connects as a read-only role.** `dashboard_reader` can
select from the modelled schemas and nothing else; it has no access to the raw
landing tables and no write permission anywhere. Ingestion and dbt use the owner
role.

**dbt core and adapter versions are both pinned in CI.** Pinning only the
adapter let pip resolve a pre-release core that does not yet support Postgres,
and a build that was green locally failed in CI. Both are now pinned to the
locally tested versions.

**Empty ingestion windows fail loudly.** An AIS window that receives no messages
exits non-zero rather than reporting success, so a revoked key or stream outage
surfaces as a red run instead of a dashboard that silently stops moving.

**Concurrency is capped at one run.** GitHub's scheduler can delay jobs under
load; without a concurrency group, a late run could overlap the next one and
write duplicate snapshots.

---

## Data sources and licensing

| Source | Content | Terms |
|---|---|---|
| [AISstream.io](https://aisstream.io) | Real-time AIS vessel positions and static data | Free API, subject to AISstream's terms of service |
| [CBSA Border Wait Times](https://open.canada.ca/data/en/dataset/d4a716f5-a2fc-4c3c-88ed-451fe05900e4) | Estimated wait times at the busiest land crossings | Open Government Licence — Canada |

The MIT licence in this repository covers the code only. The data remains
subject to the terms above, and the dashboard is indicative rather than
operational guidance.

---

## Known limitations

- Port areas are rectangular bounding boxes, not berth and anchorage polygons,
  so a vessel moored at a downtown cruise terminal counts the same as one
  waiting at Roberts Bank.
- Only vessels broadcasting AIS within those boxes are visible; the feed is not
  a complete port call record.
- Historical CBSA wait times published back to 2010 are not yet loaded, so
  seasonal and day-of-week patterns cannot yet be characterised.
- GitHub's scheduler is best-effort — readings can be delayed or occasionally
  skipped, which is acceptable here and visible in the timestamps.

---

## Running it yourself

```bash
# 1. Create the landing schema in any Postgres database
psql "$DATABASE_URL" -f sql/init_raw.sql

# 2. Collect one reading
pip install -r requirements.txt
cd ingest
DATABASE_URL=... AISSTREAM_API_KEY=... python cbsa_fetch.py
DATABASE_URL=... AISSTREAM_API_KEY=... python ais_snapshot.py

# 3. Build and test the warehouse
cd ../dbt_gateway
dbt deps && dbt build

# 4. Serve the dashboard
pip install -r ../dashboard/requirements.txt
streamlit run ../dashboard/streamlit_app.py
```

Scheduled collection needs three repository secrets — `DATABASE_URL`,
`AISSTREAM_API_KEY`, and the `NEON_*` trio used by the CI dbt profile.

---

## Repository layout

```
ingest/          collectors for AIS and CBSA, plus the shared DB helper
sql/             landing-schema DDL
dbt_gateway/     dbt project: 3 staging models, 4 marts, 28 data tests
dashboard/       Streamlit app and the read-only role definition
.github/         scheduled ingestion and warehouse build
```

Built by [Saba Aslani](https://saba-aslani.github.io) ·
[LinkedIn](https://www.linkedin.com/in/saba-aslani)
