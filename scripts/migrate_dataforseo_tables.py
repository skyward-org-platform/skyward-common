"""DataForSEO migration, structured in 3 phases per the plan.

Every phase supports --dry-run to preview SQL without executing.

  PHASE 1: setup
    - CREATE SCHEMA DataForSEO_backup_04_20_2026 (idempotent)
    - Snapshot every DFS endpoint table (+ the pre-existing
      serp_google_organic_live_advanced_backup legacy table) into the
      backup dataset via CREATE TABLE ... AS SELECT *.
    - CREATE the 11 new standardized tables in DataForSEO (empty, correct
      schema, partition + cluster + column descriptions). For the one
      in-place migration (ranked_keywords): DROP the original first so we
      can recreate it with the new schema.

  PHASE 2: migrate
    - For each migration: INSERT INTO DataForSEO.<new_name> SELECT ...
      FROM DataForSEO_backup_04_20_2026.<old_name> — applying the
      per-endpoint column-mapping rules:
        * drop project_id (always)
        * drop legacy `domain` column on SERP / bulk_pages / backlinks_summary
          (was per-row result/target, not caller context)
        * preserve `domain` on ranked_keywords and backlinks_backlinks
          (real caller context, competitor labeling confirmed)
        * historical defaults: endpoint_mode='live', task_id=NULL,
          domain_id=NULL (filled in phase 3)
    - Row-count verify each migration (backup.old vs DataForSEO.new).
    - DROP old rename-source tables and the legacy
      serp_google_organic_live_advanced_backup from DataForSEO.
      (The backup dataset still holds full copies.)

  PHASE 3: backfill
    - UPDATE every new table where job_id / upload_id are NULL, stamp
      synthetic UUIDs per table. Log one row to Logs.upload_events per
      synthetic stamping, notes='historical migration backfill'.
    - For each new table with a non-null `domain` and NULL `domain_id`:
      MERGE against Meta.domains (normalized on both sides; strip scheme,
      www., trailing slash, whitespace; lowercase). Fill in domain_id and
      the canonical Meta domain string.
    - Rows where `domain` string isn't in Meta.domains: domain_id stays
      NULL, the original `domain` value is preserved.

Usage (always dry-run first, then --yes):
  python scripts/migrate_dataforseo_tables.py --phase=setup --dry-run
  python scripts/migrate_dataforseo_tables.py --phase=setup --yes
  python scripts/migrate_dataforseo_tables.py --phase=migrate --dry-run
  python scripts/migrate_dataforseo_tables.py --phase=migrate --yes
  python scripts/migrate_dataforseo_tables.py --phase=backfill --dry-run
  python scripts/migrate_dataforseo_tables.py --phase=backfill --yes

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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate_dataforseo_manifest import MIGRATIONS, Migration


SOURCE_DATASET = "DataForSEO"
BACKUP_DATASET = "DataForSEO_backup_04_20_2026"  # BQ datasets forbid hyphens.

# Tables in SOURCE_DATASET that aren't endpoint tables but still get relocated
# to the backup dataset (phase 1) and dropped from SOURCE_DATASET (phase 2).
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
# PHASE 1: SETUP — backup dataset + snapshots + empty new tables.
# ---------------------------------------------------------------------------

def _ensure_backup_dataset_sql(project: str) -> str:
    return f"CREATE SCHEMA IF NOT EXISTS `{project}.{BACKUP_DATASET}`;"


def _snapshot_table_sql(project: str, table_name: str) -> str:
    return (
        f"CREATE TABLE `{project}.{BACKUP_DATASET}.{table_name}`\n"
        f"AS SELECT * FROM `{project}.{SOURCE_DATASET}.{table_name}`;"
    )


def _create_new_sql(migration: Migration, project: str) -> str:
    cluster = ", ".join(migration.clustering_fields)
    return (
        f"CREATE TABLE `{project}.{SOURCE_DATASET}.{migration.new_name}` (\n"
        f"{migration.new_schema}\n"
        f")\n"
        f"PARTITION BY DATE({migration.partition_field})\n"
        f"CLUSTER BY {cluster};"
    )


def _drop_source_table_sql(project: str, table_name: str) -> str:
    return f"DROP TABLE `{project}.{SOURCE_DATASET}.{table_name}`;"


def _do_setup(bq, migrations: list[Migration], project: str, dry_run: bool) -> int:
    click.echo(f"\n=== PHASE 1: SETUP ===")
    click.echo(f"Backup dataset: {project}.{BACKUP_DATASET}")
    click.echo("Snapshots every DFS table + legacy backup into the backup dataset,")
    click.echo("then creates empty new tables in DataForSEO with the new schema.\n")

    # 1a. Create the backup dataset.
    _execute_or_print(bq, _ensure_backup_dataset_sql(project),
                      dry_run=dry_run, label="CREATE BACKUP DATASET")

    # 1b. Snapshot every migration source + extras.
    for m in migrations:
        if m.old_name is None:
            continue
        _execute_or_print(bq, _snapshot_table_sql(project, m.old_name),
                          dry_run=dry_run, label=f"SNAPSHOT {m.old_name}")
    for extra in EXTRA_BACKUP_TABLES:
        _execute_or_print(bq, _snapshot_table_sql(project, extra),
                          dry_run=dry_run, label=f"SNAPSHOT {extra} (legacy)")

    # 1c. Create new empty tables in SOURCE_DATASET.
    # For in-place migrations (old_name == new_name), we must DROP the original
    # first (already snapshotted to backup) so we can CREATE with the new schema.
    for m in migrations:
        if m.old_name is not None and m.old_name == m.new_name:
            _execute_or_print(bq, _drop_source_table_sql(project, m.new_name),
                              dry_run=dry_run,
                              label=f"DROP OLD (in-place) {m.new_name}")
        _execute_or_print(bq, _create_new_sql(m, project),
                          dry_run=dry_run, label=f"CREATE NEW {m.new_name}")

    click.echo("\n=== Phase 1 complete. ===")
    click.echo(f"Next: --phase=migrate to copy data from {BACKUP_DATASET} into the new tables.")
    return 0


# ---------------------------------------------------------------------------
# PHASE 2: MIGRATE — copy data with column mapping + verify + drop old.
# ---------------------------------------------------------------------------

def _get_old_columns(bq, old_name: str, project: str, dataset: str) -> dict[str, str]:
    """Introspect a table's columns via INFORMATION_SCHEMA.

    Returns dict of column_name -> data_type (uppercased). The type is the full
    BQ declaration — scalar (`STRING`, `INT64`) or complex (`ARRAY<INT64>`,
    `STRUCT<...>`). Needed so the copy SQL can TO_JSON_STRING() ARRAY/STRUCT
    source columns when the new target is STRING (e.g. `categories`,
    `monthly_searches`).
    """
    from google.cloud import bigquery as _bq
    sql = f"""
        SELECT column_name, data_type
        FROM `{project}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name = @t
        ORDER BY ordinal_position
    """
    job_config = _bq.QueryJobConfig(
        query_parameters=[_bq.ScalarQueryParameter("t", "STRING", old_name)]
    )
    df = bq.client.query(sql, job_config=job_config).result().to_dataframe()
    if df.empty:
        return {}
    return {row["column_name"]: row["data_type"].upper() for _, row in df.iterrows()}


def _source_to_target_expr(col: str, src_type: str, target_type: str) -> str:
    """Return the SELECT expression to copy `col` from source to target.

    Handles type transformations:
      - ARRAY<*> or STRUCT<...> source + STRING target: TO_JSON_STRING(col)
      - scalar type mismatches: SAFE_CAST(col AS target_type)
      - types match: pass through unchanged
    """
    src_kind = src_type.split("<", 1)[0].strip()  # "ARRAY<INT64>" -> "ARRAY"
    is_complex = src_kind in {"ARRAY", "STRUCT"}

    if src_type == target_type:
        return col
    if is_complex and target_type == "STRING":
        return f"TO_JSON_STRING({col}) AS {col}"
    # Scalar mismatch — use SAFE_CAST so non-convertible values become NULL
    # rather than erroring the whole migration.
    return f"SAFE_CAST({col} AS {target_type}) AS {col}"


def _parse_endpoint_columns(new_schema: str) -> list[tuple[str, str]]:
    """Extract endpoint (name, type) pairs from new_schema, ignoring metadata.

    Handles both plain form (`name TYPE,`) and annotated form
    (`name TYPE OPTIONS(description="..."),`) by splitting on top-level commas
    only (commas inside parentheses are part of a single column definition).

    Types are returned uppercase (e.g. 'STRING', 'INT64', 'BOOL', 'TIMESTAMP',
    'FLOAT64'). Needed so the copy-SQL fallback for missing columns emits
    the right typed NULL — inserting CAST(NULL AS STRING) into a BOOL column
    is a BQ type error.
    """
    pairs: list[tuple[str, str]] = []
    depth = 0
    tok: list[str] = []

    def _extract(definition: str) -> tuple[str, str] | None:
        parts = definition.split()
        if len(parts) < 2:
            return None
        name = parts[0].strip()
        if not name or name in _METADATA_COLUMNS:
            return None
        # Column type is always the second whitespace-separated token,
        # before any NOT NULL / OPTIONS(...) suffix.
        col_type = parts[1].strip().upper()
        return name, col_type

    for ch in new_schema:
        if ch == "(":
            depth += 1
            tok.append(ch)
        elif ch == ")":
            depth -= 1
            tok.append(ch)
        elif ch == "," and depth == 0:
            s = "".join(tok).strip()
            pair = _extract(s) if s else None
            if pair:
                pairs.append(pair)
            tok = []
        else:
            tok.append(ch)
    tail = "".join(tok).strip()
    pair = _extract(tail) if tail else None
    if pair:
        pairs.append(pair)
    return pairs


def _build_copy_sql(bq, migration: Migration, project: str) -> str | None:
    """INSERT INTO DataForSEO.new_name SELECT ... FROM BACKUP.old_name."""
    if migration.old_name is None:
        return None  # pure-new table — no rows to copy

    old_cols_map = _get_old_columns(bq, migration.old_name, project, BACKUP_DATASET)
    old_cols = set(old_cols_map.keys())
    endpoint_pairs = _parse_endpoint_columns(migration.new_schema)

    # NOT NULL metadata columns need a placeholder when the source is NULL
    # (we've seen bulk_pages_summary rows with NULL ingest_timestamp, etc.).
    # Phase 3 step 3a rewrites these sentinel values with real synthetic UUIDs
    # and logs them to Logs.upload_events.
    BACKFILL_SENTINEL = "pending-backfill"

    select_exprs: list[str] = []
    # Metadata block (preserve where present, default otherwise).
    # job_id, upload_id, ingest_timestamp are NOT NULL — always COALESCE.
    if "job_id" in old_cols:
        select_exprs.append(f"COALESCE(job_id, '{BACKFILL_SENTINEL}') AS job_id")
    else:
        select_exprs.append(f"'{BACKFILL_SENTINEL}' AS job_id")
    if "upload_id" in old_cols:
        select_exprs.append(f"COALESCE(upload_id, '{BACKFILL_SENTINEL}') AS upload_id")
    else:
        select_exprs.append(f"'{BACKFILL_SENTINEL}' AS upload_id")
    if "ingest_timestamp" in old_cols:
        select_exprs.append("COALESCE(ingest_timestamp, CURRENT_TIMESTAMP()) AS ingest_timestamp")
    else:
        select_exprs.append("CURRENT_TIMESTAMP() AS ingest_timestamp")
    select_exprs.append("CAST(NULL AS INT64) AS domain_id")
    # `domain`: preserve only for endpoints where old semantics match caller-context.
    if migration.preserve_domain_column and "domain" in old_cols:
        select_exprs.append("domain")
    else:
        select_exprs.append("CAST(NULL AS STRING) AS domain")
    if "task_id" in old_cols:
        select_exprs.append("task_id")
    else:
        select_exprs.append("CAST(NULL AS STRING) AS task_id")
    select_exprs.append("'live' AS endpoint_mode")
    # Endpoint-specific columns. Each target column gets one of:
    #   - If absent from source: typed NULL cast (BOOL/INT64/TIMESTAMP/STRING/...).
    #   - If present with type mismatch: TO_JSON_STRING for ARRAY/STRUCT→STRING,
    #     SAFE_CAST for scalar mismatches.
    #   - If types match: pass through unchanged.
    for col, target_type in endpoint_pairs:
        if col == "domain":
            continue  # already handled above
        if col in old_cols:
            src_type = old_cols_map[col]
            select_exprs.append(_source_to_target_expr(col, src_type, target_type))
        else:
            select_exprs.append(f"CAST(NULL AS {target_type}) AS {col}")

    select_clause = ",\n  ".join(select_exprs)
    insert_cols = _METADATA_COLUMNS + [c for c, _ in endpoint_pairs if c != "domain"]
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
    endpoint_pairs = _parse_endpoint_columns(migration.new_schema)
    all_cols = _METADATA_COLUMNS + [c for c, _ in endpoint_pairs if c != "domain"]
    cols_joined = ", ".join(all_cols)
    domain_note = (
        "domain preserved from source"
        if migration.preserve_domain_column
        else "domain=NULL (old column dropped per semantic review)"
    )
    return (
        f"-- DRY RUN: Actual SQL generated at execute time by introspecting\n"
        f"-- `{project}.{BACKUP_DATASET}.{migration.old_name}` via INFORMATION_SCHEMA.\n"
        f"-- Target columns: {cols_joined}\n"
        f"-- Historical defaults: endpoint_mode='live', task_id=NULL, domain_id=NULL\n"
        f"-- project_id will be dropped if present in source\n"
        f"-- {domain_note}\n"
        f"INSERT INTO `{project}.{SOURCE_DATASET}.{migration.new_name}` ({cols_joined})\n"
        f"SELECT ...\n"
        f"FROM `{project}.{BACKUP_DATASET}.{migration.old_name}`;"
    )


def _count_rows(bq, full_table_id: str) -> int:
    sql = f"SELECT COUNT(*) AS n FROM `{full_table_id}`"
    df = bq.client.query(sql).result().to_dataframe()
    return int(df["n"].iloc[0])


def _do_migrate(bq, migrations: list[Migration], project: str, dry_run: bool) -> int:
    click.echo(f"\n=== PHASE 2: MIGRATE ===")
    click.echo(f"Copy source: {project}.{BACKUP_DATASET}")
    click.echo(f"Copy target: {project}.{SOURCE_DATASET}")
    click.echo("Verifies row counts after copy. Drops old tables only on match.\n")

    # Track success so we know which drops are safe.
    copied_successfully: list[Migration] = []

    for m in migrations:
        click.echo(f"\n--- {m.new_name} ---")
        # TRUNCATE first so phase 2 is idempotent — safe to re-run after a
        # partial-success abort. Empty tables are a cheap no-op for TRUNCATE.
        truncate_sql = f"TRUNCATE TABLE `{project}.{SOURCE_DATASET}.{m.new_name}`;"
        _execute_or_print(bq, truncate_sql, dry_run=dry_run, label="TRUNCATE TARGET")
        if dry_run:
            copy_sql = _build_copy_sql_preview(m, project)
        else:
            copy_sql = _build_copy_sql(bq, m, project)
        _execute_or_print(bq, copy_sql, dry_run=dry_run, label="COPY DATA")

        # Row-count verify (skip in dry-run; the tables don't exist yet).
        if dry_run or m.old_name is None:
            copied_successfully.append(m)
            continue
        backup_id = f"{project}.{BACKUP_DATASET}.{m.old_name}"
        new_id = f"{project}.{SOURCE_DATASET}.{m.new_name}"
        backup_n = _count_rows(bq, backup_id)
        new_n = _count_rows(bq, new_id)
        if backup_n != new_n:
            click.echo(
                f"ROW COUNT MISMATCH for {m.new_name}: backup={backup_n:,} new={new_n:,}. "
                f"Aborting migrate phase before drop-old step.",
                err=True,
            )
            return 1
        click.echo(f"  row count OK: {new_n:,} rows")
        copied_successfully.append(m)

    # Drop old tables from SOURCE_DATASET (backup still holds them).
    click.echo("\n--- Dropping old rename-source tables + legacy backup from DataForSEO ---")
    for m in copied_successfully:
        if m.old_name is None:
            continue
        if m.old_name == m.new_name:
            # In-place migrations already had their old table dropped in phase 1.
            continue
        _execute_or_print(bq, _drop_source_table_sql(project, m.old_name),
                          dry_run=dry_run, label=f"DROP OLD {m.old_name}")
    for extra in EXTRA_BACKUP_TABLES:
        _execute_or_print(bq, _drop_source_table_sql(project, extra),
                          dry_run=dry_run, label=f"DROP LEGACY {extra}")

    click.echo("\n=== Phase 2 complete. ===")
    click.echo("Next: --phase=backfill to fill domain_id + synthesize missing job_id/upload_id.")
    return 0


# ---------------------------------------------------------------------------
# PHASE 3: BACKFILL — synthetic IDs only.
#
# Domain backfill is handled separately per the manual-mapping plan
# (scripts/inventory_migrated_domains.py + scripts/apply_domain_mapping.py —
# TBD, not in phase 3).
# ---------------------------------------------------------------------------

_BACKFILL_SENTINEL = "pending-backfill"

# Rows get a synthetic (job_id, upload_id) per hour-truncated ingest_timestamp.
# Microsecond precision in prod makes per-row timestamps unique, so we need
# to coarsen to get meaningful "upload window" groupings.
_ID_GROUP_TRUNC = "HOUR"

# Tables we sentinel-backfill and the rule for each.
#   both: sentinel lives in BOTH job_id and upload_id → replace both,
#         grouped by hour-truncated ingest_timestamp.
#   upload_only: only upload_id is sentinel; real job_id is preserved →
#                replace only upload_id, grouped by (real job_id, hour).
_SENTINEL_BOTH_TABLES = ["backlinks-summary", "serp-google-organic"]
_SENTINEL_UPLOAD_ONLY_TABLES = ["dataforseo_labs-google-ranked_keywords"]


def _query_hour_groups_both(project: str, table: str) -> str:
    return (
        f"SELECT TIMESTAMP_TRUNC(ingest_timestamp, {_ID_GROUP_TRUNC}) AS hour_bucket,\n"
        f"       COUNT(*) AS row_count\n"
        f"FROM `{project}.{SOURCE_DATASET}.{table}`\n"
        f"WHERE job_id = '{_BACKFILL_SENTINEL}' OR upload_id = '{_BACKFILL_SENTINEL}'\n"
        f"GROUP BY hour_bucket\n"
        f"ORDER BY hour_bucket;"
    )


def _query_hour_groups_upload_only(project: str, table: str) -> str:
    return (
        f"SELECT job_id,\n"
        f"       TIMESTAMP_TRUNC(ingest_timestamp, {_ID_GROUP_TRUNC}) AS hour_bucket,\n"
        f"       COUNT(*) AS row_count\n"
        f"FROM `{project}.{SOURCE_DATASET}.{table}`\n"
        f"WHERE upload_id = '{_BACKFILL_SENTINEL}'\n"
        f"GROUP BY job_id, hour_bucket\n"
        f"ORDER BY hour_bucket;"
    )


def _build_merge_both_ids(project: str, table: str, mapping: list[dict]) -> str:
    """MERGE: replace sentinel job_id + upload_id, grouped by hour bucket.

    Uses per-column IF() so real (non-sentinel) values are preserved on the
    rare row where only one of the two was sentinel.
    """
    values = ",\n    ".join(
        f"STRUCT(TIMESTAMP '{m['hour_bucket']}' AS hour_bucket, "
        f"'{m['synth_job']}' AS synth_job, '{m['synth_upload']}' AS synth_upload)"
        for m in mapping
    )
    return (
        f"MERGE `{project}.{SOURCE_DATASET}.{table}` t\n"
        f"USING (\n"
        f"  SELECT hour_bucket, synth_job, synth_upload\n"
        f"  FROM UNNEST([\n"
        f"    {values}\n"
        f"  ])\n"
        f") m\n"
        f"ON TIMESTAMP_TRUNC(t.ingest_timestamp, {_ID_GROUP_TRUNC}) = m.hour_bucket\n"
        f"WHEN MATCHED\n"
        f"  AND (t.job_id = '{_BACKFILL_SENTINEL}' OR t.upload_id = '{_BACKFILL_SENTINEL}') THEN\n"
        f"UPDATE SET\n"
        f"  job_id    = IF(t.job_id    = '{_BACKFILL_SENTINEL}', m.synth_job,    t.job_id),\n"
        f"  upload_id = IF(t.upload_id = '{_BACKFILL_SENTINEL}', m.synth_upload, t.upload_id);"
    )


def _build_merge_upload_only(project: str, table: str, mapping: list[dict]) -> str:
    """MERGE: replace ONLY sentinel upload_id, grouped by (real job_id, hour).

    Real job_ids are never modified.
    """
    values = ",\n    ".join(
        f"STRUCT('{m['job_id']}' AS job_id, TIMESTAMP '{m['hour_bucket']}' AS hour_bucket, "
        f"'{m['synth_upload']}' AS synth_upload)"
        for m in mapping
    )
    return (
        f"MERGE `{project}.{SOURCE_DATASET}.{table}` t\n"
        f"USING (\n"
        f"  SELECT job_id, hour_bucket, synth_upload\n"
        f"  FROM UNNEST([\n"
        f"    {values}\n"
        f"  ])\n"
        f") m\n"
        f"ON t.job_id = m.job_id\n"
        f"   AND TIMESTAMP_TRUNC(t.ingest_timestamp, {_ID_GROUP_TRUNC}) = m.hour_bucket\n"
        f"WHEN MATCHED AND t.upload_id = '{_BACKFILL_SENTINEL}' THEN\n"
        f"UPDATE SET upload_id = m.synth_upload;"
    )


def _log_sentinel_backfill_event(bq, project: str, table: str, *,
                                  job_id: str, upload_id: str,
                                  row_count: int, hour_bucket) -> None:
    """Insert one Logs.upload_events row per synthetic upload_id."""
    import datetime as _dt
    log_table = f"{project}.Logs.upload_events"
    try:
        hour_str = hour_bucket.strftime("%Y-%m-%d %H:00 UTC")
    except AttributeError:
        hour_str = str(hour_bucket)
    entry = [{
        "job_id": job_id,
        "upload_id": upload_id,
        "source": "dataforseo",
        "source_program": "migrate_dataforseo_tables.py:phase=backfill",
        "dataset": SOURCE_DATASET,
        "table": table,
        "row_count": row_count,
        "ingest_timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "client_id": None,
        "project_id": None,
        "notes": (
            f"historical migration sentinel backfill 2026-04-22: "
            f"rows with ingest_timestamp in the {hour_str} hour bucket"
        ),
    }]
    errors = bq.client.insert_rows_json(log_table, entry)
    if errors:
        click.echo(f"WARNING: upload_events log failed for {table} @ {hour_str}: {errors}", err=True)


def _do_backfill(bq, migrations: list[Migration], project: str, dry_run: bool) -> int:
    click.echo(f"\n=== PHASE 3: BACKFILL (sentinel IDs only) ===")
    click.echo(
        "Replaces the 'pending-backfill' sentinel stamped during phase 2 with\n"
        "synthetic UUIDs, grouped by hour-truncated ingest_timestamp.\n"
        "One Logs.upload_events row is inserted per synthetic upload_id.\n"
        "Domain backfill is handled separately via the manual-mapping flow.\n"
    )

    # Path 1: tables where both job_id and upload_id are sentinel.
    for table in _SENTINEL_BOTH_TABLES:
        click.echo(f"\n--- {table} (both job_id + upload_id sentinels, group by hour) ---")
        query = _query_hour_groups_both(project, table)
        if dry_run:
            click.echo("\n[dry-run] Would query distinct hour buckets:")
            click.echo(query)
            click.echo(
                "\n[dry-run] Would then emit a MERGE that UNNESTs an array of "
                "(hour_bucket, synth_job_uuid, synth_upload_uuid) structs — "
                "one entry per bucket found — and UPDATEs rows matched by "
                "TIMESTAMP_TRUNC(..., HOUR)."
            )
            click.echo(
                "[dry-run] Per-column IF() preserves any real (non-sentinel) "
                "job_id / upload_id that might coexist with a sentinel in the "
                "other column."
            )
            continue

        rows = list(bq.client.query(query).result())
        if not rows:
            click.echo(f"  no sentinel rows — skipping")
            continue
        mapping = [
            {"hour_bucket": r.hour_bucket,
             "synth_job": str(uuid.uuid4()),
             "synth_upload": str(uuid.uuid4()),
             "row_count": r.row_count}
            for r in rows
        ]
        merge_sql = _build_merge_both_ids(project, table, mapping)
        _execute_or_print(bq, merge_sql, dry_run=False, label=f"MERGE {table}")
        for m in mapping:
            _log_sentinel_backfill_event(
                bq, project, table,
                job_id=m["synth_job"], upload_id=m["synth_upload"],
                row_count=m["row_count"], hour_bucket=m["hour_bucket"],
            )
        click.echo(
            f"  {table}: stamped {sum(m['row_count'] for m in mapping):,} rows "
            f"across {len(mapping)} hour bucket(s); logged {len(mapping)} upload_events."
        )

    # Path 2: ranked_keywords — real job_id preserved, only upload_id is sentinel.
    for table in _SENTINEL_UPLOAD_ONLY_TABLES:
        click.echo(f"\n--- {table} (upload_id sentinel only, group by job_id + hour) ---")
        query = _query_hour_groups_upload_only(project, table)
        if dry_run:
            click.echo("\n[dry-run] Would query distinct (job_id, hour) groups:")
            click.echo(query)
            click.echo(
                "\n[dry-run] Would then MERGE by (job_id, TIMESTAMP_TRUNC(..., HOUR)), "
                "setting a new synth_upload_id per group. Real job_id is never modified."
            )
            continue

        rows = list(bq.client.query(query).result())
        if not rows:
            click.echo(f"  no sentinel rows — skipping")
            continue
        mapping = [
            {"job_id": r.job_id,
             "hour_bucket": r.hour_bucket,
             "synth_upload": str(uuid.uuid4()),
             "row_count": r.row_count}
            for r in rows
        ]
        merge_sql = _build_merge_upload_only(project, table, mapping)
        _execute_or_print(bq, merge_sql, dry_run=False, label=f"MERGE {table}")
        for m in mapping:
            _log_sentinel_backfill_event(
                bq, project, table,
                job_id=m["job_id"],  # real job_id, not synthetic
                upload_id=m["synth_upload"],
                row_count=m["row_count"], hour_bucket=m["hour_bucket"],
            )
        click.echo(
            f"  {table}: stamped {sum(m['row_count'] for m in mapping):,} rows "
            f"across {len(mapping)} (job_id, hour) group(s); logged {len(mapping)} upload_events."
        )

    click.echo("\n=== Phase 3 complete. ===")
    click.echo(
        "Domain backfill (manual mapping flow) is separate — "
        "run scripts/inventory_migrated_domains.py when ready."
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
    "setup":    _do_setup,
    "migrate":  _do_migrate,
    "backfill": _do_backfill,
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
