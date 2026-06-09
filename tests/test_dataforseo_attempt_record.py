"""`build_attempt_record` assembles one debug-log row from an attempt's raw parts.

Shared by the wired endpoints so the field extraction (task status/cost, json
encoding of payload/response, http_status/error from the status_sink) lives in one
place rather than being copy-pasted into each `_fetch_live`.
"""

from __future__ import annotations

import json

from skyward.data.dataforseo.debug_log import build_attempt_record

_RESP = {
    "status_code": 20000,
    "tasks": [
        {
            "status_code": 20000,
            "status_message": "Ok.",
            "cost": 0.0101,
            "result": [{"items": [{"keyword": "x"}]}],
        }
    ],
}


def _record(**over):
    base = dict(
        endpoint="keyword_suggestions",
        target="busbank",
        attempt=1,
        is_terminal=True,
        started_at="2026-06-09T02:19:00+00:00",
        duration_ms=123,
        status_sink={"http_status": 200, "error": ""},
        payload=[{"keyword": "busbank"}],
        resp=_RESP,
        n_items=1,
    )
    base.update(over)
    return build_attempt_record(**base)


def test_extracts_task_status_and_cost():
    rec = _record()
    assert rec["task_status_code"] == 20000
    assert rec["task_status_message"] == "Ok."
    assert rec["task_cost"] == 0.0101


def test_http_status_and_error_from_sink():
    rec = _record(status_sink={"http_status": 500, "error": "boom"})
    assert rec["http_status"] == 500
    assert rec["error"] == "boom"


def test_payload_and_response_are_json_strings():
    rec = _record()
    assert json.loads(rec["payload"]) == [{"keyword": "busbank"}]
    assert json.loads(rec["response"])["status_code"] == 20000


def test_list_target_is_json_encoded():
    rec = _record(target=["a", "b"])
    assert json.loads(rec["target"]) == ["a", "b"]


def test_string_target_passes_through():
    rec = _record(target="busbank")
    assert rec["target"] == "busbank"


def test_none_response_yields_null_fields():
    rec = _record(resp=None, n_items=0, status_sink={"http_status": None, "error": "ConnErr"})
    assert rec["response"] is None
    assert rec["task_status_code"] is None
    assert rec["task_cost"] == 0.0
    assert rec["http_status"] is None
    assert rec["error"] == "ConnErr"


def test_thread_name_captured():
    rec = _record()
    assert isinstance(rec["thread_name"], str) and rec["thread_name"]


def test_core_fields_passed_through():
    rec = _record(attempt=3, is_terminal=False, duration_ms=999, n_items=7)
    assert rec["endpoint"] == "keyword_suggestions"
    assert rec["attempt"] == 3
    assert rec["is_terminal"] is False
    assert rec["duration_ms"] == 999
    assert rec["n_items"] == 7
    assert rec["started_at"] == "2026-06-09T02:19:00+00:00"
