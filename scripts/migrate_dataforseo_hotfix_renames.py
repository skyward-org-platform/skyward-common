"""Hot-fix after phase=migrate: backfill two tables whose column renames
the main migrate script couldn't auto-map.

1. keywords_data-google_ads-search_volume
   Old backup columns  -> new table columns
     local_search_volume -> search_volume
     local_location_code -> location_code
   906,137 historical rows had NULL in the new columns because the main
   migrate script's _build_copy_sql only matches on identical column names.

2. dataforseo_labs-google-search_intent
   Old `secondary_intents` (comma-joined labels, e.g. "commercial,navigational")
   -> new `secondary_keyword_intents` (JSON array of {label} dicts).
   Different column name AND format change — migrate script dropped it
   entirely. Here we convert via SPLIT + TO_JSON_STRING; the probability
   field stays implicit (old data never had probabilities).

Strategy: TRUNCATE + re-INSERT from backup for the two affected tables.
Idempotent — safe to re-run. Other tables are untouched.

Usage:
  python scripts/migrate_dataforseo_hotfix_renames.py --dry-run
  python scripts/migrate_dataforseo_hotfix_renames.py --yes
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


SOURCE_DATASET = "DataForSEO"
BACKUP_DATASET = "DataForSEO_backup_04_20_2026"


_bq_override = None


def _get_bq(project_override: str | None):
    if _bq_override is not None:
        return _bq_override
    from skyward.config import load_config
    from skyward.data.bigquery import BigQueryClient
    cfg = load_config()
    project = project_override or cfg.datahub_project_id
    return BigQueryClient(project_id=project)


# ---------------------------------------------------------------------------
# Hot-fix SQL generators
# ---------------------------------------------------------------------------

def _sql_search_volume(project: str) -> str:
    """Re-copy keywords_data-google_ads-search_volume with the local_* renames.

    Columns not in the old schema (cpc, competition, competition_index,
    low_top_of_page_bid, high_top_of_page_bid, monthly_searches — all added
    in Layer B fixes) stay NULL. Only search_volume and location_code get
    rescued from the old local_* columns.
    """
    return f"""INSERT INTO `{project}.{SOURCE_DATASET}.keywords_data-google_ads-search_volume` (
  job_id, upload_id, ingest_timestamp, domain_id, domain, task_id, endpoint_mode,
  keyword, search_volume, location_code,
  cpc, competition, competition_index,
  low_top_of_page_bid, high_top_of_page_bid, monthly_searches
)
SELECT
  COALESCE(job_id, 'pending-backfill') AS job_id,
  COALESCE(upload_id, 'pending-backfill') AS upload_id,
  COALESCE(ingest_timestamp, CURRENT_TIMESTAMP()) AS ingest_timestamp,
  CAST(NULL AS INT64) AS domain_id,
  CAST(NULL AS STRING) AS domain,
  CAST(NULL AS STRING) AS task_id,
  'live' AS endpoint_mode,
  keyword,
  local_search_volume AS search_volume,
  local_location_code AS location_code,
  CAST(NULL AS FLOAT64) AS cpc,
  CAST(NULL AS STRING) AS competition,
  CAST(NULL AS INT64) AS competition_index,
  CAST(NULL AS FLOAT64) AS low_top_of_page_bid,
  CAST(NULL AS FLOAT64) AS high_top_of_page_bid,
  CAST(NULL AS STRING) AS monthly_searches
FROM `{project}.{BACKUP_DATASET}.keyword_data-google_ads-search_volume`;"""


def _sql_search_intent(project: str) -> str:
    """Re-copy dataforseo_labs-google-search_intent with secondary_intents
    converted from comma-joined labels to the new JSON-array shape.

    Old value "commercial,navigational" becomes
    [{"label":"commercial"},{"label":"navigational"}]. Probability field is
    omitted (old data never had probabilities). NULL / empty source values
    become NULL in the new column.
    """
    return f"""INSERT INTO `{project}.{SOURCE_DATASET}.dataforseo_labs-google-search_intent` (
  job_id, upload_id, ingest_timestamp, domain_id, domain, task_id, endpoint_mode,
  keyword, search_intent, intent_probability, secondary_keyword_intents, language_code
)
SELECT
  COALESCE(job_id, 'pending-backfill') AS job_id,
  COALESCE(upload_id, 'pending-backfill') AS upload_id,
  COALESCE(ingest_timestamp, CURRENT_TIMESTAMP()) AS ingest_timestamp,
  CAST(NULL AS INT64) AS domain_id,
  CAST(NULL AS STRING) AS domain,
  CAST(NULL AS STRING) AS task_id,
  'live' AS endpoint_mode,
  keyword,
  search_intent,
  intent_probability,
  CASE
    WHEN secondary_intents IS NULL OR TRIM(secondary_intents) = '' THEN NULL
    ELSE TO_JSON_STRING(
      ARRAY(
        SELECT STRUCT(TRIM(label) AS label)
        FROM UNNEST(SPLIT(secondary_intents, ',')) AS label
        WHERE TRIM(label) != ''
      )
    )
  END AS secondary_keyword_intents,
  language_code
FROM `{project}.{BACKUP_DATASET}.dataforseo_labs_google_search_intent`;"""


_HOTFIXES = [
    ("keywords_data-google_ads-search_volume", _sql_search_volume),
    ("dataforseo_labs-google-search_intent", _sql_search_intent),
]


def _truncate_sql(project: str, table: str) -> str:
    return f"TRUNCATE TABLE `{project}.{SOURCE_DATASET}.{table}`;"


def _execute_or_print(bq, sql: str, *, dry_run: bool, label: str) -> None:
    prefix = "[dry-run]" if dry_run else "[exec]"
    click.echo(f"\n{prefix} {label}")
    click.echo(sql)
    if not dry_run:
        bq.client.query(sql).result()
        click.echo(f"{prefix} {label} — OK")


@click.command()
@click.option("--dry-run", is_flag=True, help="Print SQL without executing.")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.option("--project", default=None, help="Override GCP project id.")
def cli(dry_run: bool, yes: bool, project: str | None):
    """Re-copy the two rename-affected tables from backup with corrected column mapping."""
    bq = _get_bq(project)
    resolved_project = project or bq.project_id

    click.echo(f"Project: {resolved_project}")
    click.echo(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
    click.echo(f"Tables to hot-fix: {len(_HOTFIXES)}")
    for tbl, _ in _HOTFIXES:
        click.echo(f"  - {tbl}")

    if not dry_run and not yes:
        click.confirm("\nProceed? (TRUNCATEs both target tables and re-inserts)", abort=True)

    for table_name, sql_fn in _HOTFIXES:
        click.echo(f"\n--- {table_name} ---")
        _execute_or_print(bq, _truncate_sql(resolved_project, table_name),
                          dry_run=dry_run, label="TRUNCATE TARGET")
        _execute_or_print(bq, sql_fn(resolved_project),
                          dry_run=dry_run, label="INSERT WITH RENAMES")
        if not dry_run:
            cnt_sql = f"SELECT COUNT(*) AS n FROM `{resolved_project}.{SOURCE_DATASET}.{table_name}`"
            n = int(bq.client.query(cnt_sql).result().to_dataframe()["n"].iloc[0])
            click.echo(f"  row count after fix: {n:,}")

    click.echo("\n=== Hot-fix complete. ===")


if __name__ == "__main__":
    cli()
