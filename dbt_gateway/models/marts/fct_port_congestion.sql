{{
    config(
        materialized='incremental',
        unique_key='congestion_key',
        incremental_strategy='delete+insert'
    )
}}

/*
  Grain: one row per region per snapshot window.

  This is the headline operational metric. Stationary cargo vessels inside a
  port bounding box are the practical proxy for congestion: a ship that is not
  moving and not alongside is waiting, and waiting is what turns into demurrage
  and missed terminal appointments downstream.

  Vessel category is joined from the vessel dimension rather than recomputed,
  and vessels whose static data has not arrived yet fall into 'unknown' rather
  than being silently counted as cargo.
*/

with positions as (

    select *
    from {{ ref('stg_vessel_positions') }}

    {% if is_incremental() %}
    where snapshot_ts > (select coalesce(max(snapshot_ts), '1900-01-01') from {{ this }})
    {% endif %}

),

vessels as (

    select mmsi, vessel_category
    from {{ ref('dim_vessels') }}

),

joined as (

    select
        p.snapshot_ts,
        p.region,
        p.mmsi,
        p.is_stationary,
        p.speed_over_ground_kn,
        coalesce(v.vessel_category, 'unknown') as vessel_category
    from positions p
    left join vessels v
        on p.mmsi = v.mmsi

)

select
    {{ dbt_utils.generate_surrogate_key(['snapshot_ts', 'region']) }} as congestion_key,
    snapshot_ts,
    region,
    count(*)                                                as vessels_total,
    count(*) filter (where is_stationary)                   as vessels_stationary,
    count(*) filter (where vessel_category = 'cargo')       as vessels_cargo,
    count(*) filter (where vessel_category = 'cargo' and is_stationary)
                                                            as cargo_stationary,
    count(*) filter (where vessel_category = 'tanker')      as vessels_tanker,
    count(*) filter (where vessel_category = 'unknown')     as vessels_unknown_category,
    round(cast(avg(speed_over_ground_kn) as numeric), 2)    as avg_speed_kn,
    -- Share of traffic sitting still. Rising values over consecutive snapshots
    -- are the early warning signal a forwarder actually cares about.
    round(
        cast(count(*) filter (where is_stationary) as numeric)
        / nullif(count(*), 0), 3
    )                                                       as stationary_share,
    extract(hour from snapshot_ts at time zone 'America/Vancouver') as local_hour_pt,
    extract(dow  from snapshot_ts at time zone 'America/Vancouver') as local_dow_pt
from joined
group by snapshot_ts, region
