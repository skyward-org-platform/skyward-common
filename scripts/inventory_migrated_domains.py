"""Build a domain-inventory CSV for the manual domain-mapping workflow.

Scope: only the two migrated tables that carry caller-context `domain` strings:
  - DataForSEO.backlinks-backlinks
  - DataForSEO.dataforseo_labs-google-ranked_keywords

For each distinct raw domain string across those two tables, writes one CSV
row with:

  raw_domain                 — the string as it appears in the table
  normalized_domain          — lowercased, scheme/www./trailing-slash stripped
  rows_ranked_keywords       — row count in ranked_keywords
  rows_backlinks_backlinks   — row count in backlinks-backlinks
  rows_total                 — sum
  meta_match_domain_id       — matching Meta.domains.domain_id (if normalized
                               forms match exactly)
  meta_match_domain          — matching Meta.domains.domain (canonical form)
  suggested                  — 'MATCH' if a Meta.domain was found, else 'CREATE'
  decision                   — EMPTY. You fill this in manually. Valid values:
                                 MATCH <domain_id>  — existing Meta.domains entry
                                 CREATE             — new Meta.domains entry needed
                                 SKIP               — don't backfill; leave NULL

Usage:
  uv run python scripts/inventory_migrated_domains.py
  uv run python scripts/inventory_migrated_domains.py --out /tmp/mapping.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


_DEFAULT_OUT = Path(__file__).resolve().parents[1] / "docs" / "domain_mapping_2026_04_22.csv"


_QUERY = r"""
WITH src AS (
  SELECT domain AS raw_domain, 'ranked_keywords' AS src_table
  FROM `{project}.DataForSEO.dataforseo_labs-google-ranked_keywords`
  WHERE domain IS NOT NULL AND domain != ''
  UNION ALL
  SELECT domain AS raw_domain, 'backlinks_backlinks' AS src_table
  FROM `{project}.DataForSEO.backlinks-backlinks`
  WHERE domain IS NOT NULL AND domain != ''
),
src_grouped AS (
  SELECT
    raw_domain,
    LOWER(
      REGEXP_REPLACE(
        REGEXP_REPLACE(
          REGEXP_REPLACE(raw_domain, r'^(https?://)?(www\.)?', ''),
          r'/+$', ''
        ),
        r'\s+', ''
      )
    ) AS normalized_domain,
    COUNTIF(src_table = 'ranked_keywords') AS rows_ranked_keywords,
    COUNTIF(src_table = 'backlinks_backlinks') AS rows_backlinks_backlinks,
    COUNT(*) AS rows_total
  FROM src
  GROUP BY raw_domain
),
meta AS (
  SELECT
    domain_id,
    domain AS meta_domain,
    LOWER(
      REGEXP_REPLACE(
        REGEXP_REPLACE(
          REGEXP_REPLACE(domain, r'^(https?://)?(www\.)?', ''),
          r'/+$', ''
        ),
        r'\s+', ''
      )
    ) AS normalized_domain
  FROM `{project}.Meta.domains`
)
SELECT
  s.raw_domain,
  s.normalized_domain,
  s.rows_ranked_keywords,
  s.rows_backlinks_backlinks,
  s.rows_total,
  m.domain_id AS meta_match_domain_id,
  m.meta_domain AS meta_match_domain,
  IF(m.domain_id IS NOT NULL, 'MATCH', 'CREATE') AS suggested
FROM src_grouped s
LEFT JOIN meta m ON s.normalized_domain = m.normalized_domain
ORDER BY s.rows_total DESC
"""


@click.command()
@click.option(
    "--out",
    type=click.Path(path_type=Path, dir_okay=False),
    default=_DEFAULT_OUT,
    help="Output CSV path. Default: docs/domain_mapping_2026_04_22.csv",
)
@click.option("--project", default=None, help="Override GCP project id.")
def cli(out: Path, project: str | None):
    """Dump the domain-inventory CSV for manual annotation."""
    from skyward.config import load_config
    from skyward.data.bigquery import BigQueryClient

    cfg = load_config()
    resolved_project = project or cfg.datahub_project_id
    bq = BigQueryClient(project_id=resolved_project)

    click.echo(f"Project: {resolved_project}")
    click.echo(f"Output:  {out}")
    click.echo("\nRunning domain inventory query on DataForSEO.backlinks-backlinks + ranked_keywords…")

    df = bq.client.query(_QUERY.format(project=resolved_project)).result().to_dataframe()
    df["decision"] = ""

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    total = len(df)
    matched = int(df["meta_match_domain_id"].notna().sum())
    to_create = total - matched
    ranked_only = int((df["rows_backlinks_backlinks"] == 0).sum())
    backlinks_only = int((df["rows_ranked_keywords"] == 0).sum())
    both = total - ranked_only - backlinks_only

    click.echo(f"\nWrote {total:,} distinct domains to {out}")
    click.echo(f"  Suggested MATCH (found in Meta.domains via normalization): {matched:,}")
    click.echo(f"  Suggested CREATE (not found in Meta.domains):             {to_create:,}")
    click.echo(f"\n  Present in ranked_keywords only:                          {ranked_only:,}")
    click.echo(f"  Present in backlinks-backlinks only:                      {backlinks_only:,}")
    click.echo(f"  Present in both:                                          {both:,}")

    click.echo(f"\nNext steps:")
    click.echo(f"  1. Open {out} in your editor / sheet app.")
    click.echo(f"  2. For each row, fill in the `decision` column with one of:")
    click.echo(f"       MATCH <domain_id>  — existing Meta.domains entry (use the suggested id or pick another)")
    click.echo(f"       CREATE             — a new Meta.domains entry should be provisioned")
    click.echo(f"       SKIP               — don't backfill; this row's domain_id stays NULL")
    click.echo(f"  3. Save the annotated CSV. Then run the apply step (next script, to be built).")


if __name__ == "__main__":
    cli()
