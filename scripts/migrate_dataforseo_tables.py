"""DataForSEO migration, in 5 phases.

Run each phase separately. Every phase supports --dry-run to preview SQL
without executing.

  PHASE 1: backup                 Copy every DFS endpoint table (+ legacy
                                  serp_google_organic_live_advanced_backup)
                                  to a new DataForSEO_backup_04_20_2026
                                  dataset. Original data unchanged.

  PHASE 2: migrate                Create the 11 new standardized tables in
                                  DataForSEO, copy data FROM the backup
                                  dataset with transforms (drop project_id,
                                  stamp endpoint_mode='live', preserve
                                  existing job_id/upload_id/ingest_timestamp/
                                  domain). domain_id left NULL (filled by
                                  backfill-domains phase).

  PHASE 3: drop-old               Row-count verify, then drop every old
                                  DFS table + the pre-existing
                                  serp_google_organic_live_advanced_backup
                                  from DataForSEO. Original data still lives
                                  in the backup dataset.

  PHASE 4: backfill-ids           Stamp synthetic job_id + upload_id on any
                                  row where they're NULL (currently only
                                  the historical backlinks-summary rows).
                                  Logs one entry per table to
                                  Logs.upload_events with
                                  notes='historical migration backfill'.

  PHASE 5: backfill-domains       For every row with a non-null `domain`
                                  and NULL `domain_id`, look up domain_id
                                  in Meta.domains (with normalization) and
                                  fill it in. Rows with no `domain` stay
                                  NULL for domain_id.

Usage:
  python scripts/migrate_dataforseo_tables.py --phase=backup --dry-run
  python scripts/migrate_dataforseo_tables.py --phase=backup --yes
  python scripts/migrate_dataforseo_tables.py --phase=migrate --dry-run
  python scripts/migrate_dataforseo_tables.py --phase=migrate --yes
  python scripts/migrate_dataforseo_tables.py --phase=drop-old --dry-run
  python scripts/migrate_dataforseo_tables.py --phase=drop-old --yes
  python scripts/migrate_dataforseo_tables.py --phase=backfill-ids --dry-run
  python scripts/migrate_dataforseo_tables.py --phase=backfill-ids --yes
  python scripts/migrate_dataforseo_tables.py --phase=backfill-domains --dry-run
  python scripts/migrate_dataforseo_tables.py --phase=backfill-domains --yes

Safeguards:
  --dry-run     print every SQL statement, execute nothing
  --yes         skip confirmation prompts (required for real execution)
  --only NAME   limit a phase to a single table (surgical re-runs)
  --project ID  override GCP project id
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import click

# Allow running as `python scripts/migrate_dataforseo_tables.py`
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate_dataforseo_manifest import MIGRATIONS, BACKUP_SUFFIX, Migration


SOURCE_DATASET = "DataForSEO"
BACKUP_DATASET = "DataForSEO_backup_04_20_2026"  # BQ datasets can't have hyphens

# Tables in SOURCE_DATASET that aren't endpoint tables but should still be
# relocated to the backup dataset during phase=backup and dropped from
# SOURCE_DATASET during phase=drop-old.
EXTRA_BACKUP_TABLES = [
    "serp_google_organic_live_advanced_backup",
]

# Test hook — lets tests inject a fake BQ client.
_bq_override = None


def _get_bq(project_override: str | None):
    if _bq_override is not None:
        return _bq_override
    from skyward.config import load_config
    from skyward.data.bigquery import BigQueryClient
    cfg = load_config()
    project = project_override or cfg.datahub_project_id
    return BigQueryClient(project_id=project)


# Metadata columns the new schema always provides.
_METADATA_COLUMNS = [
    "job_id", "upload_id", "ingest_timestamp",
    "domain_id", "domain", "task_id", "endpoint_mode",
]


# ---------------------------------------------------------------------------
# PHASE 1: BACKUP — snapshot whole dataset to a new backup dataset.
# ---------------------------------------------------------------------------

def _ensure_backup_dataset_sql(project: str) -> str:
    return f"CREATE SCHEMA IF NOT EXISTS `{project}.{BACKUP_DATASET}`;"


def _snapshot_table_sql(project: str, table_name: str) -> str:
    return (
        f"CREATE TABLE `{project}.{BACKUP_DATASET}.{table_name}`\n"
        f"AS SELECT * FROM `{project}.{SOURCE_DATASET}.{table_name}`;"
    )


def _do_backup(bq, migrations: list[Migration], project: str, dry_run: bool) -> int:
    click.echo(f"\n=== PHASE 1: BACKUP to {project}.{BACKUP_DATASET} ===")
    _execute_or_print(bq, _ensure_backup_dataset_sql(project), dry_run=dry_run, label="CREATE BACKUP DATASET")

    # Snapshot the 11 endpoint tables.
    for m in migrations:
        if m.old_name is None:
            continue  # new table, nothing to back up
        _execute_or_print(
            bq, _snapshot_table_sql(project, m.old_name),
            dry_run=dry_run, label=f"SNAPSHOT {m.old_name}",
        )

    # Snapshot extra non-endpoint tables the user wants preserved.
    for extra in EXTRA_BACKUP_TABLES:
        _execute_or_print(
            bq, _snapshot_table_sql(project, extra),
            dry_run=dry_run, label=f"SNAPSHOT {extra} (legacy)",
        )

    click.echo("\n=== Phase 1 complete. ===")
    return 0


# ---------------------------------------------------------------------------
# PHASE 2: MIGRATE — create new tables in SOURCE_DATASET, copy from BACKUP.
# ---------------------------------------------------------------------------

def _create_new_sql(migration: Migration, project: str) -> str:
    cluster = ", ".join(migration.clustering_fields)
    return (
        f"CREATE TABLE `{project}.{SOURCE_DATASET}.{migration.new_name}` (\n"
        f"{migration.new_schema}\n"
        f")\n"
        f"PARTITION BY DATE({migration.partition_field})\n"
        f"CLUSTER BY {cluster};"
    )


def _get_old_columns(bq, old_name: str, project: str, dataset: str) -> list[str]:
    """Introspect a table's columns via INFORMATION_SCHEMA."""
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
    """Extract endpoint column names from new_schema (excluding metadata).

    Handles both plain form (`name TYPE,`) and annotated form
    (`name TYPE OPTIONS(description="..."),`) by splitting on top-level commas
    (commas inside parentheses are ignored).
    """
    names: list[str] = []
    depth = 0
    token_chars: list[str] = []
    for ch in new_schema:
        if ch == "(":
            depth += 1
            token_chars.append(ch)
        elif ch == ")":
            depth -= 1
            token_chars.append(ch)
        elif ch == "," and depth == 0:
            tok = "".join(token_chars).strip()
            if tok:
                name = tok.split()[0].strip()
                if name and name not in _METADATA_COLUMNS:
                    names.append(name)
            token_chars = []
        else:
            token_chars.append(ch)
    # trailing token after last comma
    tail = "".join(token_chars).strip()
    if tail:
        name = tail.split()[0].strip()
        if name and name not in _METADATA_COLUMNS:
            names.append(name)
    return names


