{#
  CBSA publishes 'Last updated' as local wall time plus a timezone abbreviation,
  e.g. '2026-06-16 13:20 PDT'. Postgres cannot parse most of these abbreviations
  reliably, so we map each one to a fixed UTC offset and shift manually.

  Note: the abbreviation itself already encodes whether daylight time is in
  effect (PDT vs PST), so a static offset map is correct here.
#}
{% macro cbsa_local_to_utc(column) %}
    (
        cast(substring({{ column }} from 1 for 16) as timestamp)
        + make_interval(
            hours => case right(trim({{ column }}), 3)
                        when 'NDT' then 2   -- Newfoundland handled below
                        when 'ADT' then 3
                        when 'AST' then 4
                        when 'EDT' then 4
                        when 'EST' then 5
                        when 'CDT' then 5
                        when 'CST' then 6
                        when 'MDT' then 6
                        when 'MST' then 7
                        when 'PDT' then 7
                        when 'PST' then 8
                        else 0
                     end,
            mins  => case right(trim({{ column }}), 3)
                        when 'NDT' then 30  -- UTC-2:30
                        when 'NST' then 30  -- UTC-3:30
                        else 0
                     end
        )
    ) at time zone 'UTC'
{% endmacro %}
