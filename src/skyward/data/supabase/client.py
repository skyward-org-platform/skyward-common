from __future__ import annotations

import pandas as pd
import psycopg


class SupabaseClient:
    """Project-level transport for the skyward-ops Supabase (Postgres) project.

    Analogue of ``BigQueryClient``. Reused by ``MetaClient`` and future
    client-data modules. Connect via the Supabase session/transaction pooler
    connection string (``SUPABASE_DB_URL``).

    The connection is opened in **autocommit** mode so each statement is
    independent (mirroring how every BigQuery query is its own job). Callers
    that need a multi-statement transaction — the per-test fixture and the
    one-time migration loader — set ``client._conn.autocommit = False`` and
    drive ``commit()`` / ``rollback()`` themselves.
    """

    def __init__(self, db_url: str):
        if not db_url:
            raise ValueError("SupabaseClient requires a connection string (SUPABASE_DB_URL)")
        self._conn = psycopg.connect(db_url, autocommit=True)

    def query(self, sql: str, params: dict | None = None) -> pd.DataFrame:
        """Run a SELECT and return a DataFrame (empty with column names if no rows)."""
        with self._conn.cursor() as cur:
            cur.execute(sql, params or {})
            cols = [d.name for d in cur.description] if cur.description else []
            rows = cur.fetchall() if cur.description else []
        return pd.DataFrame(rows, columns=cols)

    def execute(self, sql: str, params: dict | None = None) -> list:
        """Run an INSERT/UPDATE/DELETE. Returns RETURNING rows if any.

        Relies on the connection's autocommit setting: in the default
        autocommit mode psycopg commits each statement automatically; when a
        caller has set ``autocommit = False`` they own the transaction.
        """
        with self._conn.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchall() if cur.description else []

    def close(self) -> None:
        self._conn.close()
