{#
  Delay values arrive as free text and the vocabulary differs between the live
  feed and the historical archive. Observed across both:

    live feed : 'No Delay' | '15 minutes' | 'Not Applicable' | '--'
    archive   : 'No delay' | '15'         | 'Not applicable' |
                'Closed'   | 'Temporarily closed' | 'Missed entry'

  One macro handles both, so a value never has to be mapped in two places, and
  case is normalised because the two sources disagree on it ('No Delay' vs
  'No delay').

  Returning null rather than 0 for the non-numeric cases matters: averaging a
  closed crossing as zero minutes would understate congestion everywhere.
#}
{% macro parse_delay_minutes(column) %}
    case
        when {{ column }} is null then null
        when lower(trim({{ column }})) = 'no delay' then 0
        when trim({{ column }}) ~ '^[0-9]+$' then
            cast(trim({{ column }}) as integer)
        when {{ column }} ~ '^[0-9]+ minute' then
            cast(substring({{ column }} from '^[0-9]+') as integer)
        when {{ column }} ~ '^[0-9]+ hour' then
            cast(substring({{ column }} from '^[0-9]+') as integer) * 60
        else null
    end
{% endmacro %}

{#
  Reported status, kept alongside the numeric value so every null stays
  explainable. 'missed_entry' is deliberately distinct from 'not_reported':
  the first is a known gap in CBSA's own collection, the second is a lane the
  agency is simply not publishing at that moment.
#}
{% macro delay_status(column) %}
    case
        when {{ column }} is null then 'unknown'
        when lower(trim({{ column }})) = 'not applicable' then 'not_applicable'
        when trim({{ column }}) = '--' then 'not_reported'
        when lower(trim({{ column }})) = 'missed entry' then 'missed_entry'
        when lower(trim({{ column }})) like '%closed%' then 'closed'
        when lower(trim({{ column }})) = 'no delay' then 'reported'
        when trim({{ column }}) ~ '^[0-9]+$' then 'reported'
        when {{ column }} ~ '^[0-9]+ (minute|hour)' then 'reported'
        else 'unknown'
    end
{% endmacro %}
