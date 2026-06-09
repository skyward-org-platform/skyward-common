"""Create the shared DataForSEO debug-log table: `DataForSEO.debug_request_logs`.

One-time DDL. The endpoint `upload()` / `DebugLogCollector` deliberately refuse to
auto-create tables, so this script provisions the destination the
`include_debug_logs=True` path writes to.

One row per live *attempt* across all DFS endpoints (currently only
keyword_suggestions + related_keywords capture; others carry a TODO).

Usage (dry-run prints the DDL, --yes executes):
  python scripts/create_dfs_debug_log_table.py            # dry-run
  python scripts/create_dfs_debug_log_table.py --yes      # execute
"""

from __future__ import annotations

import click

DATASET = "DataForSEO"
TABLE = "debug_request_logs"


def _full_table_id(project: str) -> str:
    return f"{project}.{DATASET}.{TABLE}"


def _ddl(project: str) -> str:
    # Partition by ingest date and cluster by job_id/endpoint — the two axes every
    # diagnostic query filters on (a single run's rows, by endpoint).
    return f"""
CREATE TABLE IF NOT EXISTS `{_full_table_id(project)}` (
  job_id              STRING    NOT NULL,
  upload_id           STRING    NOT NULL,
  endpoint            STRING,
  target              STRING,
  attempt             INT64,
  is_terminal         BOOL,
  started_at          STRING,
  duration_ms         INT64,
  thread_name         STRING,
  http_status         INT64,
  task_status_code    INT64,
  task_status_message STRING,
  task_cost           FLOAT64,
  n_items             INT64,
  payload             STRING,
  response            STRING,
  error               STRING,
  ingest_timestamp    TIMESTAMP NOT NULL
)
PARTITION BY DATE(ingest_timestamp)
CLUSTER BY job_id, endpoint
OPTIONS (
  description = 'Per-attempt DFS live-call debug log (opt-in via include_debug_logs). '
               'Input payload, raw response, timing, thread, transport/task status, cost.'
)
""".strip()


def _make_bq(project: str | None):
    from skyward.config import load_config
    from skyward.data.bigquery import BigQueryClient

    cfg = load_config()
    project = project or cfg.datahub_project_id
    return BigQueryClient(project_id=project), project


@click.command()
@click.option("--yes", is_flag=True, help="Execute the DDL (default: dry-run).")
@click.option("--project", default=None, help="Override the GCP project id.")
def cli(yes: bool, project: str | None):
    bq, project = _make_bq(project)
    ddl = _ddl(project)

    if not yes:
        click.echo("[dry-run] Would execute:\n")
        click.echo(ddl)
        click.echo(f"\n[dry-run] Target: {_full_table_id(project)}")
        click.echo("Re-run with --yes to create the table.")
        return

    click.echo(f"[exec] Creating {_full_table_id(project)} ...")
    bq.client.query(ddl).result()
    click.echo("[exec] Done (CREATE TABLE IF NOT EXISTS — no-op if it already existed).")


if __name__ == "__main__":
    cli()
