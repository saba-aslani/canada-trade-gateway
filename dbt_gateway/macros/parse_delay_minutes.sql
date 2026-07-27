{#
  CBSA delay values arrive as free text. Observed values:
    'No Delay'        -> 0 minutes
    '15 minutes'      -> 15
    '1 hour'          -> 60      (rare, seen during peak congestion)
    'Not Applicable'  -> null    (lane/flow does not exist at this crossing)
    '--'              -> null    (not reported by CBSA)
    'Closed'          -> null    (crossing closed; tracked separately as a status)

  Returning null rather than 0 for the non-numeric cases matters: averaging
  'Not Applicable' as zero would silently understate congestion.
#}
{% macro parse_delay_minutes(column) %}
    case
        when {{ column }} is null then null
        when lower(trim({{ column }})) = 'no delay' then 0
        when {{ column }} ~ '^[0-9]+ minute' then
            cast(substring({{ column }} from '^[0-9]+') as integer)
        when {{ column }} ~ '^[0-9]+ hour' then
            cast(substring({{ column }} from '^[0-9]+') as integer) * 60
        else null
    end
{% endmacro %}

{#
  Reported status, kept alongside the numeric value so nulls stay explainable.
#}
{% macro delay_status(column) %}
    case
        when {{ column }} is null then 'unknown'
        when lower(trim({{ column }})) = 'not applicable' then 'not_applicable'
        when trim({{ column }}) = '--' then 'not_reported'
        when lower(trim({{ column }})) like '%closed%' then 'closed'
        when lower(trim({{ column }})) = 'no delay' then 'reported'
        when {{ column }} ~ '^[0-9]+ (minute|hour)' then 'reported'
        else 'unknown'
    end
{% endmacro %}
