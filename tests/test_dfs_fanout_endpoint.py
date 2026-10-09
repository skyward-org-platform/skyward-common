"""ChatGPT LLM Scraper (fan-out) endpoint: planning, queue mode, live mode, collector path."""

import asyncio
import itertools
import json
import threading
import time
from pathlib import Path

import pandas as pd
import pytest
import requests

import skyward.data.dataforseo.endpoints.ai_optimization_chat_gpt_llm_scraper as mod
from skyward.data.dataforseo import (
    ClientConfig, DataForSEOClient, IncompleteTaskError, InvalidLocationError,
    fanout_prompt_id,
)
from skyward.data.dataforseo.collector.allowlist import build_allowlist
from skyward.data.dataforseo.fanout import SCHEMA, parse_tag, unescape_prompt
from skyward.functions import generate_job_id
from tests.conftest import FakeBigQueryClient

KEY = "ai_optimization_chat_gpt_llm_scraper"
FIXTURES = Path(__file__).parent / "fixtures" / "llm_scraper"


class _Resp:
    def __init__(self, data, status_code=200):
        self._data, self.status_code = data, status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._data


class FakeScraper:
    """Stands in for DataForSEO's ChatGPT scraper: task_post, tasks_ready, task_get, live."""

    def __init__(self):
        self.posts: list[tuple[str, list, object]] = []
        self.gets: list[str] = []
        self.tasks: dict[str, dict] = {}
        self.collected: set[str] = set()
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self.reject: set[str] = set()          # prompts refused at submit / live (40501)
        self.empty_unforced: set[str] = set()  # prompts with no fan-out unless forced
        self.always_empty: set[str] = set()
        self.never_ready: set[str] = set()
        self.release_per_poll: int | None = None
        self.live_hook = None                  # (task, call_number) -> None | response dict
        self.live_delay = 0.0
        self.active = 0
        self.max_active = 0

    # -- helpers --
    @staticmethod
    def prompt_of(task):
        return unescape_prompt(task["keyword"])

    def _result(self, task):
        prompt = self.prompt_of(task)
        empty = prompt in self.always_empty or (
            prompt in self.empty_unforced and not task["force_web_search"])
        return {
            "keyword": prompt.replace("+", " "), "location_code": task["location_code"],
            "language_code": task["language_code"], "model": "gpt-6",
            "datetime": "2026-10-09 19:18:29 +00:00", "markdown": f"answer to {prompt}",
            "fan_out_queries": None if empty else [f"{prompt} q1", f"{prompt} q2"],
            "sources": None if empty else [{"url": "https://a.com/x", "domain": "a.com",
                                            "title": "A", "source_name": "A",
                                            "publication_date": None}],
            "search_results": None, "brand_entities": None, "item_types": ["chat_gpt_text"],
            "items": [{"type": "chat_gpt_text", "sources": None}],
        }

    def _task_id(self):
        with self._lock:
            return f"task-{next(self._ids)}"

    # -- transport --
    def post(self, url, json=None, timeout=None):
        with self._lock:
            self.posts.append((url, json, timeout))
        if url.endswith("/task_post"):
            tasks = []
            for task in json:
                tid = self._task_id()
                if self.prompt_of(task) in self.reject:
                    tasks.append({"id": tid, "status_code": 40501, "cost": 0,
                                  "status_message": "Invalid Field: 'keyword'.",
                                  "data": dict(task), "result": None})
                    continue
                with self._lock:
                    self.tasks[tid] = task
                tasks.append({"id": tid, "status_code": 20100, "status_message": "Task Created.",
                              "cost": 0.0024 if task.get("priority") == 2 else 0.0012,
                              "data": dict(task), "result": None})
            return _Resp({"status_code": 20000, "tasks": tasks})

        # live/advanced: one task per call
        task = json[0]
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            n = sum(1 for u, j, _ in self.posts if j[0]["keyword"] == task["keyword"])
        try:
            if self.live_delay:
                time.sleep(self.live_delay)
            if self.live_hook is not None:
                custom = self.live_hook(task, n)
                if custom is not None:
                    return custom
            tid = self._task_id()
            if self.prompt_of(task) in self.reject:
                return _Resp({"tasks": [{"id": tid, "status_code": 40501, "cost": 0,
                                         "status_message": "Invalid Field: 'keyword'.",
                                         "data": dict(task), "result": None}]})
            return _Resp({"tasks": [{"id": tid, "status_code": 20000, "status_message": "Ok.",
                                     "cost": 0.004, "data": dict(task),
                                     "result": [self._result(task)]}]})
        finally:
            with self._lock:
                self.active -= 1

    def get(self, url, timeout=None):
        with self._lock:
            self.gets.append(url)
        if url.endswith("/languages"):
            return _Resp({"tasks": [{"result": [{"language_code": "en"},
                                                {"language_code": "es-419"}]}]})
        if url.endswith("/tasks_ready"):
            waiting = [tid for tid, t in self.tasks.items()
                       if tid not in self.collected and self.prompt_of(t) not in self.never_ready]
            if self.release_per_poll is not None:
                waiting = waiting[:self.release_per_poll]
            return _Resp({"tasks": [{"result": [{"id": tid} for tid in waiting] or None}]})
        tid = url.rsplit("/", 1)[-1]
        task = self.tasks.get(tid)
        if task is None:
            return _Resp({"tasks": [{"id": tid, "status_code": 40401,
                                     "status_message": "Task Not Found."}]})
        if self.prompt_of(task) in self.never_ready:
            return _Resp({"tasks": [{"id": tid, "status_code": 40602,
                                     "status_message": "Task In Queue.", "data": dict(task)}]})
        self.collected.add(tid)
        return _Resp({"tasks": [{"id": tid, "status_code": 20000, "status_message": "Ok.",
                                 "cost": 0, "data": dict(task), "result": [self._result(task)]}]})

    # -- views --
    def task_posts(self):
        return [(u, j) for u, j, _ in self.posts if u.endswith("/task_post")]

    def live_posts(self):
        return [(u, j, t) for u, j, t in self.posts if u.endswith("/live/advanced")]


