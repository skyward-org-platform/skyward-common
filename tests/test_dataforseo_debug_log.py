"""Unit tests for the DFS debug-log collector (run-scoped buffer + periodic flush).

The collector buffers per-attempt debug records during a DFS run and flushes them
to the shared `DataForSEO.debug_request_logs` BigQuery table in batches — every
`flush_threshold` records and once at run end — so we never issue per-row DML and a
mid-run crash loses at most the last unflushed batch.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from skyward.data.dataforseo.debug_log import DebugLogCollector
from skyward.functions import generate_job_id
from tests.conftest import FakeBigQueryClient


@pytest.fixture
def bq():
    client = FakeBigQueryClient()
    client.log_upload_event = MagicMock()
    return client


def _row(i: int) -> dict:
    return {
        "endpoint": "keyword_suggestions",
        "target": f"seed{i}",
        "attempt": 1,
        "n_items": 0,
    }


def test_record_below_threshold_does_not_write(bq):
    col = DebugLogCollector(bq, job_id=generate_job_id(), flush_threshold=5)
    for i in range(4):
        col.record(_row(i))
    assert bq.client.loaded_tables == []


def test_record_reaching_threshold_flushes_batch(bq):
    col = DebugLogCollector(bq, job_id=generate_job_id(), flush_threshold=5)
    for i in range(5):
        col.record(_row(i))
    assert len(bq.client.loaded_tables) == 1
    assert len(bq.client.loaded_tables[0]["df"]) == 5


def test_flush_writes_remaining_and_is_idempotent(bq):
    col = DebugLogCollector(bq, job_id=generate_job_id(), flush_threshold=100)
    for i in range(3):
        col.record(_row(i))
    assert bq.client.loaded_tables == []
    col.flush()
    assert len(bq.client.loaded_tables) == 1
    assert len(bq.client.loaded_tables[0]["df"]) == 3
    col.flush()  # nothing buffered → no second write
    assert len(bq.client.loaded_tables) == 1


def test_flush_empty_buffer_is_noop(bq):
    col = DebugLogCollector(bq, job_id=generate_job_id())
    col.flush()
    assert bq.client.loaded_tables == []


def test_flush_stamps_upload_id_and_ingest_timestamp(bq):
    col = DebugLogCollector(bq, job_id=generate_job_id(), flush_threshold=2)
    col.record(_row(0))
    col.record(_row(1))
    written = bq.client.loaded_tables[0]["df"]
    assert written["upload_id"].notna().all()
    assert written["upload_id"].nunique() == 1
    assert written["ingest_timestamp"].notna().all()


def test_flush_writes_to_shared_debug_table(bq):
    col = DebugLogCollector(bq, job_id=generate_job_id(), flush_threshold=1)
    col.record(_row(0))
    assert bq.client.loaded_tables[0]["table_ref"] == (
        "data-hub-468216.DataForSEO.debug_request_logs"
    )


def test_flush_logs_upload_event(bq):
    jid = generate_job_id()
    col = DebugLogCollector(bq, job_id=jid, flush_threshold=1)
    col.record(_row(0))
    assert bq.log_upload_event.call_count == 1
    _, kwargs = bq.log_upload_event.call_args
    assert kwargs["job_id"] == jid
    assert kwargs["table"] == "debug_request_logs"
    assert kwargs["row_count"] == 1


def test_flush_stamps_job_id_on_every_row(bq):
    jid = generate_job_id()
    col = DebugLogCollector(bq, job_id=jid, flush_threshold=2)
    col.record(_row(0))
    col.record(_row(1))
    written = bq.client.loaded_tables[0]["df"]
    assert (written["job_id"] == jid).all()


def test_concurrent_records_do_not_lose_rows(bq):
    col = DebugLogCollector(bq, job_id=generate_job_id(), flush_threshold=10_000)

    def worker(start: int) -> None:
        for i in range(start, start + 250):
            col.record(_row(i))

    threads = [threading.Thread(target=worker, args=(s,)) for s in range(0, 1000, 250)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    col.flush()

    total = sum(len(t["df"]) for t in bq.client.loaded_tables)
    assert total == 1000
