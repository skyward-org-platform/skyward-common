"""Create the v1.6.1 DataForSEO cost tables and views.

Tables: DataForSEO.cost_log, job_runs, cost_estimates, locations.
Views:  job_costs, job_progress, endpoint_cost_actuals, locations_* (countries, US types).

One-time DDL (tables use CREATE TABLE IF NOT EXISTS; views use CREATE OR REPLACE VIEW, so
re-running updates view SQL without touching data). Nothing auto-creates these tables.

Usage:
  python scripts/create_dfs_cost_tables.py          # dry-run: print every statement
  python scripts/create_dfs_cost_tables.py --yes    # execute
"""

from __future__ import annotations

import click

PROJECT_DEFAULT = "data-hub-468216"
DATASET = "DataForSEO"
T = f"{PROJECT_DEFAULT}.{DATASET}"

COST_LOG_DDL = f"""
CREATE TABLE IF NOT EXISTS `{T}.cost_log` (
  job_id            STRING    NOT NULL OPTIONS(description="Caller's job_id."),
  upload_id         STRING             OPTIONS(description="Save window the request's rows joined; NULL for upload=False and standard mode (link by task_id)."),
  task_id           STRING             OPTIONS(description="DataForSEO task id; links standard-mode cost to collector-fetched rows."),
  endpoint          STRING    NOT NULL OPTIONS(description="Endpoint key, e.g. 'serp_google_organic'."),
  endpoint_mode     STRING    NOT NULL OPTIONS(description="live | standard."),
  call_type         STRING             OPTIONS(description="live | live_page | task_post."),
  attempt           INT64              OPTIONS(description="1-based attempt for the same payload within one unit of work."),
  http_status       INT64              OPTIONS(description="Transport HTTP status."),
  dfs_status_code   INT64              OPTIONS(description="DataForSEO task status_code (20000 ok, 20100 task created)."),
  items_sent        INT64              OPTIONS(description="Keywords or targets sent in the task."),
  result_rows       INT64              OPTIONS(description="Rows DataForSEO returned for the task."),
  price_inputs      JSON               OPTIONS(description="Pricing-relevant payload fields (depth, limit, offset, location, language)."),
  cost_usd          NUMERIC            OPTIONS(description="tasks[i].cost as reported by DataForSEO."),
  client_id         STRING             OPTIONS(description="Client id when the caller supplies one."),
  requested_at      TIMESTAMP          OPTIONS(description="When the response came back. Informational only; never used for linking."),
  ingest_timestamp  TIMESTAMP NOT NULL OPTIONS(description="When the row was written.")
)
PARTITION BY DATE(ingest_timestamp)
CLUSTER BY job_id, endpoint
OPTIONS(description="One row per billed DataForSEO task (live requests and task_post submissions). Written automatically by skyward-common v1.6.1+.")
""".strip()

JOB_RUNS_DDL = f"""
CREATE TABLE IF NOT EXISTS `{T}.job_runs` (
  job_id            STRING    NOT NULL OPTIONS(description="Caller's job_id."),
  endpoint          STRING    NOT NULL OPTIONS(description="Endpoint key."),
  endpoint_mode     STRING    NOT NULL OPTIONS(description="live | standard."),
  event             STRING    NOT NULL OPTIONS(description="start | end."),
  status            STRING    NOT NULL OPTIONS(description="running | completed | failed | stopped_low_balance | rejected_low_balance | rejected_location."),
  planned_requests  INT64              OPTIONS(description="Worst-case requests/tasks for the run."),
  planned_items     INT64              OPTIONS(description="Worst-case billable items in the rate's unit."),
  planned_max_rows  INT64              OPTIONS(description="Worst-case data rows (drives save window size)."),
  estimate_max_usd  NUMERIC            OPTIONS(description="List-price worst case plus buffer."),
  estimate_avg_usd  NUMERIC            OPTIONS(description="Observed average estimate (or list price when no data)."),
  balance_at_start  NUMERIC            OPTIONS(description="DataForSEO balance read by the pre-run check."),
  balance_override  BOOL               OPTIONS(description="True when ignore_balance_check=True."),
  error             STRING             OPTIONS(description="Error repr for failed/stopped/rejected runs."),
  client_id         STRING             OPTIONS(description="Client id when supplied."),
  ingest_timestamp  TIMESTAMP NOT NULL OPTIONS(description="When the row was written.")
)
PARTITION BY DATE(ingest_timestamp)
CLUSTER BY job_id
OPTIONS(description="Append-only start/end rows per DataForSEO run. Feeds job_progress.")
""".strip()