@pytest.fixture
def bq():
    return FakeBigQueryClient()


@pytest.fixture
def client(bq):
    c = DataForSEOClient(username="u", password="p", bq_client=bq)
    c._session = FakeScraper()
    c.ai_optimization_chat_gpt_llm_scraper._sleep = lambda s: None
    return c


@pytest.fixture
def ep(client):
    return client.ai_optimization_chat_gpt_llm_scraper


def cost_rows(bq):
    return [r for ins in bq.client.inserted_rows if ins["table"].endswith(".cost_log")
            for r in ins["rows"]]


def job_runs(bq):
    return [r for ins in bq.client.inserted_rows if ins["table"].endswith(".job_runs")
            for r in ins["rows"]]


def data_loads(bq):
    return [l for l in bq.client.loaded_tables if "llm_scraper" in l["table_ref"]]


def prompts(n):
    return [f"prompt number {i}" for i in range(n)]


# ----- identity and planning -----

def test_endpoint_identity(ep):
    assert ep.ENDPOINT_KEY == ep.COLLECTOR_ENDPOINT_KEY == KEY
    assert ep.TABLE_NAME == "ai_optimization-chat_gpt-llm_scraper"
    assert ep.location_flag == "in_llm_scraper"
    assert ep._get_schema() == SCHEMA


@pytest.mark.parametrize("kwargs,requests_,items,rows", [
    (dict(endpoint_mode="standard"), 3, 250, 250),
    (dict(endpoint_mode="standard", samples=3), 8, 750, 750),
    (dict(endpoint_mode="standard", priority=2), 3, 500, 250),
    (dict(endpoint_mode="standard", retry_empty=1), 3, 250, 500),
    (dict(endpoint_mode="standard", retry_empty=1, force_web_search=False), 5, 500, 500),
    (dict(endpoint_mode="live"), 250, 250, 250),
    (dict(endpoint_mode="live", priority=2), 250, 250, 250),
])
def test_plan(ep, kwargs, requests_, items, rows):
    plan = ep.plan(prompts(250), **kwargs)
    assert (plan.planned_requests, plan.planned_items, plan.planned_max_rows) == (
        requests_, items, rows)
    assert plan.endpoint == KEY


