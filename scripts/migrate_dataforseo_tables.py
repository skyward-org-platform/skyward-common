"""One-shot migration tool: rename + schema-standardize all DataForSEO tables.

Execution plan per table (see also migrate_dataforseo_manifest.py):
  1. Backup: CREATE TABLE <old>_backup_04-20-2026 AS SELECT * FROM <old>
  2. Create new table with full schema + partition + cluster
  3. Copy data from old to new (drop project_id, stamp historical defaults)
  4. (separate --drop-old phase) Verify row counts, drop old tables

Usage:
  python scripts/migrate_dataforseo_tables.py --dry-run
  python scripts/migrate_dataforseo_tables.py --yes
  python scripts/migrate_dataforseo_tables.py --drop-old --yes

Safeguards:
  --dry-run     print every SQL statement, execute nothing
  --yes         skip confirmation prompts (required for real execution)
  --only NAME   migrate only the specified new_name (surgical re-runs)
  --project ID  override GCP project id (default: from skyward.config.load_config)
  --drop-old    run the row-count-verify + drop-old phase (separate run)
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

# Allow running as `python scripts/migrate_dataforseo_tables.py`
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate_dataforseo_manifest import MIGRATIONS, BACKUP_SUFFIX, Migration


# Test hook — lets tests inject a fake BQ client
_bq_override = None


def _get_bq(project_override: str | None):
    if _bq_override is not None:
        return _bq_override
    from skyward.config import load_config
    from skyward.data.bigquery import BigQueryClient
    cfg = load_config()
    project = project_override or cfg.datahub_project_id
    return BigQueryClient(project_id=project)


# Metadata columns the new schema always provides — everything else comes from
# the endpoint-specific portion of new_schema.
_METADATA_COLUMNS = [
    "job_id", "upload_id", "ingest_timestamp",
    "domain_id", "domain", "task_id", "endpoint_mode",
]


def _backup_sql(migration: Migration, project: str, dataset: str = "DataForSEO") -> str | None:
    if migration.old_name is None:
        return None
    backup = f"{migration.old_name}{BACKUP_SUFFIX}"
    return (
        f"CREATE TABLE `{project}.{dataset}.{backup}` AS\n"
        f"SELECT * FROM `{project}.{dataset}.{migration.old_name}`;"
    )


def _create_new_sql(migration: Migration, project: str, dataset: str = "DataForSEO") -> str:
    cluster = ", ".join(migration.clustering_fields)
    return (
        f"CREATE TABLE `{project}.{dataset}.{migration.new_name}` (\n"
        f"{migration.new_schema}\n"
        f")\n"
        f"PARTITION BY DATE({migration.partition_field})\n"
        f"CLUSTER BY {cluster};"
    )


def _get_old_columns(bq, old_name: str, project: str, dataset: str = "DataForSEO") -> list[str]:
    """Introspect the old table's columns via INFORMATION_SCHEMA."""
    from google.cloud import bigquery as _bq
    sql = f"""
        SELECT column_name
        FROM `{project}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name = @t
        ORDER BY ordinal_position
    """
    job_config = _bq.QueryJobConfig(
        query_parameters=[_bq.ScalarQueryParameter("t", "STRING", old_name)]
    )
    df = bq.client.query(sql, job_config=job_config).result().to_dataframe()
    return df["column_name"].tolist() if not df.empty else []


def _parse_endpoint_columns(new_schema: str) -> list[str]:
    """Extract endpoint-specific column names from new_schema (excluding metadata block)."""
    # Split on commas (schema is comma-separated). Strip whitespace, skip empties,
    # take the first token as the column name.
    names: list[str] = []
    for raw in new_schema.replace("\n", " ").split(","):
        tok = raw.strip()
        if not tok:
            continue
        name = tok.split()[0].strip()
        if name and name not in _METADATA_COLUMNS:
            names.append(name)
    return names


