-- Run once in the Neon SQL Editor.
-- The dashboard is public, so it connects with a role that can read the
-- modelled marts and nothing else. Ingestion and dbt keep using the owner role.

create role dashboard_reader with login password 'CHOOSE_A_STRONG_PASSWORD';

grant connect on database neondb to dashboard_reader;

grant usage on schema analytics_marts, analytics_staging to dashboard_reader;

grant select on all tables in schema analytics_marts, analytics_staging
    to dashboard_reader;

-- Tables that dbt rebuilds later should inherit the same grant.
alter default privileges in schema analytics_marts
    grant select on tables to dashboard_reader;
alter default privileges in schema analytics_staging
    grant select on tables to dashboard_reader;

-- Deliberately not granted: the raw schema. The dashboard has no reason to
-- read unmodelled landing data.