def test_estimate_cost_uses_flat_per_prompt_rates(ep):
    std = ep.estimate_cost(prompts(1000), endpoint_mode="standard")
    assert std.list_price_usd == pytest.approx(1.20) and std.basis == "list_price"
    assert std.max_usd == pytest.approx(1.32)
    assert ep.estimate_cost(prompts(1000), endpoint_mode="standard",
                            priority=2).list_price_usd == pytest.approx(2.40)
    assert ep.estimate_cost(prompts(1000), endpoint_mode="live").list_price_usd == pytest.approx(4.0)


def test_prepare_normalises_dedupes_and_rejects(ep):
    long_prompt = "x" * 2001
    plus_heavy = "+" * 700            # 2100 characters once escaped
    accepted, rejected, dupes = ep._prepare(
        ["  a b  ", "a b", "A b", "", long_prompt, plus_heavy, "ok"])
    assert [p for _, p in accepted] == ["a b", "A b", "ok"]
    assert dupes == 1
    assert [p for _, p, _ in rejected] == ["", long_prompt, plus_heavy]
    assert all(pid == fanout_prompt_id(p) for pid, p in accepted)


def test_cast_types(ep):
    df = pd.DataFrame([{**{c: None for c in SCHEMA}, "task_id": "t", "prompt": "p",
                        "attempt": 1, "fan_out_count": None, "force_web_search": True,
                        "answered_at": "2026-10-09 19:18:29 +00:00",
                        "fan_out_queries": None}])
    out = ep._cast_types(df)
    assert str(out["attempt"].dtype) == "Int64" and str(out["fan_out_count"].dtype) == "Int64"
    assert str(out["force_web_search"].dtype) == "boolean"
    assert str(out["answered_at"].dtype).startswith("datetime64[") and out["answered_at"].dt.tz
    assert str(out["fan_out_queries"].dtype) == "string" and out["fan_out_queries"].isna().all()


@pytest.mark.parametrize("extra", [{"domain": None}, {"domain_id": 3}, {"domain": "a.com"}])
def test_domain_arguments_are_refused(ep, client, extra):
    with pytest.raises(TypeError, match="no domain"):
        ep.post(["p"], job_id=generate_job_id(), **extra)
    with pytest.raises(TypeError, match="no domain"):
        ep.live(["p"], job_id=generate_job_id(), **extra)
    assert client._session.posts == []


def test_unknown_argument_is_refused(ep):
    with pytest.raises(TypeError, match="keywords_per_task"):
        ep.post(["p"], job_id=generate_job_id(), keywords_per_task=5)


@pytest.mark.parametrize("bad", [dict(samples=0), dict(retry_empty=-1), dict(priority=3)])
def test_bad_options(ep, bad):
    with pytest.raises(ValueError):
        ep.post(["p"], job_id=generate_job_id(), **bad)


# ----- queue mode, self-contained -----

