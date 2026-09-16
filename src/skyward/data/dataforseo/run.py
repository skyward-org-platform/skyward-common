"""Run lifecycle for DataForSEO endpoint calls (v1.6.1).

Every public run method builds a RunContext. Each unit of work (one `_fetch_live` call,
one bulk batch, one task_post batch) runs inside `RunContext.run_unit`, which makes a
RunUnit the active unit for the executing thread. `DataForSEOClient._post` records billed
responses into the active unit. Linking is only by job_id, upload_id and task_id.
"""

from __future__ import annotations

import hashlib
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
    extract_unattributed_billed_retry_record,
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


def round_money(value: float | None) -> float | None:
    """Round a money value to 6 decimal places so it fits BigQuery NUMERIC.

    DataForSEO's balance endpoint (and float math in general) can return values with
    far more than 9 digits after the decimal point (e.g. 33.357433999999557). BigQuery
    NUMERIC allows at most 9 fractional digits, so an unrounded value like that makes
    the whole row insert fail — silently, since job_runs/cost_log writers only log a
    warning on failure. Round defensively at every writer that touches a money field
    (balance_at_start, estimate_max_usd, estimate_avg_usd, cost_usd) so this can't
    happen regardless of how the float arrived.
    """
    return None if value is None else round(float(value), 6)


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

    def record_unattributed_billed_retry(self, url: str, payload, http_status: int,
                                          attempt: int) -> None:
        """A 2xx DataForSEO response that DFS already billed but whose body could not
        be used, right before `_post` retries the same payload (and gets billed again).
        Appends a zero-cost marker row (see `extract_unattributed_billed_retry_record`)
        so the gap is visible in cost_log instead of only in a log line.
        """
        record = extract_unattributed_billed_retry_record(url, payload, http_status, attempt)
        if record is not None:
            self.records.append(record)


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
    max_age_s: float = 60.0,
) -> float | None:
    """Fail fast when balance < required_usd * balance_buffer. Returns the balance read.

    `max_age_s` controls how stale a cached balance reading may be. Pre-run callers keep
    the default (a same-run cache hit right after a mid-run check is fine); the mid-run
    check passes 0 so it never reuses the pre-run reading or an earlier mid-run one.
    """
    if required_usd <= 0:
        return None
    info = client.get_balance_cached(max_age_s=max_age_s)
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


