"""Domain inventory for the DataForSEO migration backfill.

Read-only. No BQ writes. Run this to see how many historical rows can be
resolved to a Meta.domains entry, and how many are orphans.

Reads from:
  DataForSEO_backup_04_20_2026.<old_table>  (populated by phase=backup)
    — or, if that doesn't exist yet, from DataForSEO.<old_table> directly

Reports:
  - Distinct domain values per table, normalized
  - Counts: (a) exact match to Meta.domains, (b) match after normalization,
    (c) completely unknown to Meta.domains
  - Sample of unknown domains (top 20 by row count) per table

Normalization rules (applied to both old-table domain and Meta.domains.domain):
  - Lowercase
  - Strip leading http://, https://, www.
  - Strip trailing slashes
  - Strip whitespace

Usage:
  python scripts/inventory_dataforseo_domains.py              # uses backup dataset if exists, else DataForSEO
  python scripts/inventory_dataforseo_domains.py --source=DataForSEO
  python scripts/inventory_dataforseo_domains.py --project data-hub-468216
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# Old tables that carry a `domain` column (per audit in docs/dataforseo_next_steps).
_OLD_TABLES_WITH_DOMAIN = [
    "backlinks_backlinks_live",
    "backlinks_bulk_pages_summary_live",
    "backlinks_summary_live",
    "serp_google_organic_live_advanced",
    "dataforseo_labs-google-ranked_keywords",
]

# Tables that never had a `domain` column — reported so we know to skip.
_OLD_TABLES_WITHOUT_DOMAIN = [
    "google_keyword-suggestions_live",
    "google_related-keywords_live",
    "dataforseo_labs_google_keyword_overview",
    "dataforseo_labs_google_search_intent",
    "keyword_data-google_ads-search_volume",
]

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


def _dataset_exists(bq, project: str, dataset: str) -> bool:
    try:
        bq.client.get_dataset(f"{project}.{dataset}")
        return True
    except Exception:
        return False


def _pick_source(bq, project: str, preferred: str | None) -> str:
    """Default to the backup dataset if present (so we don't hit prod if avoided),
    otherwise fall back to DataForSEO."""
    if preferred:
        return preferred
    if _dataset_exists(bq, project, "DataForSEO_backup_04_20_2026"):
        return "DataForSEO_backup_04_20_2026"
    return "DataForSEO"


def _inventory_one(bq, project: str, source_dataset: str, table: str) -> dict:
    """For one table, count how many rows/domains can be matched to Meta.domains."""
    norm_t = _NORMALIZE_SQL.format(col="t.domain")
    norm_m = _NORMALIZE_SQL.format(col="m.domain")

    # Aggregate per distinct domain value in the source table.
    sql = f"""
    WITH src AS (
      SELECT
        domain AS raw_domain,
        {norm_t} AS norm_domain,
        COUNT(*) AS row_count
      FROM `{project}.{source_dataset}.{table}` t
      WHERE domain IS NOT NULL
      GROUP BY domain, norm_domain
    ),
    meta AS (
      SELECT DISTINCT
        m.domain AS meta_domain,
        {norm_m} AS norm_domain
      FROM `{project}.Meta.domains` m
    )
    SELECT
      s.raw_domain,
      s.norm_domain,
      s.row_count,
      m.meta_domain,
      (m.meta_domain IS NOT NULL) AS matched,
      (m.meta_domain = s.raw_domain) AS exact_match
    FROM src s
    LEFT JOIN meta m
      ON s.norm_domain = m.norm_domain
    ORDER BY s.row_count DESC
    """
    df = bq.client.query(sql).result().to_dataframe()

    total_distinct = len(df)
    total_rows = int(df["row_count"].sum()) if total_distinct else 0
    exact = int(df["exact_match"].fillna(False).sum())
    exact_rows = int(df.loc[df["exact_match"].fillna(False), "row_count"].sum())
    normalized = int((df["matched"] & ~df["exact_match"].fillna(False)).sum())
    normalized_rows = int(df.loc[df["matched"] & ~df["exact_match"].fillna(False), "row_count"].sum())
    unknown = int((~df["matched"]).sum())
    unknown_rows = int(df.loc[~df["matched"], "row_count"].sum())

    unknowns_top = df[~df["matched"]].head(20)
    return {
        "table": table,
        "total_distinct": total_distinct,
        "total_rows": total_rows,
        "exact": exact,
        "exact_rows": exact_rows,
        "normalized": normalized,
        "normalized_rows": normalized_rows,
        "unknown": unknown,
        "unknown_rows": unknown_rows,
        "unknowns_top": unknowns_top,
    }


def _inventory_table_without_domain(bq, project: str, source_dataset: str, table: str) -> dict:
    """Row counts only — no domain signal to report."""
    sql = f"SELECT COUNT(*) AS n FROM `{project}.{source_dataset}.{table}`"
    try:
        n = int(bq.client.query(sql).result().to_dataframe()["n"].iloc[0])
    except Exception:
        n = 0
    return {"table": table, "total_rows": n}


@click.command()
@click.option("--source", default=None, help="Source dataset (defaults to backup if present, else DataForSEO).")
@click.option("--project", default=None, help="Override GCP project id.")
def cli(source: str | None, project: str | None):
    from skyward.config import load_config
    from skyward.data.bigquery import BigQueryClient
    cfg = load_config()
    resolved_project = project or cfg.datahub_project_id
    bq = BigQueryClient(project_id=resolved_project)

    source_dataset = _pick_source(bq, resolved_project, source)
    click.echo(f"Project: {resolved_project}")
    click.echo(f"Source:  {source_dataset}")
    click.echo("")

    click.echo("=" * 80)
    click.echo("Tables WITH domain column (resolvable via Meta.domains lookup)")
    click.echo("=" * 80)
    for table in _OLD_TABLES_WITH_DOMAIN:
        try:
            r = _inventory_one(bq, resolved_project, source_dataset, table)
        except Exception as e:
            click.echo(f"\n{table}: ERR {e}")
            continue
        click.echo(f"\n{table}")
        click.echo(f"  Total rows:                  {r['total_rows']:>12,}")
        click.echo(f"  Total distinct domains:      {r['total_distinct']:>12,}")
        click.echo(f"  Exact match (Meta.domains):  {r['exact']:>12,} domains  ({r['exact_rows']:>12,} rows)")
        click.echo(f"  Match after normalization:   {r['normalized']:>12,} domains  ({r['normalized_rows']:>12,} rows)")
        click.echo(f"  Unknown (not in Meta):       {r['unknown']:>12,} domains  ({r['unknown_rows']:>12,} rows)")
        if r["unknown"] > 0:
            click.echo(f"  Top unknown by row count:")
            for _, row in r["unknowns_top"].iterrows():
                raw = row["raw_domain"] or "(null)"
                click.echo(f"    - {raw[:70]:<70} ({row['row_count']:>8,} rows)")

    click.echo("")
    click.echo("=" * 80)
    click.echo("Tables WITHOUT domain column (domain_id stays NULL after migration)")
    click.echo("=" * 80)
    for table in _OLD_TABLES_WITHOUT_DOMAIN:
        try:
            r = _inventory_table_without_domain(bq, resolved_project, source_dataset, table)
            click.echo(f"  {table}: {r['total_rows']:,} rows (no domain signal)")
        except Exception as e:
            click.echo(f"  {table}: ERR {e}")


if __name__ == "__main__":
    cli()