def test_queue_250_prompts_three_posts_all_rows_saved_in_windows(ep, client, bq):
    job = generate_job_id()
    df = asyncio.run(ep.post_all(prompts(250), job_id=job))

    posts = client._session.task_posts()
    assert sorted(len(p) for _, p in posts) == [50, 100, 100]
    assert len(df) == 250 and set(df["prompt"]) == set(prompts(250))
    assert (df["endpoint_mode"] == "standard").all()
    assert df["domain_id"].isna().all() and df["domain"].isna().all()
    assert (df["fan_out_count"] == 2).all() and (df["attempt"] == 1).all()
    assert df["task_id"].is_unique

    rows = cost_rows(bq)
    assert len(rows) == 250 and {r["items_sent"] for r in rows} == {1}
    assert sum(r["cost_usd"] for r in rows) == pytest.approx(0.30)
    assert {r["endpoint"] for r in rows} == {KEY}
    assert {r["call_type"] for r in rows} == {"task_post"}
    assert not any("prompt number" in r["price_inputs"] for r in rows)   # no prompt text

    loads = data_loads(bq)
    assert sorted(len(l["df"]) for l in loads) == [50, 200]              # 200-row windows
    assert all(l["table_ref"].endswith("DataForSEO.ai_optimization-chat_gpt-llm_scraper")
               for l in loads)
    saved = pd.concat([l["df"] for l in loads])
    assert (saved["job_id"] == job).all() and saved["upload_id"].nunique() == 2
    assert saved["domain_id"].isna().all()

    end = [r for r in job_runs(bq) if r["event"] == "end"][0]
    assert end["status"] == "completed" and end["endpoint_mode"] == "standard"
    assert ep.rejected_prompts == []


def test_queue_collects_across_several_polls(ep, client):
    client._session.release_per_poll = 40
    df = ep.post(prompts(130), job_id=generate_job_id())
    assert len(df) == 130
    ready_polls = [u for u in client._session.gets if u.endswith("/tasks_ready")]
    assert len(ready_polls) >= 4


def test_queue_payload_escapes_and_returns_the_original_prompt(ep, client):
    prompt = "What is 100% of 50 + 25?"
    df = ep.post([prompt], job_id=generate_job_id())
    sent = client._session.task_posts()[0][1][0]
    assert sent["keyword"] == "What is 100%25 of 50 %2B 25?"
    assert sent["force_web_search"] is True and "priority" not in sent
    assert parse_tag(sent["tag"]) == (fanout_prompt_id(prompt), 1, True)
    assert df["prompt"].tolist() == [prompt]
    assert df["prompt_id"].tolist() == [fanout_prompt_id(prompt)]


def test_priority_and_answer_switch_reach_the_payload(ep, client):
    df = ep.post(["p"], job_id=generate_job_id(), priority=2, store_answer=False,
                 location_code=2826, language_code="es-419")
    sent = client._session.task_posts()[0][1][0]
    assert sent["priority"] == 2 and sent["tag"].endswith(".1.x")
    assert (sent["location_code"], sent["language_code"]) == (2826, "es-419")
    assert df["answer_markdown"].isna().all() and (df["answer_chars"] > 0).all()


def test_rejected_prompt_gets_a_row_and_does_not_sink_the_request(ep, client, bq):
    client._session.reject = {"prompt number 3"}
    df = ep.post(prompts(10), job_id=generate_job_id())
    assert len(df) == 10
    bad = df[df["prompt"] == "prompt number 3"].iloc[0]
    assert bad["status_code"] == 40501 and "Invalid Field" in bad["status_message"]
    assert pd.isna(bad["fan_out_count"])
    assert (df[df["prompt"] != "prompt number 3"]["status_code"] == 20000).all()
    assert ep.rejected_prompts == ["prompt number 3"]
    end = [r for r in job_runs(bq) if r["event"] == "end"][0]
    assert end["status"] == "partial"


def test_locally_refused_prompts_are_never_sent_but_get_rows(ep, client, bq):
    df = ep.post(["ok", "", "y" * 2001], job_id=generate_job_id())
    assert len(client._session.task_posts()[0][1]) == 1
    assert len(df) == 3
    refused = df[df["status_code"].isna()]
    assert len(refused) == 2 and refused["status_message"].str.startswith("Not sent").all()
    assert refused["task_id"].isna().all()
    assert len(ep.rejected_prompts) == 2
    assert len(cost_rows(bq)) == 1