def _build_copy_sql(bq, migration: Migration, project: str, dataset: str = "DataForSEO") -> str | None:
    """Build INSERT INTO new (cols) SELECT ... FROM old, with transformations."""
    if migration.old_name is None:
        return None  # new table — no copy
    if migration.old_name == migration.new_name:
        return f"-- SKIP COPY for in-place update: {migration.new_name}"

    old_cols = set(_get_old_columns(bq, migration.old_name, project, dataset))
    endpoint_cols = _parse_endpoint_columns(migration.new_schema)

    # Build SELECT list
    select_exprs: list[str] = []
    # Metadata block
    if "job_id" in old_cols:
        select_exprs.append("job_id")
    else:
        select_exprs.append("CAST(NULL AS STRING) AS job_id")
    if "upload_id" in old_cols:
        select_exprs.append("upload_id")
    else:
        select_exprs.append("CAST(NULL AS STRING) AS upload_id")
    if "ingest_timestamp" in old_cols:
        select_exprs.append("ingest_timestamp")
    else:
        select_exprs.append("CURRENT_TIMESTAMP() AS ingest_timestamp")
    select_exprs.append("CAST(NULL AS INT64) AS domain_id")
    if migration.preserve_domain_column and "domain" in old_cols:
        select_exprs.append("domain")
    else:
        select_exprs.append("CAST(NULL AS STRING) AS domain")
    if "task_id" in old_cols:
        select_exprs.append("task_id")
    else:
        select_exprs.append("CAST(NULL AS STRING) AS task_id")
    select_exprs.append("'live' AS endpoint_mode")
    # Endpoint-specific columns
    for col in endpoint_cols:
        if col == "domain":  # already handled above
            continue
        if col in old_cols:
            select_exprs.append(col)
        else:
            select_exprs.append(f"CAST(NULL AS STRING) AS {col}")

    select_clause = ",\n  ".join(select_exprs)
    insert_cols = _METADATA_COLUMNS + [c for c in endpoint_cols if c != "domain"]
    insert_cols_clause = ", ".join(insert_cols)

    return (
        f"INSERT INTO `{project}.{dataset}.{migration.new_name}` ({insert_cols_clause})\n"
        f"SELECT\n  {select_clause}\n"
        f"FROM `{project}.{dataset}.{migration.old_name}`;"
    )


def _drop_old_sql(migration: Migration, project: str, dataset: str = "DataForSEO") -> str | None:
    if migration.old_name is None:
        return None
    if migration.old_name == migration.new_name:
        return None
    return f"DROP TABLE `{project}.{dataset}.{migration.old_name}`;"


def _count_rows(bq, full_table_id: str) -> int:
    sql = f"SELECT COUNT(*) AS n FROM `{full_table_id}`"
    df = bq.client.query(sql).result().to_dataframe()
    return int(df["n"].iloc[0])


def _execute_or_print(bq, sql: str | None, *, dry_run: bool, label: str) -> None:
    if sql is None:
        return
    prefix = "[dry-run]" if dry_run else "[exec]"
    click.echo(f"\n{prefix} {label}")
    click.echo(sql)
    if not dry_run:
        job = bq.client.query(sql)
        job.result()
        click.echo(f"{prefix} {label} — OK")


def _verify_and_drop(bq, migrations: list[Migration], project: str, dataset: str, dry_run: bool) -> int:
    """For each rename migration, verify old vs new counts, then drop old."""
    for m in migrations:
        if m.old_name is None or m.old_name == m.new_name:
            continue
        old_id = f"{project}.{dataset}.{m.old_name}"
        new_id = f"{project}.{dataset}.{m.new_name}"
        old_n = _count_rows(bq, old_id)
        new_n = _count_rows(bq, new_id)
        if old_n != new_n:
            click.echo(
                f"Row count mismatch for {m.new_name}: old={old_n}, new={new_n}. Aborting drop phase.",
                err=True,
            )
            return 1
        click.echo(f"{m.new_name}: row counts match ({new_n}). Dropping old.")
        _execute_or_print(bq, _drop_old_sql(m, project, dataset), dry_run=dry_run, label="DROP OLD")
    return 0


