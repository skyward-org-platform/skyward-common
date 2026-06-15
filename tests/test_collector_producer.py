"""Producer-side core for the collector path:
- parse_task_post(): extract posted task_ids and detect 40200 (Payment Required) at submit.
- submit_and_wait(): submit tracking rows, poll dfs_job_summary until proceed_at_pct, return a receipt.
Both are endpoint-agnostic and injectable for tests (no real BQ / no in-process polling).
"""

from __future__ import annotations

from skyward.data.dataforseo.collector.producer import parse_task_post, submit_and_wait

POST_OK = {"tasks": [
    {"id": "t1", "status_code": 20100, "data": {"tag": "kw1"}},
    {"id": "t2", "status_code": 20100, "data": {"tag": "kw2"}},
]}
POST_MIXED = {"tasks": [
    {"id": "t1", "status_code": 20100, "data": {"tag": "kw1"}},
    {"id": "t2", "status_code": 40200, "data": {"tag": "kw2"}},   # Payment Required
    {"id": "t3", "status_code": 40000, "data": {"tag": "kw3"}},   # some other failure
]}


def test_parse_task_post_all_ok():
    r = parse_task_post(POST_OK)
    assert [p["task_id"] for p in r["posted"]] == ["t1", "t2"]
    assert r["posted"][0]["keyword"] == "kw1"
    assert r["rejected_payment"] == 0
    assert r["rejected_other"] == 0


def test_parse_task_post_detects_40200_and_other():
    r = parse_task_post(POST_MIXED)
    assert [p["task_id"] for p in r["posted"]] == ["t1"]
    assert r["rejected_payment"] == 1   # the 40200
    assert r["rejected_other"] == 1     # the 40000


def test_parse_task_post_handles_none_and_empty():
    assert parse_task_post(None)["posted"] == []
    assert parse_task_post({})["posted"] == []
    assert parse_task_post({"tasks": []})["rejected_payment"] == 0


class _FakeStore:
    def __init__(self, pct_seq):
        self._pct = list(pct_seq)
        self.submitted = None

    def submit(self, *, job_id, endpoint, tasks):
        self.submitted = (job_id, endpoint, list(tasks))

    def completion_pct(self, *, job_id, endpoint):
        return self._pct.pop(0) if len(self._pct) > 1 else self._pct[0]


def _clock():
    t = [0.0]
    return t, (lambda s: t.__setitem__(0, t[0] + s)), (lambda: t[0])


def test_submit_and_wait_submits_then_proceeds_when_complete():
    store = _FakeStore([0.0, 0.5, 1.0])
    _, sleep, now = _clock()
    posted = [{"task_id": "t1", "keyword": "k1"}, {"task_id": "t2", "keyword": "k2"}]
    summary = submit_and_wait(
        bq_client=None, job_id="j", endpoint_key="serp_google_organic",
        posted_tasks=posted, proceed_at_pct=1.0, poll_interval=5, max_wait=100,
        store=store, sleep=sleep, now=now,
    )
    assert store.submitted[0] == "j"
    assert store.submitted[1] == "serp_google_organic"
    assert summary["total_tasks"] == 2
    assert summary["completion_pct"] == 1.0
    assert summary["proceeded"] is True


def test_submit_and_wait_early_proceed_at_threshold():
    store = _FakeStore([0.0, 0.96])
    _, sleep, now = _clock()
    summary = submit_and_wait(
        bq_client=None, job_id="j", endpoint_key="ep",
        posted_tasks=[{"task_id": "t1", "keyword": "k"}], proceed_at_pct=0.95,
        poll_interval=1, max_wait=100, store=store, sleep=sleep, now=now,
    )
    assert summary["proceeded"] is True
    assert summary["completion_pct"] >= 0.95


def test_submit_and_wait_times_out_without_proceeding():
    store = _FakeStore([0.0])  # never completes
    _, sleep, now = _clock()
    summary = submit_and_wait(
        bq_client=None, job_id="j", endpoint_key="ep",
        posted_tasks=[{"task_id": "t1", "keyword": "k"}], proceed_at_pct=1.0,
        poll_interval=10, max_wait=20, store=store, sleep=sleep, now=now,
    )
    assert summary["proceeded"] is False
    assert summary["completion_pct"] == 0.0