def test_empty_fan_out_is_resubmitted_with_search_forced(ep, client, bq):
    client._session.empty_unforced = {"prompt number 1"}
    df = ep.post(prompts(3), job_id=generate_job_id(), force_web_search=False, retry_empty=1)
    mine = df[df["prompt"] == "prompt number 1"].sort_values("attempt")
    assert mine["attempt"].tolist() == [1, 2]
    assert pd.isna(mine.iloc[0]["fan_out_count"]) and mine.iloc[1]["fan_out_count"] == 2
    assert mine["force_web_search"].tolist() == [False, True]
    assert len(df) == 4
    retry_payload = client._session.task_posts()[-1][1]
    assert len(retry_payload) == 1 and retry_payload[0]["force_web_search"] is True
    assert retry_payload[0]["tag"].endswith(".2")
    assert len(cost_rows(bq)) == 4


def test_still_empty_after_retries_stops_and_keeps_every_attempt(ep, client):
    client._session.always_empty = {"p"}
    df = ep.post(["p"], job_id=generate_job_id(), retry_empty=2)
    assert sorted(df["attempt"]) == [1, 2, 3] and df["fan_out_count"].isna().all()
    assert len(client._session.task_posts()) == 3


def test_default_does_not_retry_when_fan_out_is_present(ep, client):
    ep.post(prompts(5), job_id=generate_job_id())
    assert len(client._session.task_posts()) == 1


def test_samples_and_retry_attempt_numbers_do_not_collide(ep, client):
    client._session.always_empty = {"p"}
    df = ep.post(["p", "q"], job_id=generate_job_id(), samples=3, retry_empty=1)
    assert sorted(df[df["prompt"] == "q"]["attempt"]) == [1, 2, 3]
    assert sorted(df[df["prompt"] == "p"]["attempt"]) == [1, 2, 3, 4, 5, 6]
    assert df["task_id"].is_unique


def test_timeout_keeps_collected_rows_and_names_what_is_left(client, bq):
    client.config.task_total_timeout = 0.4
    ep = client.ai_optimization_chat_gpt_llm_scraper
    ep._sleep = lambda s: time.sleep(0.05)
    client._session.never_ready = {"prompt number 7"}
    with pytest.raises(IncompleteTaskError) as info:
        ep.post(prompts(10), job_id=generate_job_id(), poll_interval=0)
    assert len(info.value.task_ids) == 1
    assert info.value.prompts == ["prompt number 7"]
    saved = pd.concat([l["df"] for l in data_loads(bq)])
    assert len(saved) == 9 and "prompt number 7" not in set(saved["prompt"])
    end = [r for r in job_runs(bq) if r["event"] == "end"][0]
    assert end["status"] == "failed"

    # the stragglers can be fetched later without paying again
    client._session.never_ready = set()
    before = len(cost_rows(bq))
    late = ep.collect(info.value.task_ids, job_id=generate_job_id())
    assert late["prompt"].tolist() == ["prompt number 7"] and late["fan_out_count"].tolist() == [2]
    assert len(cost_rows(bq)) == before


def test_missing_table_fails_before_anything_is_sent(ep, client, bq):
    def boom(table):
        raise RuntimeError("404 Not found")
    bq.client.get_table = boom
    with pytest.raises(RuntimeError, match="Nothing was sent"):
        ep.post(prompts(3), job_id=generate_job_id())
    assert client._session.posts == []
    # upload=False does not need the table
    assert len(ep.post(prompts(3), job_id=generate_job_id(), upload=False)) == 3


def test_unknown_language_fails_before_anything_is_sent(ep, client):
    with pytest.raises(ValueError, match="language_code 'xx'"):
        ep.post(["p"], job_id=generate_job_id(), language_code="xx")
    assert client._session.posts == []
    assert len(ep.post(["p"], job_id=generate_job_id(), language_code="xx",
                       ignore_location_check=True)) == 1


def test_city_level_location_is_refused_by_the_catalog(ep, client):
    asked = []

    def is_supported(code, flag):
        asked.append((code, flag))
        return False
    client.locations.is_supported = is_supported
    with pytest.raises(InvalidLocationError):
        ep.post(["p"], job_id=generate_job_id(), location_code=1014221)
    assert asked == [(1014221, "in_llm_scraper")]
    assert client._session.posts == []


