{{
    config(
        materialized='incremental',
        unique_key='wait_observation_key',
        incremental_strategy='delete+insert'
    )
}}

/*
  Unpivots the four flow columns into one row per (crossing, flow, fetch).
  Long format is the right grain here: it lets a single dashboard filter switch
  between commercial and traveller traffic without duplicating measures.
*/

with source as (

    select *
    from {{ source('raw', 'cbsa_border_waits') }}

    {% if is_incremental() %}
    where fetched_at > (select coalesce(max(fetched_at), '1900-01-01') from {{ this }})
    {% endif %}

),

unpivoted as (

    select observation_id, fetched_at, customs_office, location, last_updated_text,
           'commercial' as traffic_type, 'canada' as direction,
           commercial_canada_bound as delay_text
    from source

    union all

    select observation_id, fetched_at, customs_office, location, last_updated_text,
           'commercial' as traffic_type, 'us' as direction,
           commercial_us_bound as delay_text
    from source

    union all

    select observation_id, fetched_at, customs_office, location, last_updated_text,
           'travellers' as traffic_type, 'canada' as direction,
           travellers_canada_bound as delay_text
    from source

    union all

    select observation_id, fetched_at, customs_office, location, last_updated_text,
           'travellers' as traffic_type, 'us' as direction,
           travellers_us_bound as delay_text
    from source

),

parsed as (

    select
        cast(observation_id as varchar) || '-' || traffic_type || '-' || direction
            as wait_observation_key,
        observation_id,
        fetched_at,
        trim(customs_office)                as crossing_name,
        trim(location)                      as crossing_location,
        traffic_type,
        direction,
        delay_text                          as delay_text_raw,
        {{ parse_delay_minutes('delay_text') }}  as delay_minutes,
        {{ delay_status('delay_text') }}         as delay_status,
        {{ cbsa_local_to_utc('last_updated_text') }} as cbsa_updated_at_utc
    from unpivoted

)

select
    wait_observation_key,
    observation_id,
    fetched_at,
    crossing_name,
    crossing_location,
    traffic_type,
    direction,
    delay_text_raw,
    delay_minutes,
    delay_status,
    cbsa_updated_at_utc,
    -- Time features for the forecasting model, in Pacific terms so BC-facing
    -- users read them naturally.
    extract(hour  from fetched_at at time zone 'America/Vancouver') as local_hour_pt,
    extract(dow   from fetched_at at time zone 'America/Vancouver') as local_dow_pt,
    extract(month from fetched_at at time zone 'America/Vancouver') as local_month_pt
from parsed