def _build_copy_sql(bq, migration: Migration, project: str) -> str | None:
    """Build INSERT INTO DataForSEO.new_name SELECT ... FROM BACKUP_DATASET.old_name."""
    if migration.old_name is None:
        return None  # new table — no copy
    if migration.old_name == migration.new_name:
        return f"-- SKIP COPY for in-place update: {migration.new_name}"

    # Copy source is the backup dataset (which phase=backup already populated).
    old_cols = set(_get_old_columns(bq, migration.old_name, project, BACKUP_DATASET))
    endpoint_cols = _parse_endpoint_columns(migration.new_schema)

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
        if col == "domain":
            continue
        if col in old_cols:
            select_exprs.append(col)
        else:
            select_exprs.append(f"CAST(NULL AS STRING) AS {col}")

    select_clause = ",\n  ".join(select_exprs)
    insert_cols = _METADATA_COLUMNS + [c for c in endpoint_cols if c != "domain"]
    insert_cols_clause = ", ".join(insert_cols)

    return (
        f"INSERT INTO `{project}.{SOURCE_DATASET}.{migration.new_name}` ({insert_cols_clause})\n"
        f"SELECT\n  {select_clause}\n"
        f"FROM `{project}.{BACKUP_DATASET}.{migration.old_name}`;"
    )