def test_transport_failure_at_submit_gives_rows_not_an_exception(ep, client):
    client.config.max_retries = 1
    client.config.retry_delay = 0
    original = client._session.post

    def flaky(url, json=None, timeout=None):
        if url.endswith("/task_post"):
            raise requests.ConnectionError("down")
        return original(url, json=json, timeout=timeout)
    client._session.post = flaky
    df = ep.post(prompts(2), job_id=generate_job_id())
    assert len(df) == 2 and df["status_code"].isna().all()
    assert df["status_message"].str.contains("task_post failed").all()


# ----- live mode -----

def test_live_one_request_per_prompt_with_the_long_timeout(ep, client, bq):
    df = asyncio.run(ep.live_all(prompts(5), job_id=generate_job_id()))
    live = client._session.live_posts()
    assert len(live) == 5 and all(len(j) == 1 for _, j, _ in live)
    assert {t for _, _, t in live} == {180}
    assert len(df) == 5 and (df["endpoint_mode"] == "live").all()
    assert (df["fan_out_count"] == 2).all() and df["domain_id"].isna().all()
    rows = cost_rows(bq)
    assert len(rows) == 5 and sum(r["cost_usd"] for r in rows) == pytest.approx(0.02)
    saved = pd.concat([l["df"] for l in data_loads(bq)])
    by_task = {r["task_id"]: r["upload_id"] for r in rows}
    assert all(by_task[t] == u for t, u in zip(saved["task_id"], saved["upload_id"]))


def test_live_single_prompt_string(ep, client):
    df = ep.live("just one", job_id=generate_job_id())
    assert df["prompt"].tolist() == ["just one"]


def test_live_concurrency_is_bounded(ep, client):
    client._session.live_delay = 0.03
    ep.live(prompts(12), job_id=generate_job_id(), max_workers=3)
    assert 1 < client._session.max_active <= 3


def test_live_worker_cap(ep, client):
    client._session.live_delay = 0.01
    ep.live(prompts(60), job_id=generate_job_id(), max_workers=500)
    assert client._session.max_active <= ep.MAX_LIVE_WORKERS


def test_live_timeout_is_not_resent(ep, client, bq):
    def hook(task, n):
        if FakeScraper.prompt_of(task) == "slow one":
            raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=180)")
    client._session.live_hook = hook
    df = ep.live(["slow one", "fine"], job_id=generate_job_id())
    sent = [FakeScraper.prompt_of(j[0]) for _, j, _ in client._session.live_posts()]
    assert sent.count("slow one") == 1
    slow = df[df["prompt"] == "slow one"].iloc[0]
    assert "possibly billed" in slow["status_message"] and pd.isna(slow["status_code"])
    assert df[df["prompt"] == "fine"]["fan_out_count"].tolist() == [2]
    markers = [r for r in cost_rows(bq) if "unattributed_billed_retry" in r["price_inputs"]]
    assert len(markers) == 1 and markers[0]["cost_usd"] == 0.0


def test_live_connection_failure_is_reported_not_called_a_timeout(ep, client):
    def hook(task, n):
        raise requests.ConnectionError("connection refused")
    client._session.live_hook = hook
    df = ep.live(["p"], job_id=generate_job_id())
    assert "Live request failed" in df.iloc[0]["status_message"]
    assert len(client._session.live_posts()) == 1


def test_live_rate_limit_backs_off_then_succeeds(ep, client):
    slept = []
    ep._sleep = slept.append

    def hook(task, n):
        if n == 1:
            return _Resp({"tasks": [{"id": "x", "status_code": 40202, "cost": 0,
                                     "status_message": "Rate limit.", "data": dict(task)}]})
    client._session.live_hook = hook
    df = ep.live(["p"], job_id=generate_job_id())
    assert len(client._session.live_posts()) == 2 and len(slept) == 1
    assert df["status_code"].tolist() == [20000]
    assert ep.rejected_prompts == []


def test_live_http_429_backs_off(ep, client):
    slept = []
    ep._sleep = slept.append

    def hook(task, n):
        if n <= 2:
            return _Resp({}, status_code=429)
    client._session.live_hook = hook
    df = ep.live(["p"], job_id=generate_job_id())
    assert len(client._session.live_posts()) == 3 and len(slept) == 2
    assert df["fan_out_count"].tolist() == [2]


