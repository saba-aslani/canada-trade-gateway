/*
  One row per border crossing. Attributes are derived from the feed itself
  rather than hard-coded, so a crossing added or renamed by CBSA flows through
  without a code change.
*/

with observations as (

    select *
    from {{ ref('stg_border_waits') }}

),

crossings as (

    select
        crossing_name,
        max(crossing_location)                              as crossing_location,
        min(fetched_at)                                     as first_observed_at,
        max(fetched_at)                                     as last_observed_at,
        -- A crossing handles commercial traffic if it has ever reported a real
        -- commercial value; 'Not Applicable' means the lane does not exist.
        max(case when traffic_type = 'commercial'
                  and delay_status = 'reported'
                 then 1 else 0 end)                         as handles_commercial_int,
        count(*)                                            as observation_count
    from observations
    group by crossing_name

)

select
    {{ dbt_utils.generate_surrogate_key(['crossing_name']) }}  as crossing_key,
    crossing_name,
    crossing_location,
    -- 'Surrey, BC/Blaine, WA' -> Canadian side is before the slash.
    trim(split_part(split_part(crossing_location, '/', 1), ',', 2)) as canada_province,
    trim(split_part(split_part(crossing_location, '/', 2), ',', 2)) as us_state,
    case
        when trim(split_part(split_part(crossing_location, '/', 1), ',', 2)) = 'BC'
            then 'pacific'
        when trim(split_part(split_part(crossing_location, '/', 1), ',', 2)) = 'ON'
            then 'ontario'
        when trim(split_part(split_part(crossing_location, '/', 1), ',', 2)) = 'QC'
            then 'quebec'
        when trim(split_part(split_part(crossing_location, '/', 1), ',', 2))
             in ('NB', 'NS', 'PE', 'NL') then 'atlantic'
        else 'prairie'
    end                                                     as region_group,
    cast(handles_commercial_int as boolean)                 as handles_commercial,
    first_observed_at,
    last_observed_at,
    observation_count
from crossings