def _build_copy_sql_preview(migration: Migration, project: str) -> str | None:
    """Preview SQL for dry-run — doesn't introspect INFORMATION_SCHEMA."""
    if migration.old_name is None:
        return None
    if migration.old_name == migration.new_name:
        return f"-- SKIP COPY for in-place update: {migration.new_name}"
    endpoint_cols = _parse_endpoint_columns(migration.new_schema)
    all_cols = _METADATA_COLUMNS + [c for c in endpoint_cols if c != "domain"]
    cols_joined = ", ".join(all_cols)
    return (
        f"-- DRY RUN: Actual SQL will be generated at execute time by introspecting\n"
        f"-- `{project}.{BACKUP_DATASET}.{migration.old_name}` via INFORMATION_SCHEMA.\n"
        f"-- Target columns: {cols_joined}\n"
        f"-- Historical defaults: endpoint_mode='live', task_id=NULL, domain_id=NULL\n"
        f"-- project_id will be dropped if present in source\n"
        f"INSERT INTO `{project}.{SOURCE_DATASET}.{migration.new_name}` ({cols_joined})\n"
        f"SELECT ...\n"
        f"FROM `{project}.{BACKUP_DATASET}.{migration.old_name}`;"
    )


def _do_migrate(bq, migrations: list[Migration], project: str, dry_run: bool) -> int:
    click.echo(f"\n=== PHASE 2: MIGRATE (new tables in {project}.{SOURCE_DATASET}) ===")
    click.echo(f"Source of copy: {project}.{BACKUP_DATASET} (must have run phase=backup first)")

    for m in migrations:
        click.echo(f"\n--- {m.new_name} ---")
        _execute_or_print(bq, _create_new_sql(m, project), dry_run=dry_run, label="CREATE NEW")
        if dry_run:
            copy_sql = _build_copy_sql_preview(m, project)
        else:
            copy_sql = _build_copy_sql(bq, m, project)
        _execute_or_print(bq, copy_sql, dry_run=dry_run, label="COPY FROM BACKUP")

    click.echo("\n=== Phase 2 complete. ===")
    click.echo(f"Run --phase=drop-old to verify counts and drop old tables from {SOURCE_DATASET}.")
    return 0


# ---------------------------------------------------------------------------
# PHASE 3: DROP-OLD — row-count verify + drop old tables from SOURCE_DATASET.
# ---------------------------------------------------------------------------

def _count_rows(bq, full_table_id: str) -> int:
    sql = f"SELECT COUNT(*) AS n FROM `{full_table_id}`"
    df = bq.client.query(sql).result().to_dataframe()
    return int(df["n"].iloc[0])


