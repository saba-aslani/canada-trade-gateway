/*
  One row per vessel (MMSI).

  Vessels are seen in position reports long before their static data arrives,
  because ShipStaticData broadcasts far less frequently than position reports.
  The join is therefore driven by observed positions and left-joined to static
  attributes, so a vessel is never dropped just because we have not caught its
  static message yet. Coverage of vessel_category improves as the stream runs.
*/

with observed as (

    select
        mmsi,
        max(ship_name)      as ais_ship_name,
        min(snapshot_ts)    as first_seen_at,
        max(snapshot_ts)    as last_seen_at,
        count(*)            as position_report_count
    from {{ ref('stg_vessel_positions') }}
    group by mmsi

),

static_data as (

    select *
    from {{ ref('stg_vessels') }}

)

select
    o.mmsi,
    coalesce(s.ship_name, o.ais_ship_name)          as ship_name,
    coalesce(s.vessel_category, 'unknown')          as vessel_category,
    s.ship_type,
    s.imo_number,
    s.call_sign,
    s.destination,
    s.eta_text,
    s.length_m,
    s.beam_m,
    s.max_draught,
    case when s.mmsi is not null then true else false end as has_static_data,
    o.first_seen_at,
    o.last_seen_at,
    o.position_report_count
from observed o
left join static_data s
    on o.mmsi = s.mmsi
