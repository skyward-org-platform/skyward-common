"""Refresh the cached DataForSEO location catalog: `DataForSEO.locations`.

Pulls the SERP, Google Ads and Labs location lists (all free, no balance used) and
replaces the table with one WRITE_TRUNCATE load. Safe to re-run any time.

Usage:
  python scripts/refresh_dfs_locations.py          # dry-run: fetch + print counts, no write
  python scripts/refresh_dfs_locations.py --yes    # fetch + replace the table
"""

from __future__ import annotations

import click
import pandas as pd


def _client():
    from skyward.config import load_config
    from skyward.data.bigquery import BigQueryClient
    from skyward.data.dataforseo import DataForSEOClient

    cfg = load_config()
    return DataForSEOClient(
        username=cfg.dataforseo_username, password=cfg.dataforseo_password,
        bq_client=BigQueryClient(project_id=cfg.datahub_project_id),
    )


@click.command()
@click.option("--yes", is_flag=True, help="Replace DataForSEO.locations (default: dry-run).")
def cli(yes: bool):
    from skyward.data.dataforseo.locations import merge_location_lists

    client = _client()
    if not yes:
        lists = client.locations.fetch_lists()
        for flag, rows in lists.items():
            click.echo(f"[dry-run] {flag}: {len(rows):,} codes")
        merged = merge_location_lists(lists, pd.Timestamp.now("UTC"))
        click.echo(f"[dry-run] merged catalog: {len(merged):,} rows")
        click.echo("Re-run with --yes to replace DataForSEO.locations.")
        return
    n = client.refresh_locations()
    click.echo(f"[exec] DataForSEO.locations replaced with {n:,} rows.")


if __name__ == "__main__":
    cli()
