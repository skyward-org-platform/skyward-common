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

# ── SAFETY GUARD ───────────────────────────────────────────────────────────
# The _pg_schema fixture DROPS AND RECREATES the `meta` schema. It must NEVER
# run against a production DB. Known-production identifiers are hard-denied, and
# any non-local host requires an explicit ALLOW_DESTRUCTIVE_TEST_DB=1 opt-in.
# (On 2026-06-10 a TEST_DATABASE_URL pointed at skyward-ops wiped live Meta.)
_PROD_DENYLIST = ("ycvkkukiulygmmkcpsnt", "supabase.co", "pooler.supabase.com")


def _assert_safe_test_db(url: str) -> None:
    low = url.lower()
    for needle in _PROD_DENYLIST:
        if needle in low:
            pytest.exit(
                f"REFUSING to run destructive schema fixture: TEST_DATABASE_URL points at a "
                f"production/Supabase host ({needle!r}). This fixture DROPS the `meta` schema. "
                f"Use a throwaway local Postgres (e.g. localhost:5432). Aborting.",
                returncode=2,
            )
    is_local = any(h in low for h in ("@localhost", "@127.0.0.1", "@::1", "@host.docker.internal"))
    if not is_local and os.getenv("ALLOW_DESTRUCTIVE_TEST_DB") != "1":
        pytest.exit(
            "REFUSING to run destructive schema fixture against a non-local TEST_DATABASE_URL "
            "without ALLOW_DESTRUCTIVE_TEST_DB=1. This fixture DROPS the `meta` schema. Aborting.",
            returncode=2,
        )


@pytest.fixture(scope="session")
def _pg_schema():
    """Apply all meta migrations (in order) once per session against the throwaway DB."""
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    _assert_safe_test_db(TEST_DB_URL)  # never drop a prod/non-local schema
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
