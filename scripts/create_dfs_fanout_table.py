"""Create the ChatGPT fan-out table and its two views (v1.6.2).

  DataForSEO.ai_optimization-chat_gpt-llm_scraper           one row per task
  DataForSEO.ai_optimization-chat_gpt-llm_scraper_queries   one row per distinct fan-out query
  DataForSEO.ai_optimization-chat_gpt-llm_scraper_sources   one row per cited source

The schema lives in scripts/migrate_dataforseo_manifest.py. Safe to re-run: the table is
CREATE TABLE IF NOT EXISTS and the views are CREATE OR REPLACE.

  python scripts/create_dfs_fanout_table.py          # dry-run: print the SQL
  python scripts/create_dfs_fanout_table.py --yes    # execute
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate_dataforseo_manifest import (  # noqa: E402
    AI_OPTIMIZATION_CHAT_GPT_LLM_SCRAPER_TABLE as TABLE, MIGRATIONS, fanout_view_statements,
)

DATASET = "DataForSEO"
DEFAULT_PROJECT = "data-hub-468216"


def table_statement(project: str) -> str:
    m = next(m for m in MIGRATIONS if m.new_name == TABLE)
    return (
        f"CREATE TABLE IF NOT EXISTS `{project}.{DATASET}.{m.new_name}` (\n"
        f"{m.new_schema.rstrip()}\n"
        f")\n"
        f"PARTITION BY DATE({m.partition_field})\n"
        f"CLUSTER BY {', '.join(m.clustering_fields)}\n"
        f'OPTIONS(description="ChatGPT fan-out queries per prompt (DataForSEO AI Optimization, '
        f'ChatGPT LLM Scraper). One row per task. Not tied to a client: keyed by prompt_id.")'
    )


def all_statements(project: str) -> list[tuple[str, str]]:
    return [(TABLE, table_statement(project)), *fanout_view_statements(project, DATASET)]


def _make_bq(project: str | None):
    from skyward.config import load_config
    from skyward.data.bigquery import BigQueryClient

    cfg = load_config()
    return BigQueryClient(project_id=project or cfg.datahub_project_id)


@click.command()
@click.option("--yes", is_flag=True, help="Execute the DDL (default: dry-run).")
@click.option("--project", default=None, help="Override the GCP project id.")
def cli(yes: bool, project: str | None):
    if not yes:
        click.echo("[dry-run] Would execute, in order:\n")
        for name, sql in all_statements(project or DEFAULT_PROJECT):
            click.echo(f"-- {name}\n{sql}\n")
        click.echo("[dry-run] Re-run with --yes to execute.")
        return
    bq = _make_bq(project)
    for name, sql in all_statements(bq.client.project):
        click.echo(f"[exec] {DATASET}.{name} ...")
        bq.client.query(sql).result()
    click.echo("[exec] Done.")


if __name__ == "__main__":
    cli()
