"""The always-on standard-mode collector.

Runs forever (intended substrate: a small Compute Engine VM as a systemd service —
ClickUp 86bac9q9y). Each cycle, for every allowlisted endpoint:
  tasks_ready -> task_get each ready id -> parse -> stamp metadata -> append to the
  canonical table -> batch-update dfs_task_log / dfs_job_summary via TrackingStore.

Run locally:  python -m skyward.data.dataforseo.collector.service
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from skyward.data.dataforseo.collector.tracking import (
    DATASET, SENTINEL_JOB_ID, TrackingStore, run_with_retry,
)
from skyward.functions import generate_upload_id

_NOT_FOUND = 40401  # DFS task_get: Task Not Found (e.g. a task that was never queued)
_READY_PAGE_CAP = 1000  # DFS tasks_ready returns at most this many ids per call; a full page => more backlog


class CollectorDfsError(Exception):
    """DataForSEO was unreachable for an endpoint's tasks_ready (transport failure)."""

    def __init__(self, endpoint: str):
        super().__init__(f"DataForSEO unreachable for {endpoint}")
        self.endpoint = endpoint


def fetch_ready_task_ids(client, ready_url: str) -> list[str] | None:
    """GET tasks_ready for one endpoint -> list of ready task_ids (DFS shape is uniform).

    Returns None on a transport failure (client._get exhausted its retries and got nothing) —
    distinct from [] (reached DFS, nothing ready) so the caller can alert on unreachability.
    """
    resp = client._get(f"{client.BASE_URL}/{ready_url}")
    if resp is None:
        return None
    ids: list[str] = []
    for task in resp.get("tasks") or []:
        for entry in task.get("result") or []:
            tid = entry.get("id")
            if tid:
                ids.append(tid)
    return ids


def _task_status_code(raw) -> int | None:
    if not isinstance(raw, dict):
        return None
    return ((raw.get("tasks") or [{}])[0] or {}).get("status_code")


