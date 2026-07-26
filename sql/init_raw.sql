-- Canada Trade Gateway Live — raw ingestion schema
-- Run once against your Neon/Supabase Postgres database.
-- Raw layer stores data as-received; parsing/typing happens in dbt staging (ELT).

create schema if not exists raw;

-- One row per vessel per snapshot window (latest position seen during the window)
create table if not exists raw.ais_position_snapshots (
    snapshot_id          bigserial primary key,
    snapshot_ts          timestamptz not null,   -- start of the snapshot window (UTC)
    region               text        not null,   -- 'vancouver' | 'prince_rupert'
    mmsi                 bigint      not null,
    ship_name            text,
    latitude             double precision not null,
    longitude            double precision not null,
    sog                  double precision,       -- speed over ground, knots
    cog                  double precision,       -- course over ground, degrees
    true_heading         integer,
    navigational_status  integer,                -- 0=underway, 1=at anchor, 5=moored ...
    message_ts           timestamptz,            -- time_utc from AIS metadata
    ingested_at          timestamptz not null default now()
);

create index if not exists idx_ais_snap_ts   on raw.ais_position_snapshots (snapshot_ts);
create index if not exists idx_ais_snap_mmsi on raw.ais_position_snapshots (mmsi);

-- Slowly-updating vessel attributes from ShipStaticData messages (upserted)
create table if not exists raw.ais_ship_static (
    mmsi           bigint primary key,
    ship_name      text,
    ship_type      integer,        -- 70-79 = cargo, 80-89 = tanker
    imo_number     bigint,
    call_sign      text,
    destination    text,
    eta_text       text,           -- raw ETA as 'MM-DD HH:MM' from AIS
    dim_a          integer,        -- dimensions relative to GPS antenna, metres
    dim_b          integer,
    dim_c          integer,
    dim_d          integer,
    max_draught    double precision,
    first_seen_at  timestamptz not null default now(),
    last_seen_at   timestamptz not null default now()
);

-- One row per crossing per fetch of the CBSA live feed.
-- Delay values kept as raw text ('No Delay', '15 minutes', 'Not Applicable', '--');
-- parsing to minutes happens in dbt staging.
create table if not exists raw.cbsa_border_waits (
    observation_id            bigserial primary key,
    fetched_at                timestamptz not null,
    customs_office            text not null,
    location                  text,
    last_updated_text         text,   -- e.g. '2026-06-16 13:20 PDT' (local tz abbrev)
    commercial_canada_bound   text,
    commercial_us_bound       text,
    travellers_canada_bound   text,
    travellers_us_bound       text,
    ingested_at               timestamptz not null default now()
);

create index if not exists idx_cbsa_fetched on raw.cbsa_border_waits (fetched_at);
create index if not exists idx_cbsa_office  on raw.cbsa_border_waits (customs_office);
