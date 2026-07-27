{{
    config(
        materialized='incremental',
        unique_key='snapshot_id',
        incremental_strategy='delete+insert'
    )
}}

/*
  Decodes AIS navigational status and derives an "at anchor" flag.

  Why two signals instead of one: navigational_status is self-reported by the
  crew and is frequently stale or left on the wrong value. Speed over ground is
  objective. We treat a vessel as effectively stationary if either the reported
  status says anchored/moored OR speed is under 0.5 knots, and we keep both
  columns so the dashboard can show how often the two disagree.
*/

with source as (

    select *
    from {{ source('raw', 'ais_position_snapshots') }}

    {% if is_incremental() %}
    where snapshot_ts > (select coalesce(max(snapshot_ts), '1900-01-01') from {{ this }})
    {% endif %}

)

select
    snapshot_id,
    snapshot_ts,
    region,
    mmsi,
    nullif(trim(ship_name), '')             as ship_name,
    latitude,
    longitude,
    sog                                     as speed_over_ground_kn,
    cog                                     as course_over_ground_deg,
    true_heading,
    navigational_status,
    case navigational_status
        when 0 then 'under_way_engine'
        when 1 then 'at_anchor'
        when 2 then 'not_under_command'
        when 3 then 'restricted_manoeuvrability'
        when 4 then 'constrained_by_draught'
        when 5 then 'moored'
        when 6 then 'aground'
        when 7 then 'engaged_in_fishing'
        when 8 then 'under_way_sailing'
        when 15 then 'undefined'
        else 'other'
    end                                     as nav_status_desc,
    case
        when navigational_status in (1, 5) then true
        when sog is not null and sog < 0.5 then true
        else false
    end                                     as is_stationary,
    -- Reported-vs-observed disagreement, useful as a data-quality metric.
    case
        when navigational_status in (1, 5) and sog is not null and sog >= 0.5
            then true
        else false
    end                                     as status_speed_conflict,
    message_ts,
    ingested_at
from source