def _do_drop_old(bq, migrations: list[Migration], project: str, dry_run: bool) -> int:
    click.echo(f"\n=== PHASE 3: DROP-OLD from {project}.{SOURCE_DATASET} ===")
    click.echo(f"Backup verification: comparing counts against {project}.{BACKUP_DATASET}\n")

    for m in migrations:
        if m.old_name is None or m.old_name == m.new_name:
            continue
        backup_id = f"{project}.{BACKUP_DATASET}.{m.old_name}"
        new_id = f"{project}.{SOURCE_DATASET}.{m.new_name}"
        if dry_run:
            click.echo(f"[dry-run] Would compare row counts: {backup_id} vs {new_id}")
            click.echo(f"[dry-run] If counts match, would DROP `{project}.{SOURCE_DATASET}.{m.old_name}`\n")
            continue
        backup_n = _count_rows(bq, backup_id)
        new_n = _count_rows(bq, new_id)
        if backup_n != new_n:
            click.echo(
                f"Row count mismatch for {m.new_name}: backup={backup_n}, new={new_n}. "
                f"Aborting drop phase.",
                err=True,
            )
            return 1
        click.echo(f"{m.new_name}: row counts match ({new_n}). Dropping old.")
        _execute_or_print(
            bq, f"DROP TABLE `{project}.{SOURCE_DATASET}.{m.old_name}`;",
            dry_run=dry_run, label="DROP OLD",
        )

    # Also drop the EXTRA_BACKUP_TABLES from SOURCE_DATASET (they're in BACKUP_DATASET now).
    for extra in EXTRA_BACKUP_TABLES:
        _execute_or_print(
            bq, f"DROP TABLE `{project}.{SOURCE_DATASET}.{extra}`;",
            dry_run=dry_run, label=f"DROP LEGACY {extra}",
        )

    click.echo("\n=== Phase 3 complete. ===")
    return 0


# ---------------------------------------------------------------------------
# PHASE 4: BACKFILL-IDS — stamp synthetic job_id/upload_id where NULL.
# ---------------------------------------------------------------------------

def _do_backfill_ids(bq, migrations: list[Migration], project: str, dry_run: bool) -> int:
    """Stamp synthetic job_id + upload_id on rows where either is NULL.

    We generate one synthetic job_id + upload_id per table (reproducible-in-a-
    single-run only — new UUIDs each execution). Rows with pre-existing job_id
    are not touched.
    """
    click.echo(f"\n=== PHASE 4: BACKFILL-IDS (fill NULL job_id/upload_id) ===")

    for m in migrations:
        if m.old_name is None:
            continue  # pure-new table, no historical rows
        new_id = f"{project}.{SOURCE_DATASET}.{m.new_name}"
        synth_job_id = str(uuid.uuid4())
        synth_upload_id = str(uuid.uuid4())
        sql = (
            f"UPDATE `{new_id}`\n"
            f"SET\n"
            f"  job_id = '{synth_job_id}',\n"
            f"  upload_id = '{synth_upload_id}'\n"
            f"WHERE job_id IS NULL OR upload_id IS NULL;"
        )
        _execute_or_print(bq, sql, dry_run=dry_run, label=f"BACKFILL IDs on {m.new_name}")

        # Log to upload_events. Only bother if we're actually executing; dry-run
        # wouldn't produce a real row count anyway.
        if not dry_run:
            count_sql = (
                f"SELECT COUNT(*) AS n FROM `{new_id}` "
                f"WHERE job_id = '{synth_job_id}'"
            )
            try:
                n = int(bq.client.query(count_sql).result().to_dataframe()["n"].iloc[0])
            except Exception:
                n = 0
            if n > 0:
                _log_historical_upload_event(
                    bq, project,
                    job_id=synth_job_id, upload_id=synth_upload_id,
                    table=m.new_name, row_count=n,
                )
                click.echo(f"  logged {n} rows to Logs.upload_events under job_id={synth_job_id}")

    click.echo("\n=== Phase 4 complete. ===")
    return 0


def _log_historical_upload_event(bq, project: str, *, job_id, upload_id, table, row_count):
    """Insert one row into Logs.upload_events to document the synthetic-ID backfill."""
    import datetime as _dt
    log_table = f"{project}.Logs.upload_events"
    entry = [{
        "job_id": job_id,
        "upload_id": upload_id,
        "source": "dataforseo",
        "source_program": "migrate_dataforseo_tables.py:backfill-ids",
        "dataset": SOURCE_DATASET,
        "table": table,
        "row_count": row_count,
        "ingest_timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "client_id": None,
        "project_id": None,
        "notes": "historical migration backfill 2026-04-22: synthetic IDs for rows that predate job_id/upload_id tracking",
    }]
    errors = bq.client.insert_rows_json(log_table, entry)
    if errors:
        click.echo(f"WARNING: upload_events log failed for {table}: {errors}", err=True)