COST_ESTIMATES_DDL = f"""
CREATE TABLE IF NOT EXISTS `{T}.cost_estimates` (
  endpoint               STRING  NOT NULL OPTIONS(description="Endpoint key."),
  endpoint_mode          STRING  NOT NULL OPTIONS(description="live | standard."),
  price_per_request_usd  FLOAT64          OPTIONS(description="List price per request/task."),
  price_per_item_usd     FLOAT64          OPTIONS(description="List price per item_unit."),
  item_unit              STRING           OPTIONS(description="result_row | keyword_sent | serp_page | none."),
  max_items_per_request  INT64            OPTIONS(description="DataForSEO per-request item cap, when there is one."),
  source_url             STRING           OPTIONS(description="DataForSEO pricing page the rate came from."),
  verified_on            STRING           OPTIONS(description="Date the rate was checked against the pricing page."),
  max_buffer             FLOAT64          OPTIONS(description="Fraction added to list price for the max estimate (0.10 = 10%)."),
  notes                  STRING           OPTIONS(description="Pricing notes.")
)
OPTIONS(description="Hand-maintained DataForSEO rate card. Seed/refresh with scripts/seed_dfs_cost_estimates.py.")
""".strip()

LOCATIONS_DDL = f"""
CREATE TABLE IF NOT EXISTS `{T}.locations` (
  location_code         INT64     NOT NULL OPTIONS(description="DataForSEO location code (one shared system across lists)."),
  location_name         STRING             OPTIONS(description="Location name."),
  location_code_parent  INT64              OPTIONS(description="Parent location code."),
  country_iso_code      STRING             OPTIONS(description="ISO country code."),
  location_type         STRING             OPTIONS(description="Country, State, DMA Region, City, County, Postal Code, ..."),
  in_serp               BOOL               OPTIONS(description="Accepted by SERP endpoints."),
  in_google_ads         BOOL               OPTIONS(description="Accepted by Google Ads (search volume) endpoints."),
  in_labs               BOOL               OPTIONS(description="Accepted by DataForSEO Labs endpoints."),
  available_languages   STRING             OPTIONS(description="Labs only: JSON text of supported languages."),
  refreshed_at          TIMESTAMP          OPTIONS(description="When refresh_locations() last replaced the table.")
)
CLUSTER BY location_code
OPTIONS(description="Cached DataForSEO location catalog. Replace with scripts/refresh_dfs_locations.py.")
""".strip()

JOB_COSTS_VIEW = f"""
CREATE OR REPLACE VIEW `{T}.job_costs` AS
SELECT
  job_id, endpoint, endpoint_mode, upload_id,
  ROUND(SUM(cost_usd), 6) AS total_cost_usd,
  COUNT(*) AS billed_tasks,
  COUNTIF(result_rows > 0) AS tasks_with_rows,
  SUM(result_rows) AS result_rows,
  MIN(requested_at) AS first_request_at,
  MAX(requested_at) AS last_request_at
FROM `{T}.cost_log`
GROUP BY job_id, endpoint, endpoint_mode, upload_id
""".strip()

