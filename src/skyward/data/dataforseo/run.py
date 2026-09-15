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

from skyward.data.dataforseo.cost_log import extract_cost_records
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