# ---------------------------------------------------------------------------
# PHASE 5: BACKFILL-DOMAINS — fill domain_id from Meta.domains via `domain`.
# ---------------------------------------------------------------------------

# Normalization applied to both sides: strip scheme, www., trailing slash, lowercase.
_NORMALIZE_SQL = r"""
LOWER(
  REGEXP_REPLACE(
    REGEXP_REPLACE(
      REGEXP_REPLACE({col}, r'^(https?://)?(www\.)?', ''),
      r'/+$', ''
    ),
    r'\s+', ''
  )
)
""".strip()


def _backfill_domains_sql(project: str, new_table_name: str) -> str:
    t = f"{project}.{SOURCE_DATASET}.{new_table_name}"
    meta = f"{project}.Meta.domains"
    norm_target = _NORMALIZE_SQL.format(col="t.domain")
    norm_meta = _NORMALIZE_SQL.format(col="m.domain")
    return (
        f"MERGE `{t}` t\n"
        f"USING (\n"
        f"  SELECT domain_id, domain,\n"
        f"    {norm_meta} AS _norm\n"
        f"  FROM `{meta}` m\n"
        f") m\n"
        f"ON {norm_target} = m._norm\n"
        f"WHEN MATCHED AND t.domain_id IS NULL THEN\n"
        f"  UPDATE SET domain_id = m.domain_id, domain = m.domain;"
    )


def _do_backfill_domains(bq, migrations: list[Migration], project: str, dry_run: bool) -> int:
    """For rows with non-null `domain` and NULL `domain_id`, resolve via Meta.domains."""
    click.echo(f"\n=== PHASE 5: BACKFILL-DOMAINS ===")
    click.echo("For rows with a preserved `domain` string but NULL `domain_id`, look up\n"
               "the match in Meta.domains (normalized) and fill in domain_id.\n"
               "Rows with no `domain` remain NULL for domain_id.")

    for m in migrations:
        if m.old_name is None:
            continue
        sql = _backfill_domains_sql(project, m.new_name)
        _execute_or_print(
            bq, sql, dry_run=dry_run,
            label=f"BACKFILL DOMAIN on {m.new_name}",
        )

    click.echo("\n=== Phase 5 complete. ===")
    click.echo(
        "Run scripts/inventory_dataforseo_domains.py (read-only) to see how "
        "many rows still have NULL domain_id and why."
    )
    return 0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_PHASES = {
    "backup": _do_backup,
    "migrate": _do_migrate,
    "drop-old": _do_drop_old,
    "backfill-ids": _do_backfill_ids,
    "backfill-domains": _do_backfill_domains,
}


@click.command()
@click.option(
    "--phase",
    type=click.Choice(list(_PHASES.keys())),
    required=True,
    help="Which phase to run.",
)
@click.option("--dry-run", is_flag=True, help="Print SQL without executing.")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.option("--only", default=None, help="Limit phase to a single table (new_name).")
@click.option("--project", default=None, help="Override GCP project id.")
def cli(phase: str, dry_run: bool, yes: bool, only: str | None, project: str | None):
    """Run one phase of the DataForSEO migration."""
    bq = _get_bq(project)
    resolved_project = project or bq.project_id

    migrations = list(MIGRATIONS)
    if only:
        migrations = [m for m in MIGRATIONS if m.new_name == only]
        if not migrations:
            click.echo(f"No migration matches --only={only}", err=True)
            sys.exit(1)

    click.echo(f"Project: {resolved_project}")
    click.echo(f"Phase: {phase}")
    click.echo(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
    click.echo(f"Migrations in scope: {len(migrations)}")

    if not dry_run and not yes:
        click.confirm("\nProceed?", abort=True)

    rc = _PHASES[phase](bq, migrations, resolved_project, dry_run)
    sys.exit(rc)


if __name__ == "__main__":
    cli()
