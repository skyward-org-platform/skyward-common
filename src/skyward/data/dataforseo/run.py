"""Run lifecycle for DataForSEO endpoint calls (v1.6.1).

Every public run method builds a RunContext. Each unit of work (one `_fetch_live` call,
one bulk batch, one task_post batch) runs inside `RunContext.run_unit`, which makes a
RunUnit the active unit for the executing thread. `DataForSEOClient._post` records billed
responses into the active unit. Linking is only by job_id, upload_id and task_id.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Awaitable, Callable

import pandas as pd

from skyward.data.dataforseo.batch_uploader import BatchUploader, choose_upload_batch_rows
from skyward.data.dataforseo.cost_log import (
    COST_LOG_TABLE, CostLogWriter, cost_flush_every, extract_cost_records,
)
from skyward.data.dataforseo.estimates import CostEstimate, RunPlan
from skyward.data.dataforseo.exceptions import InsufficientBalanceError

logger = logging.getLogger(__name__)

DATASET = "DataForSEO"
JOB_RUNS_TABLE = "job_runs"
DEFAULT_BALANCE_BUFFER = 1.2

_ACTIVE_UNIT: ContextVar["RunUnit | None"] = ContextVar("dfs_active_unit", default=None)


def active_unit() -> "RunUnit | None":
    return _ACTIVE_UNIT.get()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def target_list(target) -> list[str]:
    if isinstance(target, (list, tuple)):
        return [str(t) for t in target]
    return [str(target)]


class RunUnit:
    """Cost records for one unit of work. Used by a single thread at a time."""

    def __init__(self, target) -> None:
        self.target = target
        self.records: list[dict] = []
        self._attempts: Counter = Counter()

    def record_http(self, url: str, payload, resp, http_status) -> None:
        records = extract_cost_records(url, payload, resp, http_status)
        if not records:
            return
        key = json.dumps(payload, sort_keys=True, default=str)
        self._attempts[key] += 1
        for r in records:
            r["attempt"] = self._attempts[key]
        self.records.extend(records)


def check_balance(
    client,
    *,
    required_usd: float,
    balance_buffer: float,
    ignore: bool,
    job_id: str,
    endpoint: str,
    completed_targets=(),
    remaining_targets=(),
    upload_ids=(),
    stage: str = "pre-run",
) -> float | None:
    """Fail fast when balance < required_usd * balance_buffer. Returns the balance read."""
    if required_usd <= 0:
        return None
    info = client.get_balance_cached()
    if not info.get("raw"):
        msg = f"[{endpoint}] Could not read DataForSEO balance; skipping the {stage} balance check."
        print(msg)
        logger.warning(msg)
        return None
    balance = float(info["balance"])
    needed = round(required_usd * balance_buffer, 6)
    if balance >= needed:
        return balance
    msg = (f"[{endpoint}] DataForSEO balance ${balance:.4f} is below ${needed:.4f} "
           f"({stage} max estimate ${required_usd:.4f} x balance_buffer {balance_buffer}); "
           f"shortfall ${needed - balance:.4f}.")
    if ignore:
        warning = f"WARNING: ignore_balance_check=True, proceeding anyway. {msg}"
        print(warning)
        logger.warning(warning)
        return balance
    raise InsufficientBalanceError(
        msg, job_id=job_id, endpoint=endpoint, balance=balance, required=needed,
        upload_ids=upload_ids, completed_targets=completed_targets,
        remaining_targets=remaining_targets,
    )


def write_job_run_row(bq_client, row: dict, *, max_attempts: int = 3, sleep=time.sleep) -> bool:
    """Stream one job_runs row. Never raises; returns False when it could not write."""
    if bq_client is None:
        return False
    table = f"{bq_client.client.project}.{DATASET}.{JOB_RUNS_TABLE}"
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            errors = bq_client.client.insert_rows_json(table, [row])
            if not errors:
                return True
            last_error = errors
        except Exception as e:  # noqa: BLE001
            last_error = e
        if attempt < max_attempts:
            sleep(attempt)
    logger.warning("DFS job_runs insert failed: %r", last_error)
    return False


class RunContext:
    """One public run: cost log, save windows, balance guard, job_runs start/end rows."""

    def __init__(
        self,
        *,
        client,
        endpoint_key: str,
        job_id: str,
        plan: RunPlan,
        estimate: CostEstimate,
        endpoint_mode: str,
        upload: bool,
        write: Callable[[pd.DataFrame, str], None],
        stamp: Callable[[pd.DataFrame], pd.DataFrame],
        empty_columns: list[str],
        upload_batch_rows: int | None = None,
        balance_buffer: float = DEFAULT_BALANCE_BUFFER,
        ignore_balance_check: bool = False,
        tag_cost_with_upload: bool = True,
    ) -> None:
        self.client = client
        self.endpoint = endpoint_key
        self.job_id = job_id
        self.plan = plan
        self.estimate = estimate
        self.endpoint_mode = endpoint_mode
        self._stamp = stamp
        self._empty_columns = empty_columns
        self._balance_buffer = balance_buffer
        self._ignore_balance = ignore_balance_check
        self._tag_cost = tag_cost_with_upload and upload
        self._bq = client.bq_client
        self._lock = threading.Lock()
        self._frames: list[pd.DataFrame] = []
        self._completed: list[str] = []
        self._stop_error: InsufficientBalanceError | None = None
        self._closing = False
        self._closed = False
        self._close_done = threading.Event()
        self._result: pd.DataFrame | None = None
        self.spent_usd = 0.0

        if self._bq is None:
            print(f"[{endpoint_key}] No BigQuery client; DataForSEO cost logging is off for this run.")
            self.cost_writer = None
        else:
            self.cost_writer = CostLogWriter(
                self._bq, flush_every=cost_flush_every(plan.planned_requests))

        self.uploader = BatchUploader(
            threshold=choose_upload_batch_rows(plan.planned_max_rows, upload_batch_rows),
            write=write, before_save=self._before_save, after_save=self._after_save,
        ) if upload else None

    # ----- ids and progress -----

    @property
    def upload_ids(self) -> list[str]:
        return list(self.uploader.saved_upload_ids) if self.uploader is not None else []

    def completed_targets(self) -> list[str]:
        with self._lock:
            return list(self._completed)

    def remaining_targets(self) -> list[str]:
        done = set(self.completed_targets())
        return [t for t in self.plan.targets if t not in done]

    # ----- lifecycle -----

    def start(self, balance: float | None) -> None:
        write_job_run_row(self._bq, self._job_run_row("start", "running", balance=balance))

    def run_unit(self, target, fn: Callable[[], pd.DataFrame | None]) -> pd.DataFrame | None:
        self._raise_if_stopped()
        unit = RunUnit(target)
        token = _ACTIVE_UNIT.set(unit)
        df = None
        ok = False
        try:
            df = fn()
            ok = True
            return df
        finally:
            _ACTIVE_UNIT.reset(token)
            self._absorb(unit, df, ok)

    async def run_unit_async(
        self, target, coro_fn: Callable[[], Awaitable[pd.DataFrame | None]]
    ) -> pd.DataFrame | None:
        self._raise_if_stopped()
        unit = RunUnit(target)
        token = _ACTIVE_UNIT.set(unit)
        df = None
        ok = False
        try:
            df = await coro_fn()
            ok = True
            return df
        finally:
            _ACTIVE_UNIT.reset(token)
            self._absorb(unit, df, ok)

    def add_rows(self, df: pd.DataFrame | None) -> None:
        if df is None or df.empty:
            return
        stamped = self._stamp(df)
        with self._lock:
            self._frames.append(stamped)
        if self.uploader is not None:
            self.uploader.add(stamped)

    def close(self, error: BaseException | None = None, *, quiet: bool = False) -> pd.DataFrame:
        with self._lock:
            if self._closed:
                first = False
            else:
                self._closed = True
                first = True
        if not first:
            # A concurrent or later caller: wait for the first close() to finish its
            # save/flush/job_runs-row work, then hand back the same result. We never
            # re-raise the first call's exception here — that call already reported it.
            self._close_done.wait()
            return self._result

        # Safe default so a concurrent/later close() can never observe None, even if
        # something below raises before the real result is computed. self._close_done
        # is guaranteed to be set in the outer finally no matter what happens here.
        self._result = pd.DataFrame(columns=self._empty_columns)
        cause = error if error is not None else self._stop_error
        close_exc: BaseException | None = None
        self._closing = True
        try:
            try:
                if self.uploader is not None:
                    self.uploader.close()
            except Exception as e:  # noqa: BLE001 - the final save failing must still end the run
                close_exc = e
                cause = e
            finally:
                if self.cost_writer is not None:
                    self.cost_writer.close()
                if isinstance(cause, InsufficientBalanceError):
                    status = "stopped_low_balance"
                elif cause is not None:
                    status = "failed"
                else:
                    status = "completed"
                write_job_run_row(self._bq, self._job_run_row(
                    "end", status, error=None if cause is None else repr(cause)[:1000]))
            with self._lock:
                frames = list(self._frames)
            if frames:
                self._result = pd.concat(frames, ignore_index=True)
            elif cause is None and not quiet:
                print("No rows returned. Skipping upload.")
        finally:
            self._close_done.set()
        if close_exc is not None:
            raise close_exc
        return self._result

    # ----- internals -----

    def _raise_if_stopped(self) -> None:
        if self._stop_error is not None:
            raise self._stop_error

    def _finalize(self, record: dict, upload_id: str | None) -> dict:
        return {
            **record,
            "job_id": self.job_id,
            "upload_id": upload_id,
            "endpoint": self.endpoint,
            "endpoint_mode": self.endpoint_mode,
            "client_id": None,
            "ingest_timestamp": _now_iso(),
        }

    def _absorb(self, unit: RunUnit, df, ok: bool) -> None:
        # Cost is real the moment DFS billed it, whether or not `fn` returned normally
        # and whether or not `stamp` can make sense of the result — record spend and
        # cost rows first, and only mark the target completed when `fn` itself succeeded.
        spent = sum(r["cost_usd"] for r in unit.records)
        with self._lock:
            self.spent_usd += spent
            if ok:
                self._completed.extend(target_list(unit.target))

        stamped = None
        stamp_exc: BaseException | None = None
        if isinstance(df, pd.DataFrame) and not df.empty:
            try:
                stamped = self._stamp(df)
            except Exception as e:  # noqa: BLE001 - cost bookkeeping below must still run
                stamp_exc = e

        if stamped is not None:
            with self._lock:
                self._frames.append(stamped)

        def tag(upload_id: str | None) -> None:
            if self.cost_writer is not None and unit.records:
                self.cost_writer.add([self._finalize(r, upload_id) for r in unit.records],
                                     flush=False)

        if self.uploader is not None and self._tag_cost:
            self.uploader.add(stamped, on_joined=tag)
        else:
            tag(None)
            if self.uploader is not None and stamped is not None:
                self.uploader.add(stamped)
        if self.cost_writer is not None:
            self.cost_writer.flush_if_due()

        if stamp_exc is not None:
            raise stamp_exc

    def _before_save(self, upload_id: str) -> None:
        # Cost rows go out before data so a crash between the two loses rows, never spend.
        if self.cost_writer is not None:
            self.cost_writer.flush()

    def _after_save(self, upload_id: str) -> None:
        n = self.cost_writer.rows_by_upload_id.get(upload_id, 0) if self.cost_writer else 0
        if n and self._bq is not None:
            try:
                self._bq.log_upload_event(
                    job_id=self.job_id, upload_id=upload_id, source="dataforseo",
                    source_program="dfs_cost_log", dataset=DATASET, table=COST_LOG_TABLE,
                    row_count=n, timestamp=pd.Timestamp.now("UTC"),
                )
            except Exception as e:  # noqa: BLE001
                print(f"Cost-log upload event failed: {e}")
        if self._closing:
            return
        remaining = max(0.0, self.estimate.max_usd - self.spent_usd)
        try:
            check_balance(
                self.client, required_usd=remaining, balance_buffer=self._balance_buffer,
                ignore=self._ignore_balance, job_id=self.job_id, endpoint=self.endpoint,
                completed_targets=self.completed_targets(),
                remaining_targets=self.remaining_targets(),
                upload_ids=self.upload_ids, stage="mid-run",
            )
        except InsufficientBalanceError as e:
            self._stop_error = e
            raise

    def _job_run_row(self, event: str, status: str, *, balance: float | None = None,
                     error: str | None = None) -> dict:
        return {
            "job_id": self.job_id,
            "endpoint": self.endpoint,
            "endpoint_mode": self.endpoint_mode,
            "event": event,
            "status": status,
            "planned_requests": self.plan.planned_requests,
            "planned_items": self.plan.planned_items,
            "planned_max_rows": self.plan.planned_max_rows,
            "estimate_max_usd": self.estimate.max_usd,
            "estimate_avg_usd": self.estimate.avg_usd,
            "balance_at_start": balance,
            "balance_override": self._ignore_balance,
            "error": error,
            "client_id": None,
            "ingest_timestamp": _now_iso(),
        }
