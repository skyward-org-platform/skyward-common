"""The always-on standard-mode collector.

Runs forever (intended substrate: a small Compute Engine VM as a systemd service —
ClickUp 86bac9q9y). Each cycle, for every allowlisted endpoint:
  tasks_ready -> task_get each ready id -> parse -> stamp metadata -> append to the
  canonical table -> batch-update dfs_task_log / dfs_job_summary via TrackingStore.

Run locally:  python -m skyward.data.dataforseo.collector.service
"""

from __future__ import annotations

import time

import pandas as pd

from skyward.data.dataforseo.collector.tracking import (
    DATASET, SENTINEL_JOB_ID, TrackingStore, run_with_retry,
)
from skyward.functions import generate_upload_id

_NOT_FOUND = 40401  # DFS task_get: Task Not Found (e.g. a task that was never queued)


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


def run_cycle(client, store: TrackingStore, handler, *, bq_client=None) -> dict:
    """Drain one endpoint once. Returns {endpoint, ready, fetched, failed}."""
    bq_client = bq_client or client.bq_client
    ready = fetch_ready_task_ids(client, handler.ready_url)
    if ready is None:
        raise CollectorDfsError(handler.key)
    if not ready:
        return {"endpoint": handler.key, "ready": 0, "fetched": 0, "failed": 0}

    lookup = store.lookup_tasks(endpoint=handler.key, task_ids=ready)

    frames: list[pd.DataFrame] = []
    results: list[dict] = []
    fetched = failed = 0

    for tid in ready:
        info = lookup.get(tid) or {
            "job_id": SENTINEL_JOB_ID, "keyword": None, "domain_id": None, "domain": None,
        }
        raw = client._get(f"{client.BASE_URL}/{handler.get_url}/{tid}")
        status_code = _task_status_code(raw)

        if raw is None:
            status, n = "failed_other", 0
        elif status_code == _NOT_FOUND:
            status, n = "failed_not_found", 0
        else:
            try:
                df = handler.parse(raw, info["keyword"])
                if df is not None and not df.empty:
                    df = handler.cast(df)
            except Exception:
                df = None
            n = 0 if (df is None or df.empty) else len(df)
            status = "fetched"  # a valid response with 0 rows is still "fetched"
            if n:
                df = df.copy()
                df["job_id"] = info["job_id"]
                df["domain_id"] = info["domain_id"]
                df["domain"] = info["domain"]
                df["endpoint_mode"] = "standard"
                frames.append(df)

        if status == "fetched":
            fetched += 1
        else:
            failed += 1
        results.append({
            "task_id": tid, "status": status, "dfs_status_code": status_code,
            "result_rows": n, "attempts": 1,
        })

    if frames:
        _append_canonical(bq_client, handler.canonical_table, pd.concat(frames, ignore_index=True))

    # Mark statuses only after the canonical rows are durably written, so a crash before
    # this leaves the tasks 'pending' (the data is already in the canonical table; re-marking
    # is idempotent) rather than 'fetched' with no data.
    store.mark_fetched(endpoint=handler.key, results=results)
    return {"endpoint": handler.key, "ready": len(ready), "fetched": fetched, "failed": failed}


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


def run_forever(client, store, handlers, *, alerter=None, poll_interval=30,
                max_cycles=None, sleep=time.sleep, should_stop=None) -> None:
    """Loop over all allowlisted endpoints each cycle, alerting on failures.

    One endpoint erroring never kills the loop. Per endpoint, failures alert (deduped) and a
    later success resolves them. A heartbeat line is emitted once per loop. `should_stop()`
    (set by the SIGTERM handler) breaks the loop for a graceful shutdown.
    """
    from skyward.data.dataforseo.collector.alerts import Alerter, classify_failure
    alerter = alerter or Alerter()

    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        if should_stop is not None and should_stop():
            break
        cycle += 1
        for handler in handlers.values():
            key = handler.key
            try:
                stats = run_cycle(client, store, handler)
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
            if stats["ready"] and stats["failed"] / stats["ready"] >= 0.5:
                alerter.fire(f"failrate:{key}", title="High Fetch-Failure Rate",
                             fields={"Endpoint": key, "Failed": f"{stats['failed']}/{stats['ready']}"})
            else:
                alerter.resolve(f"failrate:{key}", title="Fetch-Failure Rate Recovered",
                                fields={"Endpoint": key})
            if stats["ready"]:
                print(f"[collector] cycle {cycle} {stats}")

        alerter.heartbeat()
        if max_cycles is None or cycle < max_cycles:
            sleep(poll_interval)


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

    print(f"[collector] starting; endpoints={list(handlers)} "
          f"poll_interval={client.config.task_poll_interval}s")
    alerter.startup()
    try:
        run_forever(client, store, handlers, alerter=alerter,
                    poll_interval=client.config.task_poll_interval,
                    should_stop=lambda: stop["flag"])
    except Exception as e:  # pragma: no cover - top-level safety net
        alerter.crash(f"collector crashed: {e!r}")
        raise
    finally:
        alerter.shutdown("signal" if stop["flag"] else "exit")


if __name__ == "__main__":  # pragma: no cover
    main()
