{{ config(materialized='view') }}

/*
  Historical CBSA readings, 2016-2020, unpivoted to one row per crossing per
  traffic type per observation.

  Materialised as a view on purpose. The archive is static and already occupies
  most of the free-tier storage budget; duplicating 1.2M rows into a table to
  gain nothing but a marginally faster scan would be a poor trade. Only the
  aggregate the model actually trains on is persisted.

  The archive carries no direction column: CBSA publishes these readings for
  traffic entering Canada, so direction is set explicitly rather than left
  implicit for whoever reads the model next.
*/

with source as (

    select *
    from {{ source('raw', 'cbsa_border_waits_historical') }}

),

unpivoted as (

    select historical_id, source_file, customs_office, location, updated_text,
           'commercial' as traffic_type, commercial_flow as delay_text
    from source

    union all

    select historical_id, source_file, customs_office, location, updated_text,
           'travellers' as traffic_type, travellers_flow as delay_text
    from source

),

parsed as (

    select
        cast(historical_id as varchar) || '-' || traffic_type as wait_observation_key,
        historical_id,
        source_file,
        trim(customs_office)                        as crossing_name,
        trim(location)                              as crossing_location,
        traffic_type,
        'canada'                                    as direction,
        delay_text                                  as delay_text_raw,
        {{ parse_delay_minutes('delay_text') }}     as delay_minutes,
        {{ delay_status('delay_text') }}            as delay_status,
        {{ cbsa_local_to_utc('updated_text') }}     as observed_at_utc
    from unpivoted

)

select
    wait_observation_key,
    historical_id,
    source_file,
    crossing_name,
    crossing_location,
    traffic_type,
    direction,
    delay_text_raw,
    delay_minutes,
    delay_status,
    observed_at_utc,
    -- Local Pacific time is the frame BC-facing users read naturally, and the
    -- busiest commercial crossings in this dataset are in BC and Ontario.
    extract(hour  from observed_at_utc at time zone 'America/Vancouver') as local_hour_pt,
    extract(dow   from observed_at_utc at time zone 'America/Vancouver') as local_dow_pt,
    extract(month from observed_at_utc at time zone 'America/Vancouver') as local_month_pt,
    extract(year  from observed_at_utc at time zone 'America/Vancouver') as local_year_pt
from parsed
where observed_at_utc is not null
