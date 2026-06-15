"""Create the standard-mode collector tracking tables:
`DataForSEO.dfs_job_summary` and `DataForSEO.dfs_task_log`.

One-time DDL. The producer (submit path) and the always-on collector both write
these; nothing auto-creates BQ tables, so this provisions them. See the plan at
docs/superpowers/plans/2026-06-12-dfs-standard-mode-collector.md and ClickUp 86bac9q9y.

Usage (dry-run prints the DDL, --yes executes):
  python scripts/create_dfs_collector_tables.py            # dry-run
  python scripts/create_dfs_collector_tables.py --yes      # execute
"""

from __future__ import annotations

import click

DATASET = "DataForSEO"
PROJECT_DEFAULT = "data-hub-468216"

JOB_SUMMARY_DDL = f"""
CREATE TABLE IF NOT EXISTS `{PROJECT_DEFAULT}.{DATASET}.dfs_job_summary` (
  job_id        STRING    NOT NULL OPTIONS(description="Producer run job_id; 'd4s_standard_unattributed' for collector-drained tasks with no producer."),
  endpoint      STRING    NOT NULL OPTIONS(description="Endpoint key, e.g. 'serp_google_organic'. Distinct row per (job_id, endpoint)."),
  total_tasks   INT64              OPTIONS(description="Tasks the producer posted for this (job_id, endpoint); written at submit."),
  fetched_count INT64              OPTIONS(description="Tasks the collector has fetched results for; incremented per collector cycle."),
  failed_count  INT64              OPTIONS(description="Tasks that resolved to a failure status."),
  status        STRING             OPTIONS(description="submitting | pending | done | partial."),
  submitted_at  TIMESTAMP          OPTIONS(description="When the producer finished submitting this job+endpoint."),
  last_updated  TIMESTAMP          OPTIONS(description="Touched by the collector each cycle it makes progress."),
  notes         STRING             OPTIONS(description="Optional free-form (e.g. submit-time rejection counts).")
)
CLUSTER BY job_id
OPTIONS(description="Per-(job_id,endpoint) progress rollup for standard-mode DFS runs. Producer reads one tiny row to track completion.")
""".strip()

TASK_LOG_DDL = f"""
CREATE TABLE IF NOT EXISTS `{PROJECT_DEFAULT}.{DATASET}.dfs_task_log` (
  task_id         STRING    NOT NULL OPTIONS(description="DFS task_id."),
  job_id          STRING    NOT NULL OPTIONS(description="Back-ref to the producer run; 'd4s_standard_unattributed' if untracked."),
  endpoint        STRING    NOT NULL OPTIONS(description="Endpoint key, e.g. 'serp_google_organic'."),
  keyword         STRING             OPTIONS(description="The target/keyword the task was posted for."),
  submitted_at    TIMESTAMP          OPTIONS(description="When the producer posted the task."),
  fetched_at      TIMESTAMP          OPTIONS(description="When the collector retrieved results."),
  status          STRING             OPTIONS(description="pending | fetched | failed_not_found | failed_other."),
  dfs_status_code INT64              OPTIONS(description="DFS task-level status_code on the resolving fetch (e.g. 20000, 40401)."),
  result_rows     INT64              OPTIONS(description="Rows written to the canonical endpoint table for this task."),
  attempts        INT64              OPTIONS(description="task_get attempts before resolution.")
)
PARTITION BY DATE(submitted_at)
CLUSTER BY endpoint, status, job_id
OPTIONS(description="Per-task ledger for standard-mode DFS runs. Collector's 'what's still pending on endpoint X' query and the recovery source-of-truth.")
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

    if not yes:
        click.echo("[dry-run] Would execute:\n")
        click.echo(JOB_SUMMARY_DDL)
        click.echo("\n" + TASK_LOG_DDL)
        click.echo("\n[dry-run] Re-run with --yes to create both tables.")
        return

    for label, ddl in (("dfs_job_summary", JOB_SUMMARY_DDL), ("dfs_task_log", TASK_LOG_DDL)):
        click.echo(f"[exec] Creating {DATASET}.{label} ...")
        bq.client.query(ddl).result()
    click.echo("[exec] Done (CREATE TABLE IF NOT EXISTS — no-op if they already existed).")


if __name__ == "__main__":
    cli()
