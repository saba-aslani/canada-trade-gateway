# Canada Trade Gateway Live

**[View the live dashboard →](https://canada-trade-gateway-dashboard.streamlit.app/)**
*(hosted on a free tier — the first visit after a quiet period may take a few seconds to wake)*

A real-time analytics pipeline watching the two arteries most Canadian import
freight moves through: the Pacific container ports and the commercial land
border. Vessel positions and border delays are collected every 15 minutes,
modelled into a tested warehouse, and published to a public dashboard alongside
a wait-time model trained on ten years of CBSA history.

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

**Land border.** Commercial delay, Canada-bound, across 26 CBSA land crossings,
banded from clear to severe and tracked by hour.

**Learned delay profile.** Expected commercial wait by crossing, weekday and
hour, from a model trained on 2016–2018 readings and tested on 2019.

---

## Architecture

```
AISstream (WebSocket)  ──┐
                         ├──►  Python ingestion  ──►  Neon Postgres (raw)
CBSA open data (CSV)  ───┘         │                        │
                                   │                        ▼
              external scheduler   │                 dbt (staging → marts)
              every 15 min ────────┘                        │
                                                            ▼
                                              Streamlit dashboard (public,
                                              read-only database role)
```

| Layer | Choice | Why |
|---|---|---|
| Ingestion | Python, GitHub Actions | No always-on server; scheduling is versioned with the code |
| Storage | Neon Postgres | Free tier, region-matched to BC (`us-west-2`) to keep dashboard latency low |
| Modelling | dbt — 9 models, 43 data tests | Transformations are reviewable, tested, and rebuilt on every run |
| Model | XGBoost hurdle model | Two stages, because the target is mostly zero |
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
reports `Not Applicable`; a crossing CBSA is not currently reporting shows `--`;
the archive adds `Closed`, `Temporarily closed` and `Missed entry`. All become
null with the reason preserved in `delay_status`. Treating them as zero would
silently understate congestion in every average. `Missed entry` is kept distinct
from `not reported` — the first is a gap in CBSA's own collection, the second is
a lane the agency is simply not publishing.

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
default into the cargo counts.

**Snapshot window length biases the sample.** Vessels under way transmit every
few seconds; vessels at anchor transmit as slowly as every three minutes. A
short listening window therefore over-samples moving vessels — precisely the
opposite of what a congestion metric wants. The scheduled job listens for 240
seconds for this reason.

**The historical archive is a view, not a table.** 1.24M parsed rows would
consume most of the free-tier storage budget to gain a marginally faster scan.
Only the aggregate the model trains on is persisted, leaving room for the live
collection, which is the part that grows.

---

## The wait-time model

Roughly three quarters of commercial readings are "No delay", so a single
regressor would spend its capacity predicting zero and report a flattering error
on a target that is mostly zero. The model is therefore a **hurdle model**:

| Stage | Question | Method |
|---|---|---|
| 1 | Will there be a delay at all? | XGBoost classifier |
| 2 | How long, given that there is one? | XGBoost regressor on `log1p(minutes)` |

Expected delay is the product of the two, which keeps the two operational
questions separate: *will I be held up*, and *for how long if I am*.

**Validation is temporal, not random.** Readings from the same hour are near
duplicates, so a random split leaks and produces a meaningless score. Training
uses 2016–2018 (772,185 readings); the test set is 2019 (257,251 readings), a
period the model never sees.

**A lookup-table baseline is scored alongside.** Every feature is categorical —
crossing, hour, weekday, month — so a per-cell historical mean is a genuinely
strong competitor. Reporting the model without that comparison would overstate
what it contributes.

### Results

| Metric | Value |
|---|---|
| Stage 1 ROC-AUC (2019 holdout) | 0.960 |
| Stage 1 PR-AUC | 0.880 |
| Stage 1 Brier score | 0.067 |
| Combined MAE | 1.85 min |
| Lookup-table baseline MAE | 2.03 min |
| Improvement over baseline | **9.1%** |

The classifier is strong; the magnitude stage is not. A 9.1% gain over a
per-cell mean is modest, and the honest reading is that with purely categorical
time features there is limited headroom above a well-constructed lookup table.
The model's real contribution is calibrated probability, not minute-level
precision.

### Does a pre-pandemic model still describe today?

The archive ends in 2019; this project's own collection began in 2026. Scoring
the 2016–2018 model against readings collected since:

| | 2019 holdout | Live 2026 |
|---|---|---|
| Share of readings showing a delay | 24.1% | **15.0%** |
| Model MAE | 1.85 min | 1.54 min |

Commercial delays are materially less frequent now than the training period
describes. The model's absolute error is lower simply because there is less
delay to predict — a reminder that a stable error metric can hide a shifted
distribution underneath.

---

## Data sources and licensing

| Source | Content | Terms |
|---|---|---|
| [AISstream.io](https://aisstream.io) | Real-time AIS vessel positions and static data | Free API, subject to AISstream's terms of service |
| [CBSA Border Wait Times](https://open.canada.ca/data/en/dataset/000fe5aa-1d77-42d1-bfe7-458c51dacfef) | Live feed and 2016–2019 archive | Open Government Licence — Canada |

The MIT licence in this repository covers the code only. The data remains
subject to the terms above, and the dashboard is indicative rather than
operational guidance.

---

## Known limitations

- Port areas are rectangular bounding boxes, not berth and anchorage polygons,
  so a vessel moored at a downtown terminal counts the same as one waiting at
  Roberts Bank.
- Only vessels broadcasting AIS within those boxes are visible; the feed is not
  a complete port call record.
- CBSA publishes granular historical wait times through 2019 only, so the model
  is trained entirely on pre-pandemic traffic and validated against current
  readings rather than retrained on them.
- Month-specific cells in the training profile hold roughly 50 readings each —
  about sixteen distinct days per crossing, hour and weekday across four years.
  Hourly patterns are well supported; month-by-month differences are thinner and
  should be read as indicative.
- The dashboard runs on Streamlit Community Cloud, which suspends idle
  containers, so the first visit after a quiet period takes a few seconds.

---

## Engineering decisions worth explaining

**Scheduling is triggered externally, not by GitHub's cron.** GitHub's scheduled
workflows are best-effort and were being throttled to roughly one run every two
hours — unacceptable for a dashboard that claims to be live. An external
scheduler now calls the `workflow_dispatch` API every 15 minutes using a
fine-grained token scoped to a single repository with Actions permission only.
Manual dispatches are not throttled, so cadence became reliable without moving
off free infrastructure.

**The public dashboard connects as a read-only role.** `dashboard_reader` can
select from the modelled schemas and nothing else; it has no access to the raw
landing tables and no write permission anywhere. Ingestion and dbt use the owner
role.

**dbt core and adapter versions are both pinned in CI.** Pinning only the
adapter let pip resolve a pre-release core that does not yet support Postgres,
and a build that was green locally failed in CI.

**Transient source failures are retried, and independent steps fail
independently.** A single CBSA timeout used to kill a whole scheduled run. The
fetcher now retries with exponential backoff, and the vessel snapshot and
warehouse build continue even when the border feed is down — while the run still
reports failure, so a broken feed stays visible rather than being swallowed.

**Empty ingestion windows fail loudly.** An AIS window that receives no messages
exits non-zero rather than reporting success, so a revoked key or stream outage
surfaces as a red run instead of a dashboard that silently stops moving.

---

## Running it yourself

```bash
# 1. Create the schemas in any Postgres database
psql "$DATABASE_URL" -f sql/init_raw.sql
psql "$DATABASE_URL" -f sql/init_raw_historical.sql

# 2. Collect one live reading
pip install -r requirements.txt
cd ingest
DATABASE_URL=... python cbsa_fetch.py
DATABASE_URL=... AISSTREAM_API_KEY=... python ais_snapshot.py

# 3. Backfill the archive (optional, ~226 MB for 2016-2019)
python cbsa_backfill.py --from-year 2016 --to-year 2019

# 4. Build and test the warehouse
cd ../dbt_gateway && dbt deps && dbt build

# 5. Train the model
cd ../ml && pip install -r requirements.txt
DATABASE_URL=... python train_wait_model.py

# 6. Serve the dashboard
streamlit run ../dashboard/streamlit_app.py
```

---

## Repository layout

```
ingest/          collectors for AIS and CBSA, plus the historical backfill
sql/             landing-schema DDL
dbt_gateway/     dbt project: 9 models, 43 data tests
ml/              hurdle model training and evaluation
dashboard/       Streamlit app and the read-only role definition
.github/         scheduled ingestion and warehouse build
```

---

Built by [Saba Aslani](https://saba-aslani.github.io) ·
[LinkedIn](https://www.linkedin.com/in/saba-aslani) ·
[Repository](https://github.com/saba-aslani/canada-trade-gateway)
