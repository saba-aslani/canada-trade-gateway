with source as (

    select *
    from {{ source('raw', 'ais_ship_static') }}

)

select
    mmsi,
    nullif(trim(ship_name), '')     as ship_name,
    ship_type,
    -- ITU-R M.1371 ship type ranges. Cargo (70-79) is the container/breakbulk
    -- band that matters for freight; tankers are kept separate because they
    -- distort dwell statistics (they anchor far longer by design).
    case
        when ship_type between 70 and 79 then 'cargo'
        when ship_type between 80 and 89 then 'tanker'
        when ship_type between 60 and 69 then 'passenger'
        when ship_type between 30 and 39 then 'fishing_special'
        when ship_type between 50 and 59 then 'service'
        when ship_type is null then 'unknown'
        else 'other'
    end                             as vessel_category,
    imo_number,
    nullif(trim(call_sign), '')     as call_sign,
    nullif(trim(destination), '')   as destination,
    eta_text,
    -- AIS reports dimensions relative to the GPS antenna: A+B is overall
    -- length, C+D is beam. Larger length is a rough proxy for vessel capacity.
    case when dim_a is not null and dim_b is not null
         then dim_a + dim_b end     as length_m,
    case when dim_c is not null and dim_d is not null
         then dim_c + dim_d end     as beam_m,
    max_draught,
    first_seen_at,
    last_seen_at
from source
