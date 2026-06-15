"""Producer-side helpers for the standard-mode collector path.

The producer `task_post`s, records tracking rows, then waits cheaply on BigQuery
(`dfs_job_summary`) instead of polling DFS in-process. Two endpoint-agnostic pieces:

- `parse_task_post()` — turn a raw DFS task_post response into posted task_ids, and
  detect per-task 40200 (Payment Required) rejections at submit time (bug flaw #5).
- `submit_and_wait()` — write the tracking rows via TrackingStore, poll completion until
  `proceed_at_pct`, and return a small receipt. No DataFrame; data is read from BQ later.
"""

from __future__ import annotations

import time
from typing import Callable

from skyward.data.dataforseo.collector.tracking import TrackingStore

# DFS task_post per-task status codes
_TASK_POST_OK = 20100
_PAYMENT_REQUIRED = 40200


def parse_task_post(response: dict | None) -> dict:
    """Extract posted tasks and rejection counts from a raw DFS task_post response.

    Returns {"posted": [{"task_id", "keyword"}], "rejected_payment": int, "rejected_other": int}.
    A 40200 (Payment Required, usually a negative balance) is counted separately so the
    caller can surface it loudly at submit time rather than silently posting dead task_ids.
    """
    posted: list[dict] = []
    rejected_payment = 0
    rejected_other = 0
    for task in (response or {}).get("tasks") or []:
        code = task.get("status_code")
        task_id = task.get("id")
        tag = (task.get("data") or {}).get("tag", "")
        if code == _TASK_POST_OK and task_id:
            posted.append({"task_id": task_id, "keyword": tag})
        elif code == _PAYMENT_REQUIRED:
            rejected_payment += 1
        else:
            rejected_other += 1
    return {
        "posted": posted,
        "rejected_payment": rejected_payment,
        "rejected_other": rejected_other,
    }


def submit_and_wait(
    *,
    bq_client,
    job_id: str,
    endpoint_key: str,
    posted_tasks: list[dict],
    proceed_at_pct: float = 1.0,
    poll_interval: float = 5.0,
    max_wait: float = 18000.0,
    store: "TrackingStore | None" = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> dict:
    """Record tracking rows for a posted batch, then wait on BQ until `proceed_at_pct`.

    Returns a receipt: {job_id, endpoint, total_tasks, completion_pct, proceeded}.
    `proceeded` is False if `max_wait` elapsed before the threshold was reached (the data
    keeps landing in the canonical table regardless — the collector owns the tail).
    """
    store = store or TrackingStore(bq_client)
    store.submit(job_id=job_id, endpoint=endpoint_key, tasks=posted_tasks)

    deadline = now() + max_wait
    pct = store.completion_pct(job_id=job_id, endpoint=endpoint_key)
    while pct < proceed_at_pct and now() < deadline:
        sleep(poll_interval)
        pct = store.completion_pct(job_id=job_id, endpoint=endpoint_key)

    return {
        "job_id": job_id,
        "endpoint": endpoint_key,
        "total_tasks": len(posted_tasks),
        "completion_pct": pct,
        "proceeded": pct >= proceed_at_pct,
    }
