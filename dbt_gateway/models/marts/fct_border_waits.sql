{{
    config(
        materialized='incremental',
        unique_key='wait_observation_key',
        incremental_strategy='delete+insert'
    )
}}

/*
  Grain: one row per crossing per traffic type per direction per fetch.

  Only rows with delay_status = 'reported' carry a numeric delay_minutes;
  everything else is kept with a null measure so the dashboard can distinguish
  "no delay" from "not reported" instead of quietly averaging them together.
*/

with waits as (

    select *
    from {{ ref('stg_border_waits') }}

    {% if is_incremental() %}
    where fetched_at > (select coalesce(max(fetched_at), '1900-01-01') from {{ this }})
    {% endif %}

),

crossings as (

    select crossing_key, crossing_name, region_group, canada_province
    from {{ ref('dim_crossings') }}

)

select
    w.wait_observation_key,
    c.crossing_key,
    w.crossing_name,
    c.region_group,
    c.canada_province,
    w.traffic_type,
    w.direction,
    w.fetched_at,
    w.cbsa_updated_at_utc,
    w.delay_minutes,
    w.delay_status,
    w.delay_text_raw,
    w.local_hour_pt,
    w.local_dow_pt,
    w.local_month_pt,
    -- Staleness of the underlying CBSA reading at the moment we fetched it.
    -- Large values mean the feed itself is lagging, not that traffic changed.
    cast(
        extract(epoch from (w.fetched_at - w.cbsa_updated_at_utc)) / 60
        as integer
    )                                                   as feed_lag_minutes,
    case
        when w.delay_minutes is null then null
        when w.delay_minutes = 0  then 'clear'
        when w.delay_minutes <= 15 then 'light'
        when w.delay_minutes <= 30 then 'moderate'
        when w.delay_minutes <= 60 then 'heavy'
        else 'severe'
    end                                                 as congestion_band
from waits w
left join crossings c
    on w.crossing_name = c.crossing_name
