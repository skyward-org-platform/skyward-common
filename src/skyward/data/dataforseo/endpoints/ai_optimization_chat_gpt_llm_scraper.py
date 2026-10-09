"""ai_optimization_chat_gpt_llm_scraper — v3/ai_optimization/chat_gpt/llm_scraper

Prompts in, ChatGPT fan-out queries out. DataForSEO drives the real ChatGPT web UI and
returns the searches it ran ("fan-out queries") plus the answer, cited sources, retrieved
search results, brand entities and local businesses.

Three ways to run it:
- `post_all()` / `post()`: the queue (task_post, tasks_ready, task_get). $0.0012 a prompt,
  100 prompts per request. Use this for bulk. Self-contained by default; with
  `use_collector=True` the cloud collector drains the results instead.
- `live_all()` / `live()`: one prompt per request, $0.004, answers in up to two minutes.

Things that are different from every other endpoint here:
- No domain. The same prompt can serve several clients, so `domain_id`/`domain` stay NULL
  and rows are keyed by `prompt_id` (see `skyward.data.dataforseo.fanout_prompt_id`).
- Location is country level only. A prompt with no place in it gets a random city of that
  country in its fan-out; put the place in the prompt to get local fan-out.
- Fan-out is a sample, not a fixed set: repeat runs reword it. `samples=N` runs each
  prompt N times; each run is its own row.
- One row is written per task, including failed, refused and empty ones.
"""

from __future__ import annotations

import asyncio
import functools
import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

from skyward.data.dataforseo.base import BaseEndpoint
from skyward.data.dataforseo.collector.producer import parse_task_post, submit_and_wait
from skyward.data.dataforseo.exceptions import IncompleteTaskError
from skyward.data.dataforseo.fanout import (
    MAX_PROMPT_CHARS, OK_STATUS, SCHEMA, escape_prompt, fanout_prompt_id, local_row,
    make_tag, normalize_prompt, parse_tag, parse_task,
)
from skyward.data.dataforseo.run import DEFAULT_BALANCE_BUFFER, active_unit, target_list
from skyward.functions import _validate_job_id

_PATH = "ai_optimization/chat_gpt/llm_scraper"
_TASK_CREATED = 20100
_IN_QUEUE = (40601, 40602)          # task handed / in queue: not ready yet
_RATE_LIMITED = (40202, 40209)      # per-minute and simultaneous-request limits
_INT_COLS = ("location_code", "attempt", "status_code", "fan_out_count", "source_count",
             "search_result_count", "brand_entity_count", "local_business_count",
             "answer_chars")


@dataclass(frozen=True)
class _Options:
    location_code: int
    language_code: str
    force_web_search: bool
    samples: int
    retry_empty: int
    priority: int
    store_answer: bool


@dataclass(frozen=True)
class _Spec:
    """One task to send: which prompt, which sample of it, and which retry of that sample."""

    prompt_id: str
    sample: int
    retry: int
    forced: bool