@click.command()
@click.option("--dry-run", is_flag=True, help="Print every SQL statement without executing.")
@click.option("--yes", is_flag=True, help="Skip confirmation prompts.")
@click.option("--only", default=None, help="Only migrate the specified new_name.")
@click.option("--project", default=None, help="Override GCP project id.")
@click.option("--drop-old", is_flag=True, help="Run the row-count-verify + drop-old phase only.")
def cli(dry_run: bool, yes: bool, only: str | None, project: str | None, drop_old: bool):
    """Run the DataForSEO table migration."""
    bq = _get_bq(project)
    resolved_project = project or bq.project_id

    migrations = list(MIGRATIONS)
    if only:
        migrations = [m for m in MIGRATIONS if m.new_name == only]
        if not migrations:
            click.echo(f"No migration matches --only={only}", err=True)
            sys.exit(1)

    if drop_old:
        click.echo("=== DROP-OLD phase (row-count verification + drop) ===")
        if not yes and not dry_run:
            click.confirm("Drop old tables after verification?", abort=True)
        rc = _verify_and_drop(bq, migrations, resolved_project, "DataForSEO", dry_run)
        sys.exit(rc)

    click.echo(f"\n=== DataForSEO Migration ({'DRY RUN' if dry_run else 'EXECUTE'}) ===")
    click.echo(f"Project: {resolved_project}")
    click.echo(f"Migrations: {len(migrations)}")
    for m in migrations:
        click.echo(f"  - {m.old_name or '(new)':45s} -> {m.new_name}")

    if not dry_run and not yes:
        click.confirm("\nProceed?", abort=True)

    for m in migrations:
        click.echo(f"\n--- {m.new_name} ---")
        _execute_or_print(bq, _backup_sql(m, resolved_project), dry_run=dry_run, label="BACKUP")
        _execute_or_print(bq, _create_new_sql(m, resolved_project), dry_run=dry_run, label="CREATE NEW")
        copy_sql = _build_copy_sql(bq, m, resolved_project) if not dry_run else _build_copy_sql_preview(m, resolved_project)
        _execute_or_print(bq, copy_sql, dry_run=dry_run, label="COPY DATA")

    click.echo("\n=== Migration phase complete (backup + create + copy) ===")
    click.echo("Run with --drop-old to verify row counts and drop old tables.")


def _build_copy_sql_preview(migration: Migration, project: str, dataset: str = "DataForSEO") -> str | None:
    """Preview version of copy SQL for dry-run (doesn't query INFORMATION_SCHEMA).

    In dry-run we don't have a real BQ to introspect from, so we emit a
    placeholder that documents what the real INSERT will look like.
    """
    if migration.old_name is None:
        return None
    if migration.old_name == migration.new_name:
        return f"-- SKIP COPY for in-place update: {migration.new_name}"
    endpoint_cols = _parse_endpoint_columns(migration.new_schema)
    all_cols = _METADATA_COLUMNS + [c for c in endpoint_cols if c != "domain"]
    cols_joined = ", ".join(all_cols)
    return (
        f"-- DRY RUN: Actual SQL will be generated at execute time by introspecting\n"
        f"-- `{project}.{dataset}.{migration.old_name}` via INFORMATION_SCHEMA.\n"
        f"-- Target columns: {cols_joined}\n"
        f"-- Historical defaults: endpoint_mode='live', task_id=NULL, domain_id=NULL\n"
        f"-- project_id will be dropped if present\n"
        f"INSERT INTO `{project}.{dataset}.{migration.new_name}` ({cols_joined}) SELECT ... FROM `{project}.{dataset}.{migration.old_name}`;"
    )


if __name__ == "__main__":
    cli()
