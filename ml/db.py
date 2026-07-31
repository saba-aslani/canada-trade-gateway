"""Shared Postgres connection helper. Requires DATABASE_URL env var."""
import os

import psycopg2


def get_connection():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL is not set. "
            "Expected a Postgres connection string, e.g. "
            "postgresql://user:pass@host/dbname?sslmode=require"
        )
    return psycopg2.connect(dsn)
