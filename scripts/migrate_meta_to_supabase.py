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
import re
import sys

import pandas as pd

# BQ Meta.projects ids 15 & 17 each collided 3 distinct per-domain projects due to
# the old get_next_id race. Split them into distinct ids. The domain that KEEPS the
# original id (highest WQA work, approved 2026-06-04); the others are assigned FRESH
# ids ABOVE the current max(project_id) at run time, so the split never collides with
# ids BQ has allocated since (BQ keeps writing until cutover). project_name encodes
# the domain (e.g. "bushire.com.au_001"); project_domains links are re-pointed by
# resolving domain_id -> domain.
PROJECT_SPLIT_KEEP = {15: "tnabushire.com.au", 17: "bushire.co.nz"}


def _domain_from_project_name(name):
    """'bushire.com.au_001' -> 'bushire.com.au' (strip a trailing _NNN suffix)."""
    return re.sub(r"_\d+$", "", str(name))


def build_split_map(projects_df):
    """Return {(old_project_id, domain): new_project_id} for collided projects.

    Fresh ids are allocated above the current max(project_id) so they can't clash
    with ids BQ allocated after the original snapshot.
    """
    proj = projects_df
    collided = sorted(set(proj["project_id"][proj["project_id"].duplicated(keep=False)]))
    next_id = int(proj["project_id"].max()) + 1
    remap = {}
    for oid in collided:
        grp = proj[proj["project_id"] == oid]
        domains = [_domain_from_project_name(n) for n in grp["project_name"]]
        keeper = PROJECT_SPLIT_KEEP.get(oid)
        if keeper not in domains:
            keeper = domains[0]  # fallback: first row keeps the original id
        kept = False
        for dom in domains:
            if dom == keeper and not kept:
                remap[(oid, dom)] = oid
                kept = True
            else:
                remap[(oid, dom)] = next_id
                next_id += 1
    return remap


def split_collided_projects(projects_df, project_domains_df, domains_df):
    """Split race-collided projects into distinct ids.

    Returns (new_projects_df, new_project_domains_df). Rows not in the split map
    are unchanged. Raises if the result still has duplicate project_ids.
    """
    id2dom = dict(zip(domains_df["domain_id"], domains_df["domain"]))
    remap = build_split_map(projects_df)

    proj = projects_df.copy()
    proj["project_id"] = proj.apply(
        lambda r: remap.get((r["project_id"], _domain_from_project_name(r["project_name"])), r["project_id"]),
        axis=1,
    )
    dup = proj["project_id"][proj["project_id"].duplicated()].tolist()
    if dup:
        raise RuntimeError(f"projects still has duplicate ids after split: {sorted(set(dup))}")

    pd_df = project_domains_df.copy()
    pd_df["project_id"] = pd_df.apply(
        lambda r: remap.get((r["project_id"], id2dom.get(r["domain_id"])), r["project_id"]),
        axis=1,
    )
    return proj, pd_df

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

    # client_datasets.domain_id is nullable (client-level datasets). Coerce
    # pandas NA -> None so a legitimately-null domain isn't flagged as an orphan.
    if "domain_id" in clds_df:
        clds_domain = [None if pd.isna(x) else int(x) for x in clds_df["domain_id"]]
    else:
        clds_domain = [None] * len(clds_df)

    orphans = find_orphans(
        clients=client_ids,
        domains=domain_ids,
        client_domains=list(zip(cd_df["client_id"], cd_df["domain_id"])),
        projects=list(zip(proj_df["project_id"], proj_df["client_id"])),
        project_domains=list(zip(pd_df["project_id"], pd_df["domain_id"])),
        client_datasets=list(zip(clds_df["client_id"], clds_domain, clds_df["dataset_id"])),
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


def _pg_columns(sb, table):
    """Column names of meta.<table> in the target Supabase schema."""
    df = sb.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'meta' AND table_name = %(t)s",
        {"t": table},
    )
    return set(df["column_name"].tolist())


def load_table(sb, table, df):
    """Insert a BQ DataFrame into meta.<table>, preserving identity IDs.

    Only columns that exist in the target Supabase table are loaded; any extra
    BQ columns (e.g. legacy standardization flags) are dropped and logged.
    """
    if df.empty:
        return 0
    target_cols = _pg_columns(sb, table)
    cols = [c for c in df.columns if c in target_cols]
    dropped = [c for c in df.columns if c not in target_cols]
    if dropped:
        print(f"    (dropping non-schema columns from {table}: {dropped})")
    collist = ", ".join(cols)
    placeholders = ", ".join(f"%({c})s" for c in cols)
    override = "OVERRIDING SYSTEM VALUE" if table in IDENTITY_TABLES else ""
    sql = f"INSERT INTO meta.{table} ({collist}) {override} VALUES ({placeholders})"
    subset = df[cols]
    # Cast to object dtype FIRST so NA/NaT become real Python None. Without the
    # astype, .where on a datetime64 column keeps NaT (psycopg then serializes the
    # NaT sentinel into a bogus far-future timestamp), and NaN stays in float cols.
    records = subset.astype(object).where(subset.notna(), None).to_dict("records")
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

    bq = BigQueryClient(project_id=project_id, credentials_info=cfg.datahub_credentials)
    sb = SupabaseClient(cfg.supabase_db_url)

    orphans = validate(bq, project_id)
    total_orphans = sum(len(v) for v in orphans.values())

    if not args.apply:
        print("\nDRY-RUN only. Re-run with --apply to load data into Supabase.")
        return

    if total_orphans and not args.force_orphans:
        sys.exit("Refusing to --apply: FK orphans present. Resolve them or pass --force-orphans.")

    # Pre-transform the race-collided projects + their domain links.
    proj_df = _read_bq_table(bq, project_id, "projects")
    pd_df = _read_bq_table(bq, project_id, "project_domains")
    dom_df = _read_bq_table(bq, project_id, "domains")
    proj_split, pd_split = split_collided_projects(proj_df, pd_df, dom_df)
    n_remapped = int((proj_split["project_id"].values != proj_df["project_id"].values).sum())
    print(f"\n=== Project split: {n_remapped} project rows reassigned to new ids ===")
    overrides = {"projects": proj_split, "project_domains": pd_split}

    print("\n=== Loading (--apply) ===")
    for table in LOAD_ORDER:
        df = overrides.get(table)
        if df is None:
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
