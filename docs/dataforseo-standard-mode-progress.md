# Checking progress for DataForSEO standard-mode "cloud pull" runs

When you submit a standard-mode pull with the collector enabled (`use_collector=True`), the call **returns immediately** — it posts the tasks to DataForSEO, records them in BigQuery, and exits. An always-on **collector service** then drains the results from DataForSEO into the canonical BigQuery tables in the background. This guide shows how to track a run's progress and know when it's done.

> Applies to the collector-mode endpoints: `serp_google_organic` and `keywords_data_google_ads_search_volume`. (Legacy in-process `post_all` — `use_collector=False`, the default — blocks and returns a DataFrame instead; this guide is for collector mode.)

## TL;DR

Poll **one row** in `data-hub-468216.DataForSEO.dfs_job_summary`, keyed by your `job_id` + `endpoint`:

```sql
SELECT fetched_count, total_tasks, failed_count, status
FROM `data-hub-468216.DataForSEO.dfs_job_summary`
WHERE job_id = @job_id AND endpoint = 'serp_google_organic'
```

`progress = fetched_count / total_tasks`; `status = 'done'` means the run is complete.

Or in Python (no SQL):

```python
from skyward.data.dataforseo.collector.tracking import TrackingStore

store = TrackingStore(bq_client)
pct = store.completion_pct(job_id=job_id, endpoint="serp_google_organic")  # 0.0–1.0
```

## The progress source: `dfs_job_summary`

One row per `(job_id, endpoint)`, written at submit and updated by the collector each cycle:

| Column | Meaning |
|---|---|
| `total_tasks` | tasks posted for this run |
| `fetched_count` | tasks the collector has results for |
| `failed_count` | tasks that resolved to a failure (not-found / other) |
| `status` | `pending` → `partial` → `done` |
| `submitted_at` / `last_updated` | timestamps (collector touches `last_updated` each cycle it makes progress) |

`status` meanings:
- `pending` — submitted, nothing fetched yet.
- `partial` — some tasks fetched, run still in progress.
- `done` — `fetched_count + failed_count >= total_tasks`. Complete.

### Python helpers

```python
store = TrackingStore(bq_client)

# Single fraction (this is exactly what `proceed_at_pct` waits on):
pct = store.completion_pct(job_id=job_id, endpoint="serp_google_organic")  # 0.0–1.0

# Per-endpoint detail for a job:
rows = store.job_status(job_id=job_id)
# -> [{"endpoint": "serp_google_organic", "total_tasks": 4191, "fetched": 1787,
#      "failed": 0, "status": "partial", "completion_pct": 0.426, ...}]
```

A minimal poll-to-done loop:

```python
import time

while True:
    s = store.job_status(job_id=job_id, endpoint="serp_google_organic")[0]
    print(f"{s['fetched']}/{s['total_tasks']} ({s['completion_pct']:.0%})  "
          f"failed={s['failed']}  {s['status']}")
    if s["status"] == "done":
        break
    time.sleep(15)
```

## ⚠️ Progress moves in steps, not smoothly

The collector drains tasks in **per-cycle batches**: each cycle pulls every task DataForSEO currently has ready, fetches them, then writes the batch and updates `fetched_count` **once, at the end of the cycle**. Practical consequences:

- For the **first minute or two** after submit, `fetched_count` is legitimately **0** — the first cycle is still in flight. **Don't treat an early 0% as a stall.**
- Progress then **jumps in chunks** (e.g. `0 → 43% → 78% → 100%`), not a smooth crawl — a large batch's cycle can take a few minutes.
- There is no "smoother" source: the canonical table and `dfs_task_log` are written at the same point in the cycle, so they tick in the same chunks. Poll on a 10–15s interval and expect step changes.

(Second-by-second progress is a future enhancement — parallel `task_get` + smaller flush batches are planned.)

## Reading the actual results

Results land in the canonical table for the endpoint, tagged with your `job_id` and `endpoint_mode = 'standard'`:

```sql
-- serp:          DataForSEO.serp-google-organic
-- search_volume: DataForSEO.keywords_data-google_ads-search_volume
SELECT * FROM `data-hub-468216.DataForSEO.serp-google-organic`
WHERE job_id = @job_id AND endpoint_mode = 'standard'
```

Or via DataHub (resolves the client's domains from Supabase Meta):

```python
df = hub.get_client_data(client_id="123", dataset="DataForSEO",
                         table="serp-google-organic", use_domain_lookup=True)
```

Stragglers keep landing **after** `proceed_at_pct` returns — the collector owns the tail, so a later read picks them up.

## Troubleshooting

- **Progress stuck at 0%** — (1) confirm you're querying the **exact** `job_id` *and* `endpoint` (a wrong/typo'd value returns no row → reads as 0); (2) remember the first-cycle lag above; (3) confirm the run actually posted (`total_tasks > 0`; check `rejected_payment` / `rejected_other` in the submit receipt) and that the collector service is alive.
- **No summary row at all** — the producer never wrote it; confirm the submit call succeeded and the tracking tables exist (`scripts/create_dfs_collector_tables.py`).
- **`failed_count` climbing** — inspect per-task reasons: `SELECT status, COUNT(*) FROM DataForSEO.dfs_task_log WHERE job_id = @job_id GROUP BY status` (`failed_not_found` / `failed_other`).
- **Rows under `job_id = 'd4s_standard_unattributed'`** — tasks the collector drained with no matching tracking row (you didn't pass `domain_id`/`domain`, or a genuine orphan). Always pass `domain_id`/`domain` at submit for clean attribution.

## The two tracking tables (reference)

- **`DataForSEO.dfs_job_summary`** — the rollup you poll (one row per `job_id` + `endpoint`).
- **`DataForSEO.dfs_task_log`** — one row per task (`status`, `fetched_at`, `result_rows`, `attempts`); use it for per-task forensics, not progress.
