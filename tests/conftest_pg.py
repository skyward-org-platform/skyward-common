"""Ephemeral-Postgres fixtures for Supabase-backed tests.

Requires TEST_DATABASE_URL pointing at a THROWAWAY Postgres database — the
session fixture drops and recreates the `meta` schema, so never point this at
the real skyward-ops project. Tests skip when TEST_DATABASE_URL is unset.

    docker run -d -e POSTGRES_PASSWORD=pg -p 5432:5432 postgres:16
    export TEST_DATABASE_URL=postgresql://postgres:pg@localhost:5432/postgres
"""
import os

import pytest

from skyward.data.supabase import SupabaseClient

TEST_DB_URL = os.getenv("TEST_DATABASE_URL")
MIGRATIONS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "db", "supabase", "migrations"
)

requires_pg = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="session")
def _pg_schema():
    """Apply all meta migrations (in order) once per session against the throwaway DB."""
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    import glob
    import psycopg

    migrations = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql")))
    with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:
        conn.execute("drop schema if exists meta cascade")
        for path in migrations:
            with open(path) as f:
                conn.execute(f.read())
    yield


@pytest.fixture
def pg_client(_pg_schema):
    """A SupabaseClient whose writes are rolled back after each test."""
    sb = SupabaseClient(TEST_DB_URL)
    sb._conn.autocommit = False
    yield sb
    sb._conn.rollback()
    sb.close()
