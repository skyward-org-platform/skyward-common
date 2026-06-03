"""One-time migration of the BigQuery Meta.* dataset into the skyward-ops
Supabase project (meta schema).

Safety model:
  * Default mode is DRY-RUN: validates FK integrity and prints row counts, NO writes.
  * Pass --apply to actually load data into Supabase.
  * IDs are preserved exactly (OVERRIDING SYSTEM VALUE), then IDENTITY sequences
    are reset to MAX(id)+1 so future inserts continue cleanly.
  * Load order respects foreign keys.

Usage:
    # dry-run: FK validation + parity preview (no writes)
    python scripts/migrate_meta_to_supabase.py

    # apply the migration (writes to Supabase)
    python scripts/migrate_meta_to_supabase.py --apply

Environment:
    SUPABASE_DB_URL          target Supabase connection string
    GCP_DATAHUB_PROJECT_ID   source BQ project (defaults to data-hub-468216)
"""
from __future__ import annotations

import argparse
import os
import sys

# FK-safe load order: parents before children.
LOAD_ORDER = [
    "clients",
    "domains",
    "dataset_catalog",
    "client_domains",
    "projects",
    "client_datasets",
    "table_catalog",
    "project_domains",
]

# Tables whose integer PK is a Postgres IDENTITY column (need OVERRIDING SYSTEM VALUE).
IDENTITY_TABLES = {"clients": "client_id", "domains": "domain_id", "projects": "project_id"}

ALL_TABLES = [
    "clients", "domains", "client_domains", "projects",
    "project_domains", "dataset_catalog", "client_datasets", "table_catalog",
]


def find_orphans(clients, domains, client_domains, projects=None,
                 project_domains=None, client_datasets=None):
    """Return FK-violating rows that BigQuery tolerated but Postgres will reject.

    Args are sets/lists of ids and tuples:
      clients, domains: sets of valid ids
      client_domains: list of (client_id, domain_id)
      projects: list of (project_id, client_id)
      project_domains: list of (project_id, domain_id)
      client_datasets: list of (client_id, domain_id_or_None, dataset_id)
    """
    out = {
        "client_domains_bad_client": [(c, d) for (c, d) in client_domains if c not in clients],
        "client_domains_bad_domain": [(c, d) for (c, d) in client_domains if d not in domains],
    }
    if projects is not None:
        out["projects_bad_client"] = [(p, c) for (p, c) in projects if c not in clients]
    if project_domains is not None:
        project_ids = {p for (p, _) in (projects or [])}
        out["project_domains_bad_project"] = [
            (p, d) for (p, d) in project_domains if projects is not None and p not in project_ids
        ]
        out["project_domains_bad_domain"] = [
            (p, d) for (p, d) in project_domains if d not in domains
        ]
    if client_datasets is not None:
        out["client_datasets_bad_client"] = [
            (c, dom, ds) for (c, dom, ds) in client_datasets if c not in clients
        ]
        out["client_datasets_bad_domain"] = [
            (c, dom, ds) for (c, dom, ds) in client_datasets
            if dom is not None and dom not in domains
        ]
    return out


def _read_bq_table(bq, project_id, table):
    return bq.client.query(
        f"SELECT * FROM `{project_id}.Meta.{table}`"
    ).result().to_dataframe()


def validate(bq, project_id):
    """Read BQ Meta, run FK checks, print a report. Returns the orphans dict."""
    clients_df = _read_bq_table(bq, project_id, "clients")
    domains_df = _read_bq_table(bq, project_id, "domains")
    cd_df = _read_bq_table(bq, project_id, "client_domains")
    proj_df = _read_bq_table(bq, project_id, "projects")
    pd_df = _read_bq_table(bq, project_id, "project_domains")
    clds_df = _read_bq_table(bq, project_id, "client_datasets")

    client_ids = set(clients_df["client_id"].tolist())
    domain_ids = set(domains_df["domain_id"].tolist())

    orphans = find_orphans(
        clients=client_ids,
        domains=domain_ids,
        client_domains=list(zip(cd_df["client_id"], cd_df["domain_id"])),
        projects=list(zip(proj_df["project_id"], proj_df["client_id"])),
        project_domains=list(zip(pd_df["project_id"], pd_df["domain_id"])),
        client_datasets=list(zip(
            clds_df["client_id"],
            clds_df["domain_id"] if "domain_id" in clds_df else [None] * len(clds_df),
            clds_df["dataset_id"],
        )),
    )
    total = sum(len(v) for v in orphans.values())
    print("=== FK validation ===")
    for key, rows in orphans.items():
        marker = "OK " if not rows else "BAD"
        print(f"  [{marker}] {key}: {len(rows)}")
        for r in rows[:10]:
            print(f"          {r}")
    print(f"  total orphan rows: {total}")
    return orphans