def run_cycle(client, store: TrackingStore, handler, *, bq_client=None, alerter=None,
              pending=None, grace_s=600.0, now=time.monotonic, flush_every=100,
              fetch_workers=6) -> dict:
    """Drain one endpoint once. Returns {endpoint, ready, skipped, deferred, fetched, failed}.

    `pending` (task_id -> first-seen monotonic time) carries the unattributed grace state
    across cycles; pass the same dict each call (run_forever keeps one per endpoint).
    `grace_s` is how long a ready task with no dfs_task_log row is deferred before it's
    treated as a genuine orphan and attributed to the sentinel — this absorbs the producer
    race (DFS can mark a task ready before the producer's task_log write lands).
    `flush_every` bounds memory: canonical rows are written and marked in batches of this many
    tasks rather than accumulating the whole cycle and concatenating once at the end (that
    end-of-cycle peak is what OOM-killed the collector on the 2 GB VM). Smaller batches also
    persist progress incrementally, so a crash/restart mid-cycle keeps what was already drained.
    `fetch_workers` is how many task_get calls run concurrently — the throughput lever. task_get
    is a read and the client is thread-safe (its live_all path fans out the same way), so this
    turns the serial fetch (the bottleneck) into a parallel one. It's bounded by the flush batch
    so memory stays capped, and kept well under the DFS account rate limit so live pulls aren't
    starved.
    """
    bq_client = bq_client or client.bq_client
    pending = pending if pending is not None else {}
    ready = fetch_ready_task_ids(client, handler.ready_url)
    if ready is None:
        raise CollectorDfsError(handler.key)
    if not ready:
        pending.clear()  # nothing ready -> no outstanding grace state to hold
        return {"endpoint": handler.key, "ready": 0, "skipped": 0, "deferred": 0,
                "fetched": 0, "failed": 0}

    lookup = store.lookup_tasks(endpoint=handler.key, task_ids=ready)
    ready_set = set(ready)
    # Drop grace state for tasks that are no longer ready (collected/expired) so it can't grow.
    for k in [k for k in pending if k not in ready_set]:
        del pending[k]

    frames: list[pd.DataFrame] = []       # canonical rows buffered for the next flush
    results: list[dict] = []              # task-log updates buffered for the next flush
    batch_jobs: set[str] = set()          # jobs touched since the last flush
    fetched = failed = skipped = deferred = 0

    def _flush() -> None:
        # Incremental commit of one batch (<= flush_every tasks). Order is load-bearing and must
        # stay: canonical rows are durably appended BEFORE mark_fetched, so a crash between the
        # two leaves those tasks 'pending' (data already written; re-marking on restart is
        # idempotent) rather than 'fetched' with no data. Doing this per-batch instead of once
        # at cycle end caps the concat/serialize memory peak and persists progress mid-cycle.
        if frames:
            _append_canonical(bq_client, handler.canonical_table, pd.concat(frames, ignore_index=True))
        if results:
            # Pass copies — the buffers are cleared below for the next batch, and callers
            # (and the real store) must not see them mutated out from under them.
            store.mark_fetched(endpoint=handler.key, results=list(results), job_ids=list(batch_jobs))
        frames.clear()
        results.clear()
        batch_jobs.clear()

    # Phase 1 — classify every ready task (serial, no I/O). This reads the clock and mutates the
    # grace `pending` state, so it must stay single-threaded; it only decides skip/defer/fetch.
    to_fetch: list[tuple[str, dict]] = []
    for tid in ready:
        info = lookup.get(tid)
        # Already fetched/failed in a prior cycle: DFS keeps re-listing it in tasks_ready
        # (eventual-consistency lag) and task_get is repeatable, so re-fetching here would
        # re-append duplicate rows. Skip it — this is the primary double-write guard.
        if info and info.get("resolved"):
            skipped += 1
            pending.pop(tid, None)
            continue
        if info is None:
            # No dfs_task_log row (yet). Could be the producer-write race (its task_log write
            # hasn't landed) or a genuine orphan. Defer within the grace window; only after it
            # stays unattributable past grace_s do we bucket it to the sentinel.
            first = pending.get(tid)
            if first is None:
                first = pending[tid] = now()
            if (now() - first) < grace_s:
                deferred += 1
                continue
            pending.pop(tid, None)
            info = {"job_id": SENTINEL_JOB_ID, "keyword": None, "domain_id": None, "domain": None}
        else:
            pending.pop(tid, None)  # became attributable — clear any prior defer
        to_fetch.append((tid, info))

    def _fetch_one(item: tuple[str, dict]) -> dict:
        # Runs in a worker thread: one task_get + parse. Pure per-task work (no shared mutable
        # state), so it's safe to run `fetch_workers` at a time.
        tid, info = item
        raw = client._get(f"{client.BASE_URL}/{handler.get_url}/{tid}")
        status_code = _task_status_code(raw)
        df = None
        if raw is None:
            status, n = "failed_other", 0
        elif status_code == _NOT_FOUND:
            status, n = "failed_not_found", 0
        else:
            try:
                parsed = handler.parse(raw, info["keyword"])
                if parsed is not None and not parsed.empty:
                    parsed = handler.cast(parsed)
            except Exception:
                parsed = None
            n = 0 if (parsed is None or parsed.empty) else len(parsed)
            status = "fetched"  # a valid response with 0 rows is still "fetched"
            if n:
                parsed = parsed.copy()
                parsed["job_id"] = info["job_id"]
                parsed["domain_id"] = info["domain_id"]
                parsed["domain"] = info["domain"]
                parsed["endpoint_mode"] = "standard"
                df = parsed
        return {"tid": tid, "info": info, "status": status,
                "status_code": status_code, "n": n, "df": df}

    # Phase 2 — fetch concurrently, one flush-sized chunk at a time. Chunking by `flush_every`
    # keeps at most that many parsed frames resident before the batch is written, so the memory
    # bound holds even though fetches now overlap. `ex.map` preserves order and the per-task
    # accumulation below is single-threaded, so attribution/counting stay deterministic.
    with ThreadPoolExecutor(max_workers=max(1, fetch_workers)) as ex:
        for i in range(0, len(to_fetch), flush_every):
            for r in ex.map(_fetch_one, to_fetch[i:i + flush_every]):
                batch_jobs.add(r["info"]["job_id"])
                if r["df"] is not None:
                    frames.append(r["df"])
                if r["status"] == "fetched":
                    fetched += 1
                else:
                    failed += 1
                results.append({
                    "task_id": r["tid"], "status": r["status"],
                    "dfs_status_code": r["status_code"], "result_rows": r["n"], "attempts": 1,
                })
            _flush()  # append canonical then mark_fetched for this chunk (order preserved)

    # On each newly-completed job: (1) dedup-DELETE backstop — clears any crash-path duplicate
    # rows (append succeeded, mark_fetched didn't, task re-fetched on restart) keyed on task_id
    # within the last 2 days; (2) per-job 'done' alert (once, via notified_at). Dedup runs even
    # without an alerter so the canonical table is always clean.
    for job in store.claim_completed_jobs(endpoint=handler.key):
        _dedup_canonical(bq_client, handler.canonical_table, job["job_id"])
        if alerter is not None:
            alerter.job_complete(job_id=job["job_id"], endpoint=handler.key,
                                 succeeded=job["succeeded"], failed=job["failed"])

    return {"endpoint": handler.key, "ready": len(ready), "skipped": skipped,
            "deferred": deferred, "fetched": fetched, "failed": failed}


