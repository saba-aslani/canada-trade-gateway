-- Historical CBSA border wait times.
-- Run once, after sql/init_raw.sql.
--
-- The historical files use a different shape from the live feed: comma
-- delimited, Canada-bound only, and delays given as bare numbers rather than
-- '15 minutes'. They therefore land in their own table rather than being
-- forced into the live schema, and are reconciled in dbt.

create table if not exists raw.cbsa_border_waits_historical (
    historical_id    bigserial primary key,
    source_file      text not null,      -- provenance, so a bad file can be traced
    customs_office   text not null,
    location         text,
    updated_text     text,               -- e.g. '2015-01-02 14:51 PST'
    commercial_flow  text,               -- 'No delay' | '15' | 'Missed entry'
    travellers_flow  text,
    ingested_at      timestamptz not null default now()
);

create index if not exists idx_hist_office on raw.cbsa_border_waits_historical (customs_office);
create index if not exists idx_hist_file   on raw.cbsa_border_waits_historical (source_file);

-- Load ledger: makes the backfill idempotent. A file already recorded here is
-- skipped, so the script can be interrupted and rerun without duplicating rows.
create table if not exists raw.cbsa_backfill_files (
    source_file  text primary key,
    row_count    bigint not null,
    loaded_at    timestamptz not null default now()
);
