"""Seed or refresh `DataForSEO.cost_estimates` from skyward.data.dataforseo.cost_rates.

Replaces the table contents with one WRITE_TRUNCATE load. Re-run whenever the rate card
constants change.

Usage:
  python scripts/seed_dfs_cost_estimates.py          # dry-run: print the rates
  python scripts/seed_dfs_cost_estimates.py --yes    # replace the table
"""

from __future__ import annotations

import click
import pandas as pd

from skyward.data.dataforseo.cost_rates import DEFAULT_RATES


@click.command()
@click.option("--yes", is_flag=True, help="Replace DataForSEO.cost_estimates (default: dry-run).")
def cli(yes: bool):
    df = pd.DataFrame([r.to_row() for r in DEFAULT_RATES])
    if not yes:
        with pd.option_context("display.width", 200, "display.max_columns", 20):
            click.echo(df[["endpoint", "endpoint_mode", "price_per_request_usd",
                           "price_per_item_usd", "item_unit", "verified_on"]].to_string(index=False))
        click.echo(f"\n[dry-run] {len(df)} rates. Re-run with --yes to replace the table.")
        return

    from google.cloud import bigquery

    from skyward.config import load_config
    from skyward.data.bigquery import BigQueryClient
    from skyward.functions import generate_job_id, generate_upload_id

    cfg = load_config()
    bq = BigQueryClient(project_id=cfg.datahub_project_id)
    table = f"{bq.client.project}.DataForSEO.cost_estimates"
    job = bq.client.load_table_from_dataframe(
        df, table,
        job_config=bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE),
    )
    job.result()
    bq.log_upload_event(
        job_id=generate_job_id(), upload_id=generate_upload_id(), source="dataforseo",
        source_program="seed_dfs_cost_estimates", dataset="DataForSEO", table="cost_estimates",
        row_count=len(df), timestamp=pd.Timestamp.now("UTC"),
    )
    click.echo(f"[exec] {table} replaced with {len(df)} rates.")


if __name__ == "__main__":
    cli()
