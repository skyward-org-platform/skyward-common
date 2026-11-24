"""Create the deduped latest view over backlinks-history (Option B).

  DataForSEO.backlinks-history_latest    one row per client domain_id, target, and month

The view DDL lives in scripts/migrate_dataforseo_manifest.py. Safe to re-run (uses CREATE OR REPLACE VIEW).

  python scripts/create_dfs_backlinks_views.py          # dry-run: print the SQL
  python scripts/create_dfs_backlinks_views.py --yes    # execute
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate_dataforseo_manifest import (  # noqa: E402
    BACKLINKS_HISTORY_LATEST_VIEW,
    backlinks_history_view_statement,
)

DATASET = "DataForSEO"
DEFAULT_PROJECT = "data-hub-468216"


def _make_bq(project: str | None):
    from skyward.config import load_config
    from skyward.data.bigquery import BigQueryClient

    cfg = load_config()
    return BigQueryClient(project_id=project or cfg.datahub_project_id)


@click.command()
@click.option("--yes", is_flag=True, help="Execute the DDL (default: dry-run).")
@click.option("--project", default=None, help="Override the GCP project id.")
def cli(yes: bool, project: str | None):
    project_id = project or DEFAULT_PROJECT
    view_name, sql = backlinks_history_view_statement(project_id, DATASET)

    if not yes:
        click.echo(f"-- DRY RUN: {view_name}")
        click.echo(sql)
        click.echo("\nRe-run with --yes to execute in BigQuery.")
        return

    bq = _make_bq(project_id)
    click.echo(f"Creating or replacing view {project_id}.{DATASET}.{view_name}...")
    bq.query(sql)
    click.echo("Done.")


if __name__ == "__main__":
    cli()