def test_live_empty_fan_out_is_retried_forced(ep, client):
    client._session.empty_unforced = {"p"}
    df = ep.live(["p"], job_id=generate_job_id(), force_web_search=False)
    assert sorted(df["attempt"]) == [1, 2]
    forced = [j[0]["force_web_search"] for _, j, _ in client._session.live_posts()]
    assert forced == [False, True]


def test_live_rejected_prompt(ep, client, bq):
    client._session.reject = {"bad"}
    df = ep.live(["bad", "good"], job_id=generate_job_id())
    assert df[df["prompt"] == "bad"]["status_code"].tolist() == [40501]
    assert ep.rejected_prompts == ["bad"]


# ----- collector path -----

def test_collector_submits_in_hundreds_and_tracks_without_a_domain(ep, client, monkeypatch):
    captured = {}
    monkeypatch.setattr(mod, "submit_and_wait", lambda **k: (
        captured.update(k) or {"total_tasks": len(k["posted_tasks"]), "proceeded": True}))
    summary = ep.post(prompts(250) + [""], job_id=generate_job_id(), use_collector=True)
    assert sorted(len(p) for _, p in client._session.task_posts()) == [50, 100, 100]
    assert len(captured["posted_tasks"]) == 250
    assert captured["endpoint_key"] == KEY
    assert captured["domain_id"] is None and captured["domain"] is None
    tag = captured["posted_tasks"][0]["keyword"]          # the collector's only handle
    assert parse_tag(tag)[0] is not None
    assert summary["total_tasks"] == 250 and summary["rejected_prompts"] == [""]
    assert [u for u in client._session.gets if "task_get" in u or "tasks_ready" in u] == []


def test_collector_mode_refuses_retries(ep, client):
    with pytest.raises(ValueError, match="retry_empty is not available"):
        ep.post(["p"], job_id=generate_job_id(), use_collector=True, retry_empty=1)
    assert client._session.posts == []


def test_config_use_collector_is_honoured(bq, monkeypatch):
    client = DataForSEOClient(username="u", password="p", bq_client=bq,
                              config=ClientConfig(use_collector=True))
    client._session = FakeScraper()
    monkeypatch.setattr(mod, "submit_and_wait", lambda **k: {"total_tasks": 1})
    out = client.ai_optimization_chat_gpt_llm_scraper.post(["p"], job_id=generate_job_id())
    assert isinstance(out, dict)


def test_allowlist_handler_parses_a_real_response_without_the_prompt_list(bq):
    client = DataForSEOClient(username="u", password="p", bq_client=bq)
    handler = build_allowlist(client)[KEY]
    assert handler.canonical_table == "ai_optimization-chat_gpt-llm_scraper"
    assert handler.ready_url.endswith("llm_scraper/tasks_ready")
    assert handler.get_url.endswith("llm_scraper/task_get/advanced")
    raw = json.loads((FIXTURES / "full.json").read_text())
    df = handler.cast(handler.parse(raw, "some-tag"))
    assert len(df) == 1
    row = df.iloc[0]
    assert row["task_id"] == raw["tasks"][0]["id"]
    assert row["prompt"] == raw["tasks"][0]["data"]["keyword"]
    assert row["prompt_id"] == fanout_prompt_id(row["prompt"])
    assert row["fan_out_count"] == 5


def test_allowlist_handler_gives_a_row_for_a_failed_task(bq):
    client = DataForSEOClient(username="u", password="p", bq_client=bq)
    handler = build_allowlist(client)[KEY]
    raw = {"tasks": [{"id": "t", "status_code": 40103, "status_message": "Task execution failed.",
                      "data": {"keyword": "p", "tag": f"{fanout_prompt_id('p')}.1"}}]}
    df = handler.cast(handler.parse(raw, None))
    assert len(df) == 1 and df.iloc[0]["status_code"] == 40103