class AiOptimizationChatGptLlmScraper(BaseEndpoint):
    LIVE_URL = f"{_PATH}/live/advanced"
    POST_URL = f"{_PATH}/task_post"
    READY_URL = f"{_PATH}/tasks_ready"
    GET_URL = f"{_PATH}/task_get/advanced"
    LANGUAGES_URL = f"{_PATH}/languages"
    TABLE_NAME = "ai_optimization-chat_gpt-llm_scraper"
    COLLECTOR_ENDPOINT_KEY = "ai_optimization_chat_gpt_llm_scraper"

    TASKS_PER_POST = 100
    # Rows are few but each is paid work, so save often: a crash loses at most this many.
    DEFAULT_UPLOAD_BATCH_ROWS = 200
    LIVE_TIMEOUT_S = 180
    MAX_LIVE_WORKERS = 25           # DataForSEO allows 30 simultaneous requests per account
    DEFAULT_POLL_S = 10.0
    MIN_POLL_S = 4.0                # tasks_ready allows 20 calls a minute
    READY_PAGE = 1000
    POST_WORKERS = 5
    GET_WORKERS = 8

    def __init__(self, client) -> None:
        super().__init__(client)
        self._prompts: dict[str, str] = {}
        self._languages: set[str] | None = None
        self._sleep = time.sleep

    @property
    def location_flag(self) -> str | None:
        return "in_llm_scraper"

    @property
    def rejected_prompts(self) -> list[str]:
        """Prompts refused in the most recent run, locally or by DataForSEO."""
        return list(self.rejected_keywords)

    # ----- BaseEndpoint contract -----

    def _get_schema(self) -> list[str]:
        return list(SCHEMA)

    def _get_dedupe_keys(self) -> list[str]:
        return ["task_id"]

    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        prompt = normalize_prompt(target)
        opts = self._options(**{k: kwargs.get(k) for k in (
            "location_code", "language_code", "force_web_search", "priority",
            "store_answer")})
        return [self._task(prompt, _Spec(fanout_prompt_id(prompt), 1, 0,
                                         opts.force_web_search), opts, standard=False)]

    def _parse_response(self, response: dict, target=None) -> pd.DataFrame:
        """One row per task. `target` is ignored: the collector passes the tag, and the
        prompt comes from this run's own map or, failing that, the request echo."""
        rows = []
        for task in (response or {}).get("tasks") or []:
            prompt_id, _, _ = parse_tag(((task or {}).get("data") or {}).get("tag"))
            rows.append(parse_task(task, prompt=self._prompts.get(prompt_id)))
        return self._frame(rows)

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        for col in _INT_COLS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        if "force_web_search" in df.columns:
            df["force_web_search"] = df["force_web_search"].astype("boolean")
        if "answered_at" in df.columns:
            df["answered_at"] = pd.to_datetime(df["answered_at"], utc=True, errors="coerce")
        typed = set(_INT_COLS) | {"force_web_search", "answered_at"}
        for col in [*SCHEMA, "task_id"]:
            if col in df.columns and col not in typed:
                df[col] = df[col].astype("string")
        return df

    def _fetch_live(self, target: str, **kwargs) -> pd.DataFrame:
        prompt = normalize_prompt(target)
        opts = self._options(**{k: kwargs.get(k) for k in (
            "location_code", "language_code", "force_web_search", "store_answer")})
        spec = _Spec(fanout_prompt_id(prompt), 1, 0, opts.force_web_search)
        return self._frame([self._live_call(prompt, spec, opts, None)])

    def plan(self, targets, *, endpoint_mode: str = "live", samples: int = 1,
             retry_empty: int = 0, priority: int = 1, force_web_search: bool = True,
             location_code: int | None = None, **kwargs):
        """Requests, billable prompts and rows for a run.

        High priority bills double on the queue, expressed as a doubled item count.
        Retries are priced in only when web search is not forced: forced runs returned
        fan-out 207 times out of 207, unforced ones came back empty about a quarter of
        the time, and pricing a retry for every task would double every estimate.
        """
        tasks = len(targets) * max(int(samples or 1), 1)
        retries = tasks * max(int(retry_empty or 0), 0)
        billed = tasks + (0 if force_web_search else retries)
        standard = endpoint_mode == "standard"
        multiplier = 2 if standard and priority == 2 else 1
        requests_ = math.ceil(billed / self.TASKS_PER_POST) if standard else billed
        return self._make_plan(
            targets, endpoint_mode, requests=requests_, items=billed * multiplier,
            max_rows=tasks + retries, location_code=location_code, samples=samples,
            priority=priority if standard else None)

    # ----- Public: queue mode -----

    async def post_all(self, prompts, **kwargs):
        """Queue mode for a list of prompts. See `post` for arguments and return value."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self.post, prompts, **kwargs))

    def post(
        self,
        prompts,
        *,
        job_id: str,
        location_code: int | None = None,
        language_code: str | None = None,
        force_web_search: bool = True,
        samples: int = 1,
        retry_empty: int | None = None,
        priority: int = 1,
        store_answer: bool = True,
        use_collector: bool | None = None,
        proceed_at_pct: float = 1.0,
        poll_interval: float | None = None,
        upload: bool = True,
        balance_buffer: float = DEFAULT_BALANCE_BUFFER,
        ignore_balance_check: bool = False,
        ignore_location_check: bool = False,
        upload_batch_rows: int | None = None,
        **unsupported,
    ):
        """Submit prompts to the queue and collect their fan-out.

        Self-contained (default): returns a DataFrame with one row per task. Results are
        saved as they arrive; on timeout what was collected is kept and
        `IncompleteTaskError` names the task ids still outstanding, which `collect()` can
        fetch later at no cost.

        Collector mode (`use_collector=True` or `config.use_collector`): submits, records
        tracking rows and waits on the collector; returns its receipt dict. The collector
        cannot resubmit, so `retry_empty` must be 0 there.

        `retry_empty` (default 1, or 0 in collector mode) resubmits a run that came back
        with no fan-out, with web search forced.
        """
        self._refuse_domain(unsupported)
        use_collector = self.config.use_collector if use_collector is None else use_collector
        if retry_empty is None:
            retry_empty = 0 if use_collector else 1
        if use_collector and retry_empty:
            raise ValueError(
                "retry_empty is not available with use_collector=True: the collector only "
                "collects results and cannot resubmit a prompt. Pass retry_empty=0, or run "
                "self-contained (use_collector=False).")
        opts = self._options(location_code, language_code, force_web_search, samples,
                             retry_empty, priority, store_answer)
        run, accepted, rejected = self._begin(
            prompts, opts, job_id=job_id, endpoint_mode="standard",
            upload=upload and not use_collector, check_table=upload,
            balance_buffer=balance_buffer, ignore_balance_check=ignore_balance_check,
            ignore_location_check=ignore_location_check, upload_batch_rows=upload_batch_rows,
            tag_cost_with_upload=False)
        try:
            if use_collector:
                summary = self._post_collector(run, accepted, opts, job_id, proceed_at_pct)
                summary["rejected_prompts"] = [p for _, p, _ in rejected]
            else:
                self._run_queue(run, accepted, opts, poll_interval)
        except BaseException as exc:
            run.close(error=exc)
            raise
        frame = run.close(quiet=True)
        return summary if use_collector else frame

    def collect(self, task_ids, *, job_id: str, upload: bool = True,
                upload_batch_rows: int | None = None) -> pd.DataFrame:
        """Fetch queue tasks that were already submitted (for example the ones an
        `IncompleteTaskError` named). Costs nothing; results stay at DataForSEO for 30 days.
        Tasks still in the queue are skipped and reported."""
        _validate_job_id(job_id)
        self._require_table(upload)
        self._prompts = {}
        self.rejected_keywords = []
        run = self._start_run(
            [], job_id=job_id, resolved=None, endpoint_mode="standard", upload=upload,
            balance_buffer=DEFAULT_BALANCE_BUFFER, ignore_balance_check=True,
            ignore_location_check=True,
            upload_batch_rows=upload_batch_rows or self.DEFAULT_UPLOAD_BATCH_ROWS,
            plan_kwargs={}, empty_columns=self._empty_columns(), tag_cost_with_upload=False)
        not_ready: list[str] = []
        try:
            with ThreadPoolExecutor(max_workers=self.GET_WORKERS) as pool:
                ids = [str(t) for t in task_ids]
                for tid, raw in zip(ids, pool.map(self._get_task, ids)):
                    task = self._first_task(raw)
                    if task is None or task.get("status_code") in _IN_QUEUE:
                        not_ready.append(tid)
                        continue
                    run.add_rows(self._frame([parse_task(task)]))
        except BaseException as exc:
            run.close(error=exc)
            raise
        if not_ready:
            print(f"[{self.ENDPOINT_KEY}] {len(not_ready)} task(s) not ready or unreachable; "
                  f"try again later: {not_ready[:5]}{' ...' if len(not_ready) > 5 else ''}")
        return run.close(quiet=True)

    # ----- Public: live mode -----

    async def live_all(self, prompts, **kwargs) -> pd.DataFrame:
        """Live mode for a list of prompts. See `live` for arguments."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self.live, prompts, **kwargs))

    def live(
        self,
        prompts,
        *,
        job_id: str,
        location_code: int | None = None,
        language_code: str | None = None,
        force_web_search: bool = True,
        samples: int = 1,
        retry_empty: int = 1,
        store_answer: bool = True,
        max_workers: int = 10,
        upload: bool = True,
        balance_buffer: float = DEFAULT_BALANCE_BUFFER,
        ignore_balance_check: bool = False,
        ignore_location_check: bool = False,
        upload_batch_rows: int | None = None,
        **unsupported,
    ) -> pd.DataFrame:
        """One live request per prompt, up to `max_workers` at once (capped at 25).

        Each call waits up to 180 seconds. A call that times out is NOT sent again, since
        it may already have been billed: the prompt gets a row saying so. Meant for small
        pulls; `post_all` is a third of the price and far better at scale.
        """
        self._refuse_domain(unsupported)
        opts = self._options(location_code, language_code, force_web_search, samples,
                             retry_empty, 1, store_answer)
        run, accepted, _ = self._begin(
            prompts, opts, job_id=job_id, endpoint_mode="live", upload=upload,
            check_table=upload, balance_buffer=balance_buffer,
            ignore_balance_check=ignore_balance_check,
            ignore_location_check=ignore_location_check, upload_batch_rows=upload_batch_rows,
            tag_cost_with_upload=True)
        workers = max(1, min(int(max_workers), self.MAX_LIVE_WORKERS))
        session = self._live_session(workers)
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(run.run_unit, prompt, functools.partial(
                        self._live_prompt, prompt, prompt_id, sample, opts, session))
                    for prompt_id, prompt in accepted
                    for sample in range(1, opts.samples + 1)
                ]
                for future in futures:
                    future.result()
        except BaseException as exc:
            run.close(error=exc)
            raise
        finally:
            if session is not None:
                session.close()
        return run.close()

    # ----- Preparation shared by every mode -----

    @staticmethod
    def _refuse_domain(unsupported: dict) -> None:
        if "domain" in unsupported or "domain_id" in unsupported:
            raise TypeError(
                "The ChatGPT fan-out endpoint takes no domain or domain_id: the same prompt "
                "can serve several clients, so rows are keyed by prompt_id, not by domain. "
                "Link prompts to clients on prompt_id (fanout_prompt_id).")
        if unsupported:
            raise TypeError(f"Unexpected argument(s): {', '.join(sorted(unsupported))}")

    def _options(self, location_code=None, language_code=None, force_web_search=None,
                 samples=None, retry_empty=None, priority=None, store_answer=None) -> _Options:
        samples = 1 if samples is None else int(samples)
        retry_empty = 0 if retry_empty is None else int(retry_empty)
        priority = 1 if priority is None else int(priority)
        if samples < 1:
            raise ValueError("samples must be >= 1")
        if retry_empty < 0:
            raise ValueError("retry_empty must be >= 0")
        if priority not in (1, 2):
            raise ValueError("priority must be 1 (normal) or 2 (high)")
        return _Options(
            location_code=int(location_code or self.config.location_code),
            language_code=str(language_code or self.config.language_code),
            force_web_search=True if force_web_search is None else bool(force_web_search),
            samples=samples, retry_empty=retry_empty, priority=priority,
            store_answer=True if store_answer is None else bool(store_answer))

    def _prepare(self, prompts) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]], int]:
        """(accepted [(prompt_id, prompt)], rejected [(prompt_id, prompt, reason)], duplicates)."""
        accepted: list[tuple[str, str]] = []
        rejected: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        duplicates = 0
        for raw in target_list(prompts):
            prompt = normalize_prompt(raw)
            prompt_id = fanout_prompt_id(prompt)
            if prompt_id in seen:
                duplicates += 1
                continue
            seen.add(prompt_id)
            if not prompt:
                rejected.append((prompt_id, prompt, "Not sent: the prompt is empty."))
            elif len(prompt) > MAX_PROMPT_CHARS or len(escape_prompt(prompt)) > MAX_PROMPT_CHARS:
                rejected.append((prompt_id, prompt, (
                    f"Not sent: the prompt is longer than {MAX_PROMPT_CHARS} characters "
                    f"(each '%' and '+' counts as three).")))
            else:
                accepted.append((prompt_id, prompt))
        return accepted, rejected, duplicates

    def _begin(self, prompts, opts: _Options, *, job_id: str, endpoint_mode: str, upload: bool,
               check_table: bool, balance_buffer: float, ignore_balance_check: bool,
               ignore_location_check: bool, upload_batch_rows: int | None,
               tag_cost_with_upload: bool):
        _validate_job_id(job_id)
        accepted, rejected, duplicates = self._prepare(prompts)
        if duplicates:
            print(f"[{self.ENDPOINT_KEY}] {duplicates} duplicate prompt(s) collapsed.")
        self._prompts = {pid: prompt for pid, prompt in accepted}
        self.rejected_keywords = []
        if not ignore_location_check:
            self._check_language(opts.language_code)
        self._require_table(check_table)
        run = self._start_run(
            [prompt for _, prompt in accepted], job_id=job_id, resolved=None,
            endpoint_mode=endpoint_mode, upload=upload, balance_buffer=balance_buffer,
            ignore_balance_check=ignore_balance_check,
            ignore_location_check=ignore_location_check,
            upload_batch_rows=upload_batch_rows or self.DEFAULT_UPLOAD_BATCH_ROWS,
            plan_kwargs={"samples": opts.samples, "retry_empty": opts.retry_empty,
                         "priority": opts.priority, "force_web_search": opts.force_web_search,
                         "location_code": opts.location_code},
            empty_columns=self._empty_columns(), tag_cost_with_upload=tag_cost_with_upload)
        if rejected:
            print(f"[{self.ENDPOINT_KEY}] {len(rejected)} prompt(s) not sent (empty or too long).")
            self.rejected_keywords.extend(p for _, p, _ in rejected)
            run.rejected_keywords.extend(p for _, p, _ in rejected)
            run.add_rows(self._frame([
                local_row(prompt, pid, attempt=1, message=reason,
                          location_code=opts.location_code, language_code=opts.language_code,
                          force_web_search=opts.force_web_search)
                for pid, prompt, reason in rejected]))
        return run, accepted, rejected

    def _empty_columns(self) -> list[str]:
        return [*SCHEMA, "task_id", "domain_id", "domain", "endpoint_mode"]

    @staticmethod
    def _frame(rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows, columns=[*SCHEMA, "task_id"])

    def _require_table(self, wanted: bool) -> None:
        """Fail before spending when the destination table is missing."""
        bq = self._client.bq_client
        if not wanted or bq is None:
            return
        table = f"{bq.client.project}.{self.DATASET}.{self.TABLE_NAME}"
        try:
            bq.client.get_table(table)
        except Exception as e:
            raise RuntimeError(
                f"[{self.ENDPOINT_KEY}] Destination table {table} is missing or unreadable "
                f"({e!r}). Nothing was sent. Create it with: python "
                f"scripts/create_dfs_fanout_table.py --yes") from e

    def _check_language(self, language_code: str) -> None:
        if self._languages is None:
            resp = self._client._get(f"{self._client.BASE_URL}/{self.LANGUAGES_URL}",
                                     max_retries=1, retry_delay=0)
            task = self._first_task(resp)
            codes = {r.get("language_code") for r in (task or {}).get("result") or []}
            self._languages = {c for c in codes if c}
        if self._languages and language_code not in self._languages:
            raise ValueError(
                f"[{self.ENDPOINT_KEY}] language_code {language_code!r} is not accepted by "
                f"the ChatGPT scraper. Pass ignore_location_check=True to send it anyway.")

    def _task(self, prompt: str, spec: _Spec, opts: _Options, *, standard: bool) -> dict:
        task = {
            "keyword": escape_prompt(prompt),
            "location_code": opts.location_code,
            "language_code": opts.language_code,
            "force_web_search": spec.forced,
            "tag": make_tag(spec.prompt_id, self._attempt(spec, opts), opts.store_answer),
        }
        if standard and opts.priority == 2:
            task["priority"] = 2
        return task

    @staticmethod
    def _attempt(spec: _Spec, opts: _Options) -> int:
        """Samples take 1..samples; each sample's retries get their own block after that."""
        if spec.retry == 0:
            return spec.sample
        return opts.samples + (spec.sample - 1) * opts.retry_empty + spec.retry

    @staticmethod
    def _first_task(resp) -> dict | None:
        if not isinstance(resp, dict):
            return None
        tasks = resp.get("tasks") or []
        return tasks[0] if tasks and isinstance(tasks[0], dict) else None

    def _initial_specs(self, accepted, opts: _Options) -> list[_Spec]:
        return [_Spec(pid, sample, 0, opts.force_web_search)
                for pid, _ in accepted for sample in range(1, opts.samples + 1)]

    @staticmethod
    def _is_empty(row: dict) -> bool:
        return row.get("status_code") == OK_STATUS and not row.get("fan_out_count")

    # ----- Queue mode internals -----

    def _submit(self, run, specs: list[_Spec], opts: _Options, pending: dict,
                lock: threading.Lock) -> None:
        """task_post the specs, 100 per request. Accepted tasks join `pending`; refused
        ones get a row straight away."""
        url = f"{self._client.BASE_URL}/{self.POST_URL}"
        chunks = [specs[i:i + self.TASKS_PER_POST]
                  for i in range(0, len(specs), self.TASKS_PER_POST)]

        def post_chunk(chunk: list[_Spec]) -> None:
            payload = [self._task(self._prompts[s.prompt_id], s, opts, standard=True)
                       for s in chunk]

            def unit() -> pd.DataFrame:
                resp = self._client._post(url, payload)
                tasks = (resp or {}).get("tasks") or []
                rows = []
                for i, spec in enumerate(chunk):
                    task = tasks[i] if i < len(tasks) and isinstance(tasks[i], dict) else {}
                    code, tid = task.get("status_code"), task.get("id")
                    if code == _TASK_CREATED and tid:
                        with lock:
                            pending[tid] = spec
                        continue
                    prompt = self._prompts[spec.prompt_id]
                    message = (task.get("status_message") if task
                               else "task_post failed: no usable response from DataForSEO.")
                    if isinstance(code, int) and 40000 <= code < 50000:
                        self._reject_keywords([prompt], code, message or "", self.ENDPOINT_KEY)
                    rows.append(local_row(
                        prompt, spec.prompt_id, attempt=self._attempt(spec, opts),
                        message=message, status_code=code, task_id=tid,
                        location_code=opts.location_code, language_code=opts.language_code,
                        force_web_search=spec.forced))
                return self._frame(rows)

            run.run_unit([self._prompts[s.prompt_id] for s in chunk], unit, mark_complete=False)

        if len(chunks) == 1:
            post_chunk(chunks[0])
            return
        with ThreadPoolExecutor(max_workers=self.POST_WORKERS) as pool:
            for _ in pool.map(post_chunk, chunks):
                pass

    def _ready_ids(self) -> list[str] | None:
        resp = self._client._get(f"{self._client.BASE_URL}/{self.READY_URL}",
                                 max_retries=1, retry_delay=0)
        if resp is None:
            return None
        return [entry["id"] for task in resp.get("tasks") or []
                for entry in (task or {}).get("result") or [] if entry.get("id")]

    def _get_task(self, task_id: str) -> dict | None:
        return self._client._get(f"{self._client.BASE_URL}/{self.GET_URL}/{task_id}",
                                 max_retries=2, retry_delay=1)

    def _run_queue(self, run, accepted, opts: _Options, poll_interval: float | None) -> None:
        pending: dict[str, _Spec] = {}
        lock = threading.Lock()
        self._submit(run, self._initial_specs(accepted, opts), opts, pending, lock)

        poll = max(self.MIN_POLL_S, float(poll_interval if poll_interval is not None
                                          else min(self.DEFAULT_POLL_S,
                                                   self.config.task_poll_interval)))
        deadline = time.monotonic() + self.config.task_total_timeout
        get_failures: dict[str, int] = {}

        with ThreadPoolExecutor(max_workers=self.GET_WORKERS) as pool:
            while pending and time.monotonic() < deadline:
                ready = self._ready_ids()
                mine = [tid for tid in (ready or []) if tid in pending]
                if not mine and ready is not None and len(ready) >= self.READY_PAGE:
                    # tasks_ready lists at most 1000 uncollected tasks for the whole
                    # account. A full page with none of ours means other work is crowding
                    # us out, so ask for our oldest tasks directly.
                    mine = list(pending)[:self.TASKS_PER_POST]
                retries: list[_Spec] = []
                for tid, raw in zip(mine, pool.map(self._get_task, mine)):
                    spec = pending[tid]
                    prompt = self._prompts[spec.prompt_id]
                    task = self._first_task(raw)
                    if task is None:
                        get_failures[tid] = get_failures.get(tid, 0) + 1
                        if get_failures[tid] < 3:
                            continue
                        row = local_row(
                            prompt, spec.prompt_id, attempt=self._attempt(spec, opts),
                            message="task_get failed three times; collect() can fetch it later.",
                            task_id=tid, location_code=opts.location_code,
                            language_code=opts.language_code, force_web_search=spec.forced)
                    elif task.get("status_code") in _IN_QUEUE:
                        continue
                    else:
                        row = parse_task(task, prompt=prompt)
                        row["prompt_id"] = spec.prompt_id
                        row["attempt"] = self._attempt(spec, opts)
                    del pending[tid]
                    run.run_unit(prompt, lambda r=row: self._frame([r]))
                    if self._is_empty(row) and spec.retry < opts.retry_empty:
                        retries.append(_Spec(spec.prompt_id, spec.sample, spec.retry + 1, True))
                if retries:
                    self._submit(run, retries, opts, pending, lock)
                if pending:
                    self._sleep(poll)

        if pending:
            error = IncompleteTaskError(
                f"[{self.ENDPOINT_KEY}] {len(pending)} task(s) were not ready within "
                f"{self.config.task_total_timeout} seconds. Everything collected so far was "
                f"kept. Fetch the rest later, at no cost, with collect(error.task_ids, "
                f"job_id=...).",
                task_ids=sorted(pending))
            error.prompts = sorted({self._prompts[s.prompt_id] for s in pending.values()})
            raise error

    def _post_collector(self, run, accepted, opts: _Options, job_id: str,
                        proceed_at_pct: float) -> dict:
        url = f"{self._client.BASE_URL}/{self.POST_URL}"
        specs = self._initial_specs(accepted, opts)
        posted: list[dict] = []
        rejected_payment = rejected_other = 0
        for i in range(0, len(specs), self.TASKS_PER_POST):
            chunk = specs[i:i + self.TASKS_PER_POST]
            payload = [self._task(self._prompts[s.prompt_id], s, opts, standard=True)
                       for s in chunk]
            parsed = parse_task_post(self._in_unit(
                run, [self._prompts[s.prompt_id] for s in chunk],
                lambda p=payload: self._client._post(url, p)))
            posted.extend(parsed["posted"])
            rejected_payment += parsed["rejected_payment"]
            rejected_other += parsed["rejected_other"]
        if rejected_payment:
            print(f"[{self.ENDPOINT_KEY}] WARNING: DataForSEO rejected {rejected_payment} "
                  f"task(s) at submit (40200 Payment Required, usually balance). They were "
                  f"not charged and not queued; rerun them later.")
        summary = submit_and_wait(
            bq_client=self._client.bq_client, job_id=job_id,
            endpoint_key=self.COLLECTOR_ENDPOINT_KEY, posted_tasks=posted,
            proceed_at_pct=proceed_at_pct, poll_interval=self.config.task_poll_interval,
            max_wait=self.config.task_total_timeout, domain_id=None, domain=None)
        summary["rejected_payment"] = rejected_payment
        summary["rejected_other"] = rejected_other
        return summary

    # ----- Live mode internals -----

    def _live_session(self, workers: int):
        """A session whose connection pool fits the worker count. The client's own pool
        holds 10; above that, connections would be opened and thrown away per request."""
        base = self._client._session
        if workers <= 10 or not isinstance(base, requests.Session):
            return None
        session = self._client._create_session()
        retry = session.get_adapter("https://").max_retries
        session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=workers,
                                              pool_maxsize=workers))
        return session

    def _live_prompt(self, prompt: str, prompt_id: str, sample: int, opts: _Options,
                     session) -> pd.DataFrame:
        rows = []
        for retry in range(opts.retry_empty + 1):
            spec = _Spec(prompt_id, sample, retry, True if retry else opts.force_web_search)
            row = self._live_call(prompt, spec, opts, session)
            rows.append(row)
            if not self._is_empty(row):
                break
        return self._frame(rows)

    def _live_call(self, prompt: str, spec: _Spec, opts: _Options, session) -> dict:
        url = f"{self._client.BASE_URL}/{self.LIVE_URL}"
        payload = [self._task(prompt, spec, opts, standard=False)]

        def failed(message: str, code=None, task_id=None) -> dict:
            return local_row(prompt, spec.prompt_id, attempt=self._attempt(spec, opts),
                             message=message, status_code=code, task_id=task_id,
                             location_code=opts.location_code,
                             language_code=opts.language_code, force_web_search=spec.forced)

        backoffs = 3
        for i in range(backoffs + 1):
            sink: dict = {}
            resp = self._client._post(url, payload, session=session, max_retries=1,
                                      retry_delay=0, status_sink=sink,
                                      timeout=self.LIVE_TIMEOUT_S)
            if resp is None:
                error = sink.get("error") or ""
                if sink.get("http_status") == 429 and i < backoffs:
                    self._sleep(min(30.0, 2 ** i + random.random()))
                    continue
                if sink.get("http_status") is None and "timeout" in error.lower().replace(" ", ""):
                    # The request may have run and been billed. Sending it again could pay
                    # twice, so record the gap and move on.
                    unit = active_unit()
                    if unit is not None:
                        unit.record_unattributed_billed_retry(url, payload, None, 1)
                    return failed(f"Timeout after {self.LIVE_TIMEOUT_S}s, possibly billed; "
                                  f"not re-sent.")
                return failed(f"Live request failed: {error[:300] or 'no response'}")
            task = self._first_task(resp) or {}
            code = task.get("status_code")
            if code in _RATE_LIMITED and i < backoffs:
                self._sleep(min(30.0, 2 ** i + random.random()))
                continue
            if isinstance(code, int) and 40000 <= code < 50000 and code not in _RATE_LIMITED:
                self._reject_keywords([prompt], code, task.get("status_message") or "",
                                      self.ENDPOINT_KEY)
            row = parse_task(task, prompt=prompt)
            row["prompt_id"] = spec.prompt_id
            row["attempt"] = self._attempt(spec, opts)
            return row
        return failed("Rate limited by DataForSEO; gave up after backing off.")