def job_run_row_id(row: dict) -> str:
    """Deterministic BigQuery insertId for a job_runs row.

    Derived from job_id, endpoint, endpoint_mode, event and status, plus the row's own
    ingest_timestamp -- so a start row and an end row for the same run always differ,
    but a retry of the exact same row (same event, same ingest_timestamp) reuses the
    same id. That lets BigQuery's insertId de-duplication catch a retried insert after a
    lost acknowledgement instead of writing a second start/end row, which would corrupt
    job_progress's runs_started/runs_ended counts.
    """
    key = "|".join(str(row.get(k, "")) for k in
                   ("job_id", "endpoint", "endpoint_mode", "event", "status", "ingest_timestamp"))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def write_job_run_row(bq_client, row: dict, *, max_attempts: int = 3, sleep=time.sleep) -> bool:
    """Stream one job_runs row. Never raises; returns False when it could not write.

    The row's insertId (see `job_run_row_id`) is computed once, before the retry loop,
    and reused on every attempt -- BigQuery's insertId de-duplication is best effort and
    time limited (roughly a few minutes), so this narrows rather than guarantees against
    a lost-acknowledgement retry writing a duplicate start/end row.
    """
    if bq_client is None:
        return False
    table = f"{bq_client.client.project}.{DATASET}.{JOB_RUNS_TABLE}"
    row_id = job_run_row_id(row)
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            errors = bq_client.client.insert_rows_json(table, [row], row_ids=[row_id])
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
        self.save_failures: list[str] = []
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

    def note_save_failure(self, upload_id: str) -> None:
        """Record that the write callback could not save this window to BigQuery.

        The write callback (BaseEndpoint._start_run) calls this when `upload()` returns
        False. `close()` turns any recorded failure into a "failed" job_runs end row and a
        loud warning — otherwise the run reports "completed" while cost_log carries an
        upload_id whose data never landed.
        """
        with self._lock:
            self.save_failures.append(upload_id)

    # ----- lifecycle -----

    def start(self, balance: float | None) -> None:
        write_job_run_row(self._bq, self._job_run_row("start", "running", balance=balance))

    def run_unit(
        self, target, fn: Callable[[], pd.DataFrame | None], *, mark_complete: bool = True,
    ) -> pd.DataFrame | None:
        """Run `fn` as one cost-tracked unit for `target`.

        `mark_complete=False` lets a caller that fetches one target over several units
        (e.g. one unit per page of pagination) record cost/rows for each unit without
        `target` showing up in `completed_targets()` until the caller itself marks it
        done — otherwise a mid-run balance stop would report a partially-fetched target
        as finished.
        """
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
            self._absorb(unit, df, ok, mark_complete=mark_complete)

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

    @property
    def is_closing(self) -> bool:
        """True once close() has begun. Lets a late writer skip work that cannot land."""
        return self._closing or self._closed

    def add_rows(self, df: pd.DataFrame | None) -> None:
        if df is None or df.empty:
            return
        stamped = self._stamp(df)
        with self._lock:
            too_late = self._closing or self._closed
            if not too_late:
                self._frames.append(stamped)
        if too_late:
            # A writer that was still in flight when close() ran -- e.g. a SERP legacy
            # worker whose task_get returned after the coordinator joined. The frames are
            # already concatenated and the uploader closed, so these rows can reach
            # neither the returned DataFrame nor BigQuery. Never raise: the SERP worker's
            # `except Exception` would turn that into a bogus per-keyword failure row.
            # Log loudly instead, so dropped rows are visible rather than silent.
            logger.error(
                "[%s] job %s: dropped %d row(s) handed to add_rows after the run closed; "
                "they were NOT saved.", self.endpoint, self.job_id, len(df))
            return
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
                if cause is None:
                    cause = e
                else:
                    # Something already ended this run -- a balance stop, or the error the
                    # caller is in the middle of re-raising. That is the real cause:
                    # letting the save failure replace it would report "failed" for what
                    # was actually a low-balance stop, and re-raising it below would
                    # pre-empt the caller's own `raise` and hand the consumer the save
                    # error instead of the original. Log it and append it to the end row's
                    # error field below; it must NOT go into save_failures, which holds
                    # upload_ids and is rendered as such.
                    logger.error(
                        "[%s] job %s: the final save failed while the run was already "
                        "ending (%r); the run's original cause stands.",
                        self.endpoint, self.job_id, e)
            finally:
                if self.cost_writer is not None:
                    self.cost_writer.close()
                with self._lock:
                    save_failures = list(self.save_failures)
                if isinstance(cause, InsufficientBalanceError):
                    status = "stopped_low_balance"
                    error_str = repr(cause)[:1000]
                elif cause is not None:
                    status = "failed"
                    error_str = repr(cause)[:1000]
                elif save_failures:
                    # No exception was raised (upload() swallows its own failures), but
                    # one or more windows never reached BigQuery — the run must not be
                    # allowed to report "completed" while that's true.
                    status = "failed"
                    error_str = (f"{len(save_failures)} save window(s) did not reach "
                                 f"BigQuery: upload_id(s) {', '.join(save_failures)}")
                    warning = (f"[{self.endpoint}] WARNING: {error_str}. cost_log rows for "
                               f"these windows were recorded, but the underlying data was "
                               f"NOT saved to BigQuery.")
                    print(warning)
                    logger.error(warning)
                else:
                    status = "completed"
                    error_str = None
                if close_exc is not None and cause is not close_exc:
                    # The save failure is not the cause, but it still has to be visible.
                    error_str = f"{error_str} | final save also failed: {close_exc!r}"[:1000]
                write_job_run_row(self._bq, self._job_run_row("end", status, error=error_str))
            with self._lock:
                frames = list(self._frames)
            if frames:
                self._result = pd.concat(frames, ignore_index=True)
            elif cause is None and not quiet:
                print("No rows returned. Skipping upload.")
        finally:
            self._close_done.set()
        if close_exc is not None and cause is close_exc:
            # Only when the save failure is itself what ended the run. When there was a
            # pre-existing cause the caller is already re-raising that, and raising here
            # would stop its `raise` from ever running.
            raise close_exc
        return self._result

    # ----- internals -----

    def _raise_if_stopped(self) -> None:
        if self._stop_error is not None:
            raise self._stop_error

    def _finalize(self, record: dict, upload_id: str | None) -> dict:
        return {
            **record,
            "cost_usd": round_money(record.get("cost_usd")),
            "job_id": self.job_id,
            "upload_id": upload_id,
            "endpoint": self.endpoint,
            "endpoint_mode": self.endpoint_mode,
            "client_id": None,
            "ingest_timestamp": _now_iso(),
        }

    def _absorb(self, unit: RunUnit, df, ok: bool, *, mark_complete: bool = True) -> None:
        # Cost is real the moment DFS billed it, whether or not `fn` returned normally
        # and whether or not `stamp` can make sense of the result — record spend and
        # cost rows first, and only mark the target completed when `fn` itself succeeded
        # (and, for multi-unit targets like paginated fetches, when the caller says so).
        spent = sum(r["cost_usd"] for r in unit.records)
        with self._lock:
            self.spent_usd += spent
            if ok and mark_complete:
                self._completed.extend(target_list(unit.target))

        stamped = None
        stamp_exc: BaseException | None = None
        if isinstance(df, pd.DataFrame) and not df.empty:
            try:
                stamped = self._stamp(df)
            except Exception as e:  # noqa: BLE001 - cost bookkeeping below must still run
                stamp_exc = e

        too_late = False
        if stamped is not None:
            with self._lock:
                too_late = self._closing or self._closed
                if not too_late:
                    self._frames.append(stamped)
        if too_late:
            # close() has already snapshotted _frames and cached _result, so anything
            # appended now is read by nobody. add_rows learned this; _absorb is the other
            # writer and had no guard, which mattered most when upload=False leaves the
            # uploader None: there was then no refusal log anywhere and the rows vanished
            # in total silence. Never raise -- callers treat that as a per-target failure.
            logger.error(
                "[%s] job %s: dropped %d row(s) absorbed after the run closed; they were "
                "NOT saved. The cost rows for them are still recorded.",
                self.endpoint, self.job_id, len(stamped))

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
                upload_ids=self.upload_ids, stage="mid-run", max_age_s=0,
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
            "estimate_max_usd": round_money(self.estimate.max_usd),
            "estimate_avg_usd": round_money(self.estimate.avg_usd),
            "balance_at_start": round_money(balance),
            "balance_override": self._ignore_balance,
            "error": error,
            "client_id": None,
            "ingest_timestamp": _now_iso(),
        }