def load_table(sb, table, df):
    """Insert a BQ DataFrame into meta.<table>, preserving identity IDs."""
    if df.empty:
        return 0
    cols = list(df.columns)
    collist = ", ".join(cols)
    placeholders = ", ".join(f"%({c})s" for c in cols)
    override = "OVERRIDING SYSTEM VALUE" if table in IDENTITY_TABLES else ""
    sql = f"INSERT INTO meta.{table} ({collist}) {override} VALUES ({placeholders})"
    records = df.where(df.notna(), None).to_dict("records")
    with sb._conn.cursor() as cur:
        cur.executemany(sql, records)
    sb._conn.commit()
    return len(df)


def reset_sequence(sb, table, id_col):
    """Reset a table's IDENTITY sequence to MAX(id)+1 (or 1 if empty)."""
    sb.execute(
        f"SELECT setval(pg_get_serial_sequence('meta.{table}', %(c)s), "
        f"COALESCE((SELECT MAX({id_col}) FROM meta.{table}), 1))",
        {"c": id_col},
    )


def verify(bq, sb, project_id):
    """Return {table: (bq_count, pg_count)} for any table whose counts differ."""
    mismatches = {}
    for table in ALL_TABLES:
        bq_n = bq.client.query(
            f"SELECT COUNT(*) AS c FROM `{project_id}.Meta.{table}`"
        ).result().to_dataframe().iloc[0]["c"]
        pg_n = sb.query(f"SELECT COUNT(*) AS c FROM meta.{table}").iloc[0]["c"]
        if int(bq_n) != int(pg_n):
            mismatches[table] = (int(bq_n), int(pg_n))
    return mismatches


def main(argv=None):
    parser = argparse.ArgumentParser(description="Migrate BQ Meta.* into Supabase meta schema.")
    parser.add_argument("--apply", action="store_true", help="Actually write to Supabase (default: dry-run).")
    parser.add_argument("--force-orphans", action="store_true",
                        help="Proceed with --apply even if FK orphans are found (they will be skipped).")
    args = parser.parse_args(argv)

    from skyward.config import load_config
    from skyward.data.bigquery import BigQueryClient
    from skyward.data.supabase import SupabaseClient

    cfg = load_config()
    project_id = cfg.datahub_project_id
    if not cfg.supabase_db_url:
        sys.exit("SUPABASE_DB_URL is not set.")

    bq = BigQueryClient()
    sb = SupabaseClient(cfg.supabase_db_url)

    orphans = validate(bq, project_id)
    total_orphans = sum(len(v) for v in orphans.values())

    if not args.apply:
        print("\nDRY-RUN only. Re-run with --apply to load data into Supabase.")
        return

    if total_orphans and not args.force_orphans:
        sys.exit("Refusing to --apply: FK orphans present. Resolve them or pass --force-orphans.")

    print("\n=== Loading (--apply) ===")
    for table in LOAD_ORDER:
        df = _read_bq_table(bq, project_id, table)
        n = load_table(sb, table, df)
        print(f"  loaded meta.{table}: {n} rows")

    print("\n=== Resetting IDENTITY sequences ===")
    for table, id_col in IDENTITY_TABLES.items():
        reset_sequence(sb, table, id_col)
        print(f"  reset meta.{table}.{id_col}")

    print("\n=== Parity verification ===")
    mismatches = verify(bq, sb, project_id)
    if mismatches:
        print("  MISMATCH:")
        for t, (b, p) in mismatches.items():
            print(f"    {t}: BQ={b} PG={p}")
        sys.exit("Row-count parity FAILED.")
    print("  all tables match.")


if __name__ == "__main__":
    main()
