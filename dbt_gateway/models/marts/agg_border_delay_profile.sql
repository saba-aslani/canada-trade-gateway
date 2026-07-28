{{ config(materialized='table') }}

/*
  Grain: one row per crossing per traffic type per hour-of-day per day-of-week
  per month.

  This is the training surface for the wait-time model, and the aggregation is
  a modelling decision rather than a convenience:

  1. Raw readings are irregular. CBSA sometimes publishes several observations
     within the same minute, so an unweighted regression on raw rows would let
     chatty periods dominate.
  2. The target is heavily zero-inflated — roughly three quarters of commercial
     readings are 'No delay'. Carrying the share of delayed readings alongside
     the conditional median makes that structure explicit instead of letting a
     single mean hide it.
  3. Persisting an aggregate rather than 1.2M parsed rows keeps the free-tier
     storage budget available for live collection, which is the part that grows.

  Only readings with a genuine measurement are aggregated. Closed crossings and
  CBSA's own missed entries are counted separately so coverage stays visible.
*/

with observations as (

    select *
    from {{ ref('stg_border_waits_historical') }}

),

measured as (

    select *
    from observations
    where delay_status = 'reported'
      and delay_minutes is not null

),

profile as (

    select
        crossing_name,
        traffic_type,
        local_hour_pt,
        local_dow_pt,
        local_month_pt,
        count(*)                                            as observations,
        count(*) filter (where delay_minutes > 0)           as delayed_observations,
        round(
            cast(count(*) filter (where delay_minutes > 0) as numeric)
            / nullif(count(*), 0), 4
        )                                                   as delay_probability,
        -- Conditional statistics: given that a delay occurred, how long was it.
        -- Reported separately from the unconditional mean because the two answer
        -- different operational questions.
        round(cast(avg(delay_minutes) as numeric), 2)       as mean_delay_minutes,
        percentile_cont(0.5) within group (order by delay_minutes)
                                                            as median_delay_minutes,
        percentile_cont(0.9) within group (order by delay_minutes)
                                                            as p90_delay_minutes,
        max(delay_minutes)                                  as max_delay_minutes,
        round(cast(stddev_samp(delay_minutes) as numeric), 2) as stddev_delay_minutes
    from measured
    group by 1, 2, 3, 4, 5

),

coverage as (

    select
        crossing_name,
        traffic_type,
        local_hour_pt,
        local_dow_pt,
        local_month_pt,
        count(*) filter (where delay_status = 'closed')       as closed_observations,
        count(*) filter (where delay_status = 'missed_entry') as missed_observations,
        count(*)                                              as total_observations
    from observations
    group by 1, 2, 3, 4, 5

)

select
    {{ dbt_utils.generate_surrogate_key([
        'p.crossing_name', 'p.traffic_type', 'p.local_hour_pt',
        'p.local_dow_pt', 'p.local_month_pt'
    ]) }}                                       as profile_key,
    p.crossing_name,
    p.traffic_type,
    cast(p.local_hour_pt as integer)            as hour_of_day,
    cast(p.local_dow_pt as integer)             as day_of_week,
    cast(p.local_month_pt as integer)           as month_of_year,
    case when p.local_dow_pt in (0, 6) then true else false end as is_weekend,
    case
        when p.local_month_pt in (12, 1, 2) then 'winter'
        when p.local_month_pt in (3, 4, 5)  then 'spring'
        when p.local_month_pt in (6, 7, 8)  then 'summer'
        else 'autumn'
    end                                         as season,
    p.observations,
    p.delayed_observations,
    p.delay_probability,
    p.mean_delay_minutes,
    p.median_delay_minutes,
    p.p90_delay_minutes,
    p.max_delay_minutes,
    p.stddev_delay_minutes,
    c.closed_observations,
    c.missed_observations,
    c.total_observations,
    round(
        cast(p.observations as numeric) / nullif(c.total_observations, 0), 4
    )                                           as measurement_coverage
from profile p
left join coverage c
    on  p.crossing_name  = c.crossing_name
    and p.traffic_type   = c.traffic_type
    and p.local_hour_pt  = c.local_hour_pt
    and p.local_dow_pt   = c.local_dow_pt
    and p.local_month_pt = c.local_month_pt
