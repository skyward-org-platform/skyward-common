"""Read-only drift check: BQ Meta.* (live, still written by consumers) vs the
skyward-ops Supabase meta.* snapshot taken at migration time.

Accounts for the intentional migration transforms so they don't show as drift:
  * projects/project_domains: the 15/17 -> 22-25 split is reverse-mapped.
  * dropped legacy/derived columns and volatile timestamps are excluded.

Reports, per table: row counts, rows only in BQ (added/modified since the
snapshot = real drift to pull), and rows only in Supabase (deleted in BQ, or
unexplained). No writes.
"""
from __future__ import annotations

import os
from collections import Counter

import numpy as np
import pandas as pd

from skyward.config import load_config
from skyward.data.bigquery import BigQueryClient
from skyward.data.supabase import SupabaseClient

# PG split ids -> original BQ collided ids, so projects/project_domains compare
# cleanly. Tracks the migration's dynamic split output (re-check after any rebuild):
# 23,24 were under BQ project 15; 25,26 were under BQ project 17.
REVERSE_SPLIT = {23: 15, 24: 15, 25: 17, 26: 17}

# Stable business columns to compare per table (exclude derived/volatile/dropped).
TABLES = {
    "clients": ["client_id", "client_name", "abbreviation", "is_active", "notes"],
    "domains": ["domain_id", "domain", "domain_name", "is_active", "notes"],
    "client_domains": ["client_id", "domain_id", "is_competitor", "priority", "notes"],
    "projects": ["project_id", "client_id", "project_type", "project_name", "status", "notes"],
    "project_domains": ["project_id", "domain_id", "role", "priority", "notes"],
    "dataset_catalog": ["dataset", "dataset_type", "hostname", "is_standardized", "owner", "active"],
    "client_datasets": ["client_id", "domain_id", "dataset_id", "is_active", "notes"],
    "table_catalog": ["dataset", "table_name", "row_count", "size_bytes", "is_active"],
}


def norm(v):
    """Normalize a cell so BQ and PG values compare equal."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def rows_counter(df, cols):
    return Counter(tuple(norm(r[c]) for c in cols) for _, r in df.iterrows())


def main():
    cfg = load_config()
    bq = BigQueryClient(project_id=cfg.datahub_project_id, credentials_info=cfg.datahub_credentials)
    sb = SupabaseClient(os.environ.get("SUPABASE_DB_URL") or cfg.supabase_db_url)
    P = cfg.datahub_project_id

    print(f"{'table':18} {'BQ':>7} {'PG':>7} {'only-BQ':>8} {'only-PG':>8}  status")
    total_drift = 0
    for table, cols in TABLES.items():
        collist = ", ".join(cols)
        bq_df = bq.client.query(f"SELECT {collist} FROM `{P}.Meta.{table}`").result().to_dataframe()
        pg_df = sb.query(f"SELECT {collist} FROM meta.{table}")
        if table in ("projects", "project_domains"):
            pg_df = pg_df.copy()
            pg_df["project_id"] = pg_df["project_id"].map(lambda x: REVERSE_SPLIT.get(int(x), int(x)))

        bq_c = rows_counter(bq_df, cols)
        pg_c = rows_counter(pg_df, cols)
        only_bq = bq_c - pg_c
        only_pg = pg_c - bq_c
        n_bq, n_pg = sum(only_bq.values()), sum(only_pg.values())
        total_drift += n_bq + n_pg
        status = "clean" if (n_bq == 0 and n_pg == 0) else "DRIFT"
        print(f"{table:18} {len(bq_df):>7} {len(pg_df):>7} {n_bq:>8} {n_pg:>8}  {status}")

        for label, ctr in (("only-in-BQ (added/modified since snapshot)", only_bq),
                            ("only-in-PG (deleted in BQ / unexplained)", only_pg)):
            if ctr:
                print(f"    {label}:")
                for tup, cnt in list(ctr.items())[:15]:
                    print(f"      {dict(zip(cols, tup))}" + (f"  x{cnt}" if cnt > 1 else ""))
                if len(ctr) > 15:
                    print(f"      ... (+{len(ctr)-15} more)")

    sb.close()
    print(f"\n{'NO DRIFT — Supabase matches BQ' if total_drift == 0 else f'DRIFT DETECTED: {total_drift} differing row(s)'}")


if __name__ == "__main__":
    main()
