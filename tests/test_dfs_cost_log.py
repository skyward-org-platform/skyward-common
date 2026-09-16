import json

from skyward.data.dataforseo.cost_log import (
    CostLogWriter, classify_call, cost_flush_every, extract_cost_records,
)
from tests.conftest import FakeBigQueryClient

BASE = "https://api.dataforseo.com/v3"


def test_classify_call():
    assert classify_call(f"{BASE}/serp/google/organic/live/advanced", [{"keyword": "a"}]) == "live"
    assert classify_call(f"{BASE}/backlinks/backlinks/live", [{"target": "a", "offset": 0}]) == "live_page"
    assert classify_call(f"{BASE}/serp/google/organic/task_post", [{}]) == "task_post"
    assert classify_call(f"{BASE}/serp/google/organic/task_get/advanced/abc", None) is None
    assert classify_call(f"{BASE}/serp/google/organic/tasks_ready", None) is None
    assert classify_call(f"{BASE}/appendix/user_data", None) is None


def test_extract_one_record_per_task_with_payload_alignment():
    payload = [{"keyword": "a", "tag": "a", "depth": 20, "location_code": 2840},
               {"keyword": "b", "tag": "b", "depth": 20, "location_code": 2840}]
    resp = {"tasks": [
        {"id": "t1", "status_code": 20100, "cost": 0.0012, "result": None},
        {"id": "t2", "status_code": 40200, "cost": 0, "result": None},
    ]}
    recs = extract_cost_records(f"{BASE}/serp/google/organic/task_post", payload, resp, 200)
    assert [r["task_id"] for r in recs] == ["t1", "t2"]
    assert recs[0]["cost_usd"] == 0.0012 and recs[1]["cost_usd"] == 0.0
    assert recs[0]["call_type"] == "task_post"
    assert json.loads(recs[0]["price_inputs"]) == {"depth": 20, "location_code": 2840}
    assert recs[0]["items_sent"] == 1 and recs[0]["result_rows"] == 0


def test_extract_counts_items_and_keywords():
    payload = [{"keywords": ["a", "b", "c"], "location_code": 2840}]
    resp = {"tasks": [{"id": "t", "status_code": 20000, "cost": 0.0124,
                       "result": [{"items": [{}, {}]}]}]}
    rec = extract_cost_records(f"{BASE}/dataforseo_labs/google/keyword_overview/live", payload, resp, 200)[0]
    assert rec["items_sent"] == 3
    assert rec["result_rows"] == 2


def test_extract_result_without_items_counts_result_entries():
    resp = {"tasks": [{"id": "t", "status_code": 20000, "cost": 0.09,
                       "result": [{"keyword": "a"}, {"keyword": "b"}]}]}
    rec = extract_cost_records(f"{BASE}/keywords_data/google_ads/search_volume/live",
                               [{"keywords": ["a", "b"]}], resp, 200)[0]
    assert rec["result_rows"] == 2


def test_extract_ignores_non_billable_and_empty():
    assert extract_cost_records(f"{BASE}/x/task_get/1", None, {"tasks": [{"cost": 1}]}, 200) == []
    assert extract_cost_records(f"{BASE}/x/live", [{}], None, None) == []


def test_cost_flush_every_scales_to_one_percent_with_bounds():
    # A floor of 25 keeps flush_if_due() (called after every absorbed unit) from firing
    # on nearly every unit for small/medium runs — CostLogWriter.flush() holds its write
    # lock across a BigQuery round trip, so that cadence serializes fan-out workers.
    assert cost_flush_every(0) == 25
    assert cost_flush_every(50) == 25
    assert cost_flush_every(1000) == 25
    assert cost_flush_every(2500) == 25   # 1% scaling lands exactly on the floor
    assert cost_flush_every(2600) == 26   # just above the floor, scaling still applies
    assert cost_flush_every(100_000) == 500
    assert cost_flush_every(10_000_000) == 500


def _row(uid="u1", cost=0.01):
    return {"job_id": "j", "upload_id": uid, "cost_usd": cost}


def test_writer_streams_at_cadence():
    bq = FakeBigQueryClient()
    w = CostLogWriter(bq, flush_every=2, sleep=lambda s: None)
    w.add([_row()])
    assert bq.client.inserted_rows == []
    w.add([_row()])
    assert len(bq.client.inserted_rows) == 1
    assert bq.client.inserted_rows[0]["table"] == "data-hub-468216.DataForSEO.cost_log"
    assert w.written_rows == 2
    assert w.rows_by_upload_id == {"u1": 2}


def test_writer_add_without_flush_then_flush_if_due():
    bq = FakeBigQueryClient()
    w = CostLogWriter(bq, flush_every=1, sleep=lambda s: None)
    w.add([_row()], flush=False)
    assert bq.client.inserted_rows == []
    w.flush_if_due()
    assert len(bq.client.inserted_rows) == 1


def test_writer_retries_then_keeps_rows_buffered_on_failure(capsys):
    bq = FakeBigQueryClient()
    bq.client.insert_errors = [["e1"], ["e2"], ["e3"]]
    w = CostLogWriter(bq, flush_every=100, max_attempts=3, sleep=lambda s: None)
    w.add([_row(cost=0.5)])
    assert w.flush() is False
    assert w.written_rows == 0
    w.close()  # insert_errors exhausted -> succeeds now
    assert w.written_rows == 1


def test_writer_close_reports_unlogged_cost(capsys):
    bq = FakeBigQueryClient()
    bq.client.insert_errors = [["e"]] * 6
    w = CostLogWriter(bq, flush_every=100, max_attempts=3, sleep=lambda s: None)
    w.add([_row(cost=0.25)])
    w.flush()
    w.close()
    assert "DFS cost logging FAILED: 1 cost rows ($0.2500)" in capsys.readouterr().out


def test_extract_coerces_non_numeric_cost_to_zero():
    resp = {"tasks": [{"id": "t", "status_code": 20000, "cost": "abc"}]}
    rec = extract_cost_records(f"{BASE}/serp/google/organic/live", [{}], resp, 200)[0]
    assert rec["cost_usd"] == 0.0


def test_extract_skips_malformed_task_and_logs_warning():
    payload = [{"keyword": "a"}, {"keyword": "b"}, {"keyword": "c"}]
    resp = {"tasks": [
        {"id": "t1", "status_code": 20000, "cost": 0.01, "result": [{"items": [{}]}]},
        {"id": "t2", "status_code": 20000, "cost": 0.02, "result": 5},  # non-iterable result
        {"id": "t3", "status_code": 20000, "cost": 0.03, "result": [{"items": [{}]}]},
    ]}
    recs = extract_cost_records(f"{BASE}/serp/google/organic/live", payload, resp, 200)
    assert len(recs) == 2
    assert [r["task_id"] for r in recs] == ["t1", "t3"]


def test_extract_treats_non_list_tasks_as_empty():
    resp = {"tasks": 1}
    recs = extract_cost_records(f"{BASE}/serp/google/organic/live", [{}], resp, 200)
    assert recs == []


def test_writer_rows_by_upload_id_only_counts_written_rows():
    bq = FakeBigQueryClient()
    bq.client.insert_errors = [["e1"], ["e2"], ["e3"]]
    w = CostLogWriter(bq, flush_every=100, max_attempts=3, sleep=lambda s: None)
    w.add([_row(uid="u1")], flush=False)
    assert w.flush() is False
    assert w.rows_by_upload_id == {}
    assert w.written_rows == 0
    w.close()  # insert_errors exhausted -> succeeds
    assert w.rows_by_upload_id == {"u1": 1}
    assert w.written_rows == 1