JOB_PROGRESS_VIEW = f"""
CREATE OR REPLACE VIEW `{T}.job_progress` AS
WITH runs AS (
  SELECT
    job_id, endpoint, endpoint_mode,
    SUM(IF(event = 'start', planned_requests, 0)) AS planned_requests,
    SUM(IF(event = 'start', estimate_max_usd, 0)) AS estimate_max_usd,
    COUNTIF(event = 'start') AS runs_started,
    COUNTIF(event = 'end' AND status NOT LIKE 'rejected%') AS runs_ended,
    COUNTIF(event = 'end' AND status = 'failed') AS runs_failed,
    COUNTIF(event = 'end' AND status = 'stopped_low_balance') AS runs_stopped_low_balance
  FROM `{T}.job_runs`
  GROUP BY job_id, endpoint, endpoint_mode
),
done AS (
  SELECT job_id, endpoint, endpoint_mode,
         COUNT(*) AS completed_tasks, ROUND(SUM(cost_usd), 6) AS cost_so_far_usd
  FROM `{T}.cost_log`
  GROUP BY job_id, endpoint, endpoint_mode
),
standard AS (
  SELECT job_id, endpoint,
         SAFE_DIVIDE(IFNULL(fetched_count, 0) + IFNULL(failed_count, 0), total_tasks) AS collector_pct
  FROM `{T}.dfs_job_summary`
)
SELECT
  r.job_id, r.endpoint, r.endpoint_mode,
  CASE
    WHEN r.runs_started = 0 THEN 'rejected'
    WHEN r.runs_ended < r.runs_started THEN 'running'
    WHEN r.runs_failed > 0 THEN 'failed'
    WHEN r.runs_stopped_low_balance > 0 THEN 'stopped_low_balance'
    ELSE 'completed'
  END AS status,
  r.planned_requests,
  IFNULL(d.completed_tasks, 0) AS completed_tasks,
  CASE
    WHEN r.endpoint_mode = 'standard' AND s.collector_pct IS NOT NULL THEN LEAST(1.0, s.collector_pct)
    WHEN r.runs_started > 0 AND r.runs_ended >= r.runs_started THEN 1.0
    ELSE LEAST(0.99, IFNULL(SAFE_DIVIDE(d.completed_tasks, r.planned_requests), 0))
  END AS pct_complete,
  IFNULL(d.cost_so_far_usd, 0) AS cost_so_far_usd,
  r.estimate_max_usd
FROM runs r
LEFT JOIN done d USING (job_id, endpoint, endpoint_mode)
LEFT JOIN standard s ON s.job_id = r.job_id AND s.endpoint = r.endpoint
""".strip()

ENDPOINT_COST_ACTUALS_VIEW = f"""
CREATE OR REPLACE VIEW `{T}.endpoint_cost_actuals` AS
SELECT
  endpoint, endpoint_mode,
  COUNT(*) AS sample_requests,
  AVG(cost_usd) AS avg_cost_per_request,
  SAFE_DIVIDE(SUM(cost_usd), NULLIF(SUM(result_rows), 0)) AS avg_cost_per_row,
  AVG(result_rows) AS avg_rows_per_request
FROM `{T}.cost_log`
WHERE ingest_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
  AND cost_usd > 0
GROUP BY endpoint, endpoint_mode
""".strip()

_LOCATION_FILTERS = {
    "locations_countries": "location_type = 'Country'",
    "locations_us_states": "country_iso_code = 'US' AND location_type = 'State'",
    "locations_us_dma": "country_iso_code = 'US' AND location_type = 'DMA Region'",
    "locations_us_cities": "country_iso_code = 'US' AND location_type = 'City'",
    "locations_us_counties": "country_iso_code = 'US' AND location_type = 'County'",
    "locations_us_postal_codes": "country_iso_code = 'US' AND location_type = 'Postal Code'",
}
LOCATION_VIEWS = {
    name: f"CREATE OR REPLACE VIEW `{T}.{name}` AS\nSELECT * FROM `{T}.locations`\nWHERE {where}"
    for name, where in _LOCATION_FILTERS.items()
}

ALL_STATEMENTS: list[tuple[str, str]] = [
    ("cost_log", COST_LOG_DDL),
    ("job_runs", JOB_RUNS_DDL),
    ("cost_estimates", COST_ESTIMATES_DDL),
    ("locations", LOCATIONS_DDL),
    ("job_costs", JOB_COSTS_VIEW),
    ("job_progress", JOB_PROGRESS_VIEW),
    ("endpoint_cost_actuals", ENDPOINT_COST_ACTUALS_VIEW),
    *LOCATION_VIEWS.items(),
]


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
        for name, sql in ALL_STATEMENTS:
            click.echo(f"-- {name}\n{sql}\n")
        click.echo("[dry-run] Re-run with --yes to execute.")
        return
    bq = _make_bq(project)
    for name, sql in ALL_STATEMENTS:
        click.echo(f"[exec] {DATASET}.{name} ...")
        bq.client.query(sql).result()
    click.echo("[exec] Done.")


if __name__ == "__main__":
    cli()