def _append_canonical(bq_client, table_name: str, df: pd.DataFrame) -> None:
    from google.cloud import bigquery

    df = df.copy()
    df["upload_id"] = generate_upload_id()
    df["ingest_timestamp"] = pd.Timestamp.now("UTC")
    if "domain_id" in df.columns:
        df["domain_id"] = pd.to_numeric(df["domain_id"], errors="coerce").astype("Int64")
    full_table_id = f"{bq_client.client.project}.{DATASET}.{table_name}"
    cfg = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_APPEND)
    run_with_retry(
        lambda: bq_client.client.load_table_from_dataframe(df, full_table_id, job_config=cfg).result(),
        label=f"canonical_load:{table_name}",
    )


def _dedup_canonical(bq_client, table_name: str, job_id: str) -> None:
    """Backstop: drop duplicate canonical rows for one job, keeping the latest upload per task.

    A crash between the canonical write and `mark_fetched` can leave a task fetched-but-pending,
    so it's re-fetched and re-appended on restart. This DELETE removes, for the given job, any
    row whose `task_id` has a newer `upload_id` — i.e. keep only the most recent write per task.

    Cheap and bounded: filtered to `job_id` (cluster key) within the last 2 days
    (`ingest_timestamp` is the DAY partition key), so it prunes to a couple of partitions and
    that job's blocks. Scoped to one job_id, so live-mode rows (other job_ids) are never touched.
    """
    from google.cloud import bigquery

    full_table_id = f"{bq_client.client.project}.{DATASET}.{table_name}"
    sql = f"""
    DELETE FROM `{full_table_id}` T
    WHERE T.job_id = @job_id
      AND T.ingest_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 DAY)
      AND EXISTS (
        SELECT 1 FROM `{full_table_id}` U
        WHERE U.task_id = T.task_id
          AND U.job_id = T.job_id
          AND U.ingest_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 DAY)
          AND (U.ingest_timestamp > T.ingest_timestamp
               OR (U.ingest_timestamp = T.ingest_timestamp AND U.upload_id > T.upload_id))
      )
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
    ])
    run_with_retry(
        lambda: bq_client.client.query(sql, job_config=cfg).result(),
        label=f"dedup_canonical:{table_name}",
    )


def run_forever(client, store, handlers, *, alerter=None, poll_interval=30,
                max_cycles=None, sleep=time.sleep, should_stop=None, grace_s=600.0,
                flush_every=100, fetch_workers=6) -> None:
    """Loop over all allowlisted endpoints each cycle, alerting on failures.

    One endpoint erroring never kills the loop. Per endpoint, failures alert (deduped) and a
    later success resolves them. A heartbeat line is emitted once per loop. `should_stop()`
    (set by the SIGTERM handler) breaks the loop for a graceful shutdown.

    When any endpoint returns a full tasks_ready page (`_READY_PAGE_CAP`), there is more backlog
    waiting at DFS, so the inter-cycle `poll_interval` idle is skipped and the next cycle runs
    immediately — draining a large backlog at fetch speed instead of one page per poll_interval.
    """
    from skyward.data.dataforseo.collector.alerts import Alerter, classify_failure
    alerter = alerter or Alerter()

    # Per-endpoint grace state (task_id -> first-seen time) for unattributed tasks, kept across
    # cycles so the producer-write race is absorbed rather than mis-bucketed to the sentinel.
    pending: dict[str, dict] = {h.key: {} for h in handlers.values()}

    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        if should_stop is not None and should_stop():
            break
        cycle += 1
        max_ready = 0  # largest tasks_ready page this loop -> whether there's backlog to chase
        for handler in handlers.values():
            key = handler.key
            try:
                stats = run_cycle(client, store, handler, alerter=alerter,
                                  pending=pending[key], grace_s=grace_s, flush_every=flush_every,
                                  fetch_workers=fetch_workers)
            except CollectorDfsError:
                alerter.fire(f"dfs:{key}", title="DataForSEO Unreachable", fields={"Endpoint": key})
                continue
            except Exception as e:  # noqa: BLE001 - never let one endpoint kill the loop
                suffix, label = classify_failure(e)
                alerter.fire(f"{suffix}:{key}", title=label,
                             fields={"Endpoint": key, "Error": repr(e)})
                print(f"[collector] cycle {cycle} {key} ERROR: {e!r}")
                continue

            # success — clear any prior failure alerts for this endpoint
            for ek, recovered_title in (("dfs", "DataForSEO Reachable Again"),
                                        ("bigquery", "BigQuery Recovered"),
                                        ("error", "Cycle Recovered")):
                alerter.resolve(f"{ek}:{key}", title=recovered_title, fields={"Endpoint": key})
            # Fail rate is over tasks actually processed this cycle (fetched + failed), not
            # `ready` — `ready` now counts skipped (already-resolved, DFS-lingering) tasks too.
            processed = stats["fetched"] + stats["failed"]
            if processed and stats["failed"] / processed >= 0.5:
                alerter.fire(f"failrate:{key}", title="High Fetch-Failure Rate",
                             fields={"Endpoint": key, "Failed": f"{stats['failed']}/{processed}"})
            else:
                alerter.resolve(f"failrate:{key}", title="Fetch-Failure Rate Recovered",
                                fields={"Endpoint": key})
            max_ready = max(max_ready, stats["ready"])
            if stats["ready"]:
                print(f"[collector] cycle {cycle} {stats}")

        alerter.heartbeat()
        # A full page means more is waiting -> poll again immediately; only idle when caught up.
        backlog = max_ready >= _READY_PAGE_CAP
        if not backlog and (max_cycles is None or cycle < max_cycles):
            # Interruptible sleep: check should_stop ~every second so a SIGTERM (systemd
            # stop/reboot) breaks promptly and the shutdown alert fires right away, instead
            # of waiting out the full poll interval.
            slept = 0.0
            while slept < poll_interval:
                if should_stop is not None and should_stop():
                    return
                step = min(1.0, poll_interval - slept)
                sleep(step)
                slept += step


def main() -> None:  # pragma: no cover - thin wiring, exercised at deploy
    import signal

    from skyward.config import load_config
    from skyward.data.bigquery import BigQueryClient
    from skyward.data.dataforseo import ClientConfig, DataForSEOClient
    from skyward.data.dataforseo.collector.alerts import Alerter
    from skyward.data.dataforseo.collector.allowlist import build_allowlist

    cfg = load_config()
    bq = BigQueryClient(project_id=cfg.datahub_project_id)
    client = DataForSEOClient(
        username=cfg.dataforseo_username, password=cfg.dataforseo_password,
        bq_client=bq, config=ClientConfig(),
    )
    store = TrackingStore(bq)
    handlers = build_allowlist(client)
    alerter = Alerter()

    stop = {"flag": False}

    def _handle_stop(signum, _frame):
        stop["flag"] = True
        print(f"[collector] received signal {signum}; stopping after current cycle")

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    import os
    grace_s = float(os.environ.get("DFS_COLLECTOR_GRACE_S", 600))
    # Tasks committed per batch. Each held task keeps ~1.6 MB of arrow-backed frame memory,
    # so a whole 1000-task cycle in memory OOM-kills the 2 GB VM; 100 caps the peak at ~160 MB.
    # Env-tunable so it can be retuned without a code redeploy.
    flush_every = int(os.environ.get("DFS_COLLECTOR_FLUSH_EVERY", 100))
    # Concurrent task_get calls. Default 6 keeps throughput well under the DFS account rate
    # limit (~2000 calls/min, shared with live pulls) while being ~8x the old serial fetch.
    fetch_workers = int(os.environ.get("DFS_COLLECTOR_FETCH_WORKERS", 6))
    print(f"[collector] starting; endpoints={list(handlers)} "
          f"poll_interval={client.config.task_poll_interval}s grace_s={grace_s} "
          f"flush_every={flush_every} fetch_workers={fetch_workers}")
    alerter.startup()
    try:
        run_forever(client, store, handlers, alerter=alerter,
                    poll_interval=client.config.task_poll_interval,
                    should_stop=lambda: stop["flag"], grace_s=grace_s, flush_every=flush_every,
                    fetch_workers=fetch_workers)
    except Exception as e:  # pragma: no cover - top-level safety net
        alerter.crash(f"collector crashed: {e!r}")
        raise
    finally:
        alerter.shutdown("signal" if stop["flag"] else "exit")


if __name__ == "__main__":  # pragma: no cover
    main()
