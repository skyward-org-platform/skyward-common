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

Money rules this module keeps:
- A request is never sent again unless DataForSEO clearly did not accept it. A timeout or
  dropped connection may already have been billed, so the prompt gets a row saying so.
- Once tasks are submitted they are paid for, so nothing during collection is allowed to
  abandon them: a low balance stops further retries, never the collection, and any error
  that does end the run carries the task ids still to fetch (`collect()` gets them free).
- Retries on an empty result are capped at the number priced into the estimate.
"""

from __future__ import annotations

import asyncio
import functools
import logging
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
from skyward.data.dataforseo.collector.producer import submit_and_wait
from skyward.data.dataforseo.exceptions import IncompleteTaskError, InsufficientBalanceError
from skyward.data.dataforseo.fanout import (
    MAX_PROMPT_CHARS, OK_STATUS, SCHEMA, escape_prompt, fanout_prompt_id, local_row,
    make_tag, normalize_prompt, parse_task,
)
from skyward.data.dataforseo.run import DEFAULT_BALANCE_BUFFER, active_unit, target_list
from skyward.functions import _validate_job_id

logger = logging.getLogger(__name__)

_PATH = "ai_optimization/chat_gpt/llm_scraper"
_TASK_CREATED = 20100
_PAYMENT_REQUIRED = 40200
_RATE_LIMITED = (40202, 40209)      # per-minute and simultaneous-request limits
# task_get answers that mean "ask again later", never a final result.
_NOT_READY = (40601, 40602, *_RATE_LIMITED)
_RESEND_HTTP = (429, 502, 503)      # refused before any work was done: safe to send again
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


class _RetryBudget:
    """How many empty-result retries a run may still pay for. Shared across threads."""

    def __init__(self, allowed: int) -> None:
        self._left = max(int(allowed), 0)
        self._lock = threading.Lock()
        self.exhausted = False

    def take(self) -> bool:
        with self._lock:
            if self._left > 0:
                self._left -= 1
                return True
            self.exhausted = True
            return False

    def close(self) -> None:
        with self._lock:
            self._left = 0


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
    POST_TIMEOUT_S = (15, 60)       # (connect, read)
    LIVE_TIMEOUT_S = (15, 180)
    MAX_LIVE_WORKERS = 25           # DataForSEO allows 30 simultaneous requests per account
    DEFAULT_POLL_S = 10.0
    MIN_POLL_S = 4.0                # tasks_ready allows 20 calls a minute
    # A task not listed by tasks_ready this long after it was posted is asked for directly:
    # something else (the collector, a lost response) may have taken it off the list.
    DIRECT_AFTER_S = 180.0
    POST_WORKERS = 5
    GET_WORKERS = 8
    # Share of forced-search tasks priced for a retry. Forced runs returned fan-out 207
    # times out of 207 in testing, so pricing a retry for every task would double every
    # estimate for nothing; the run never retries more than what was priced.
    FORCED_RETRY_SHARE = 0.10

    def __init__(self, client) -> None:
        super().__init__(client)
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
        """One row per task, from the response alone. This is what the collector calls:
        `target` is ignored and the prompt is rebuilt from the request echo."""
        tasks = (response or {}).get("tasks") if isinstance(response, dict) else None
        return self._frame([self._safe_parse(task) for task in tasks or []])

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        for col in _INT_COLS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        if "force_web_search" in df.columns:
            df["force_web_search"] = df["force_web_search"].astype("boolean")
        if "answered_at" in df.columns:
            # "mixed": one odd-format value must not blank the rest of the save window.
            df["answered_at"] = pd.to_datetime(df["answered_at"], utc=True, errors="coerce",
                                               format="mixed")
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

    def _retries_priced(self, tasks: int, retry_empty: int, force_web_search: bool) -> int:
        """How many empty-result retries are priced into the estimate, and so allowed."""
        retries = tasks * max(int(retry_empty or 0), 0)
        if not retries or not force_web_search:
            return retries
        return min(retries, math.ceil(tasks * self.FORCED_RETRY_SHARE))

    def plan(self, targets, *, endpoint_mode: str = "live", samples: int = 1,
             retry_empty: int = 0, priority: int = 1, force_web_search: bool = True,
             location_code: int | None = None, **kwargs):
        """Requests, billable prompts and rows for a run.

        High priority bills double on the queue, expressed as a doubled item count. The
        retries a run is allowed are priced in, so spend cannot pass the estimate: all of
        them when web search is not forced (about a quarter of such runs come back empty),
        a tenth of the tasks when it is.
        """
        tasks = len(targets) * max(int(samples or 1), 1)
        billed = tasks + self._retries_priced(tasks, retry_empty, force_web_search)
        standard = endpoint_mode == "standard"
        multiplier = 2 if standard and priority == 2 else 1
        requests_ = math.ceil(billed / self.TASKS_PER_POST) if standard else billed
        return self._make_plan(
            targets, endpoint_mode, requests=requests_, items=billed * multiplier,
            max_rows=billed, location_code=location_code, samples=samples,
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
        saved as they arrive. If the run cannot finish (timeout, low balance, any error
        after submission), what was collected is kept and the exception carries
        `task_ids` (the tasks still to fetch, which `collect()` gets at no cost) and
        `rows` (the DataFrame collected so far).

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
        # In collector mode the run still saves the rows it makes itself (prompts that
        # were never sent or were refused at submit); the collector saves the results.
        run, prompts_by_id, rejected = self._begin(
            prompts, opts, job_id=job_id, endpoint_mode="standard", upload=upload,
            balance_buffer=balance_buffer, ignore_balance_check=ignore_balance_check,
            ignore_location_check=ignore_location_check, upload_batch_rows=upload_batch_rows,
            tag_cost_with_upload=False)
        try:
            if use_collector:
                summary = self._post_collector(run, prompts_by_id, opts, job_id, proceed_at_pct)
                summary["rejected_prompts"] = self.rejected_prompts
            else:
                self._run_queue(run, prompts_by_id, opts, poll_interval)
        except BaseException as exc:
            self._close_with(run, exc)
            raise
        frame = run.close(quiet=True)
        return summary if use_collector else frame

    def collect(self, task_ids, *, job_id: str, upload: bool = True,
                upload_batch_rows: int | None = None) -> pd.DataFrame:
        """Fetch queue tasks that were already submitted (for example the `task_ids` an
        exception from `post` carried). Costs nothing; results stay at DataForSEO for 30
        days. Tasks that are not ready yet are skipped and reported; the returned frame's
        `attrs["not_ready"]` lists them."""
        _validate_job_id(job_id)
        self._require_table(upload)
        run = self._start_run(
            [], job_id=job_id, resolved=None, endpoint_mode="standard", upload=upload,
            balance_buffer=DEFAULT_BALANCE_BUFFER, ignore_balance_check=True,
            ignore_location_check=True,
            upload_batch_rows=upload_batch_rows or self.DEFAULT_UPLOAD_BATCH_ROWS,
            plan_kwargs={}, empty_columns=self._empty_columns(), tag_cost_with_upload=False)
        not_ready: list[str] = []
        try:
            with ThreadPoolExecutor(max_workers=self.GET_WORKERS) as pool:
                ids = list(dict.fromkeys(str(t) for t in task_ids))
                for tid, raw in zip(ids, pool.map(self._get_task, ids)):
                    task = self._first_task(raw)
                    if self._not_ready(task, listed=False):
                        not_ready.append(tid)
                        continue
                    run.add_rows(self._frame([self._safe_parse(task)]))
        except BaseException as exc:
            self._close_with(run, exc)
            raise
        if not_ready:
            print(f"[{self.ENDPOINT_KEY}] {len(not_ready)} task(s) not ready or unreachable; "
                  f"try again later: {not_ready[:5]}{' ...' if len(not_ready) > 5 else ''}")
        frame = run.close(quiet=True)
        frame.attrs["not_ready"] = not_ready
        return frame

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

        Each call waits up to 180 seconds. A call that gets no answer (timeout, dropped
        connection) is NOT sent again, since it may already have been billed: the prompt
        gets a row saying so. Meant for small pulls; `post_all` is a third of the price
        and far better at scale.
        """
        self._refuse_domain(unsupported)
        opts = self._options(location_code, language_code, force_web_search, samples,
                             retry_empty, 1, store_answer)
        run, prompts_by_id, _ = self._begin(
            prompts, opts, job_id=job_id, endpoint_mode="live", upload=upload,
            balance_buffer=balance_buffer, ignore_balance_check=ignore_balance_check,
            ignore_location_check=ignore_location_check, upload_batch_rows=upload_batch_rows,
            tag_cost_with_upload=True)
        budget = self._budget(prompts_by_id, opts)
        workers = max(1, min(int(max_workers), self.MAX_LIVE_WORKERS))
        session = self._live_session(workers)
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(run.run_unit, prompt, functools.partial(
                        self._live_prompt, prompt, prompt_id, sample, opts, session, budget))
                    for prompt_id, prompt in prompts_by_id.items()
                    for sample in range(1, opts.samples + 1)
                ]
                first_error: BaseException | None = None
                for future in futures:
                    try:
                        future.result()
                    except BaseException as exc:  # noqa: BLE001 - let the others finish
                        first_error = first_error or exc
                if first_error is not None:
                    raise first_error
        except BaseException as exc:
            self._close_with(run, exc)
            raise
        finally:
            if session is not None:
                session.close()
        self._note_budget(budget)
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
               balance_buffer: float, ignore_balance_check: bool,
               ignore_location_check: bool, upload_batch_rows: int | None,
               tag_cost_with_upload: bool):
        """Validate, start the run and write rows for prompts that will not be sent.

        Returns (run, {prompt_id: prompt} for the prompts to send, rejected). The prompt
        map belongs to this run alone: the endpoint object is shared by every run on the
        client, so nothing about a run is kept on it.
        """
        _validate_job_id(job_id)
        accepted, rejected, duplicates = self._prepare(prompts)
        if duplicates:
            print(f"[{self.ENDPOINT_KEY}] {duplicates} duplicate prompt(s) collapsed.")
        prompts_by_id = dict(accepted)
        self.rejected_keywords = []
        if not ignore_location_check:
            self._check_language(opts.language_code)
        self._require_table(upload)
        run = self._start_run(
            list(prompts_by_id.values()), job_id=job_id, resolved=None,
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
            self._keep(run, self._frame([
                local_row(prompt, pid, attempt=1, message=reason,
                          location_code=opts.location_code, language_code=opts.language_code,
                          force_web_search=opts.force_web_search)
                for pid, prompt, reason in rejected]))
        return run, prompts_by_id, rejected

    def _budget(self, prompts_by_id: dict, opts: _Options) -> _RetryBudget:
        tasks = len(prompts_by_id) * opts.samples
        return _RetryBudget(self._retries_priced(tasks, opts.retry_empty, opts.force_web_search))

    def _note_budget(self, budget: _RetryBudget) -> None:
        if budget.exhausted:
            print(f"[{self.ENDPOINT_KEY}] Some empty results were not retried: the retries "
                  f"priced into this run's estimate were used up (or the balance ran low). "
                  f"Their rows have fan_out_count NULL; rerun those prompts to try again.")

    @staticmethod
    def _close_with(run, exc: BaseException) -> None:
        """Close a failed run and hand the caller whatever was collected."""
        try:
            exc.rows = run.close(error=exc)
        except Exception as close_exc:  # noqa: BLE001 - the original error is what matters
            logger.error("close() failed while handling %r: %r", exc, close_exc)

    @staticmethod
    def _keep(run, df: pd.DataFrame) -> InsufficientBalanceError | None:
        """Hand rows to the run. A low balance noticed while saving is returned, not
        raised: the rows are already saved, and the caller decides what to stop."""
        try:
            run.add_rows(df)
        except InsufficientBalanceError as exc:
            return exc
        return None

    def _empty_columns(self) -> list[str]:
        return [*SCHEMA, "task_id", "domain_id", "domain", "endpoint_mode"]

    @staticmethod
    def _frame(rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows, columns=[*SCHEMA, "task_id"])

    def _safe_parse(self, task, prompt: str | None = None) -> dict:
        """parse_task, except a response it cannot make sense of becomes a row, not an
        exception: the task was paid for and must not take the run down with it."""
        try:
            return parse_task(task, prompt=prompt)
        except Exception as e:  # noqa: BLE001
            logger.error("[%s] could not parse task %r: %r", self.ENDPOINT_KEY,
                         (task or {}).get("id") if isinstance(task, dict) else None, e)
            data = (task.get("data") or {}) if isinstance(task, dict) else {}
            code = task.get("status_code") if isinstance(task, dict) else None
            # status_code stays NULL: an OK code here would read as "answered, no
            # fan-out" and trigger a paid retry of a task that did answer.
            return local_row(
                prompt, fanout_prompt_id(prompt) if prompt is not None else None, attempt=1,
                message=(f"Response (task status {code}) could not be parsed ({e!r}); "
                         f"collect() can fetch it again."),
                task_id=task.get("id") if isinstance(task, dict) else None,
                location_code=data.get("location_code"),
                language_code=data.get("language_code"),
                force_web_search=data.get("force_web_search"))

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
        languages = self._languages
        if languages is None:
            resp = self._client._get(f"{self._client.BASE_URL}/{self.LANGUAGES_URL}",
                                     max_retries=1, retry_delay=0)
            task = self._first_task(resp)
            codes = {r.get("language_code") for r in (task or {}).get("result") or []
                     if isinstance(r, dict)}
            languages = {c for c in codes if c}
            if not languages:
                return          # could not read the list: do not block, and ask again next time
            self._languages = languages
        if language_code not in languages:
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

    @staticmethod
    def _not_ready(task: dict | None, *, listed: bool) -> bool:
        """Whether a task_get answer means "ask again later".

        `listed` is whether tasks_ready named the task. For a task we asked about on our
        own, an OK status with no result yet is also "not ready": treating it as an empty
        answer would trigger a paid retry while the original is still running.
        """
        if task is None:
            return True
        code = task.get("status_code")
        if code in _NOT_READY:
            return True
        return not listed and code == OK_STATUS and not task.get("result")

    def _initial_specs(self, prompts_by_id: dict, opts: _Options) -> list[_Spec]:
        return [_Spec(pid, sample, 0, opts.force_web_search)
                for pid in prompts_by_id for sample in range(1, opts.samples + 1)]

    @staticmethod
    def _is_empty(row: dict) -> bool:
        return row.get("status_code") == OK_STATUS and not row.get("fan_out_count")

    def _send(self, url: str, payload: list, *, session=None, timeout) -> tuple[dict | None, str]:
        """POST a billable request, sending it again only when DataForSEO clearly did not
        take it (429, 502, 503). Returns (response, "") or (None, why).

        `why` starts with "possibly billed" when the request may have been accepted: no
        response at all (timeout, dropped connection) or one whose body could not be
        used. Those are never sent again, and a zero-cost marker row goes to the cost log
        so the gap is visible.
        """
        resends = 3
        for i in range(resends + 1):
            sink: dict = {}
            resp = self._client._post(url, payload, session=session, max_retries=1,
                                      retry_delay=0, status_sink=sink, timeout=timeout)
            if resp is not None:
                return resp, ""
            status = sink.get("http_status")
            if status in _RESEND_HTTP and i < resends:
                self._sleep(min(30.0, 2 ** i + random.random()))
                continue
            if status is not None and 400 <= status < 500:
                return None, f"refused, HTTP {status}"
            if status is None or status >= 500:
                # `_post` writes its own marker for a 2xx it could not use; a request
                # that got no response, or a server error after it may have started,
                # needs one from here.
                unit = active_unit()
                if unit is not None:
                    unit.record_unattributed_billed_retry(url, payload, None, 1)
            detail = (sink.get("error") or f"HTTP {status}")[:200]
            return None, f"possibly billed: {detail}"
        return None, "refused, rate limited"

    @staticmethod
    def _no_response_message(why: str, what: str) -> str:
        if why.startswith("possibly billed"):
            return (f"No usable response to the {what} ({why[17:]}). It may have been "
                    f"accepted and billed, so it was not re-sent.")
        return f"The {what} was {why}; nothing was billed."

    # ----- Queue mode internals -----

    def _submit(self, run, specs: list[_Spec], prompts_by_id: dict, opts: _Options,
                pending: dict) -> InsufficientBalanceError | None:
        """task_post the specs, 100 per request, each request sent once. Accepted tasks
        join `pending` as task_id -> (spec, posted_at); the rest get a row straight away."""
        url = f"{self._client.BASE_URL}/{self.POST_URL}"
        chunks = [specs[i:i + self.TASKS_PER_POST]
                  for i in range(0, len(specs), self.TASKS_PER_POST)]
        lock = threading.Lock()

        def post_chunk(chunk: list[_Spec]) -> pd.DataFrame:
            payload = [self._task(prompts_by_id[s.prompt_id], s, opts, standard=True)
                       for s in chunk]
            resp, why = self._send(url, payload, timeout=self.POST_TIMEOUT_S)
            tasks = (resp or {}).get("tasks") or []
            now = time.monotonic()
            rows = []
            for i, spec in enumerate(chunk):
                task = tasks[i] if i < len(tasks) and isinstance(tasks[i], dict) else {}
                code, tid = task.get("status_code"), task.get("id")
                if code == _TASK_CREATED and tid:
                    with lock:
                        pending[tid] = (spec, now)
                    continue
                prompt = prompts_by_id[spec.prompt_id]
                message = task.get("status_message") if task else self._no_response_message(
                    why or "refused, empty response", "task_post")
                if isinstance(code, int) and 40000 <= code < 50000:
                    self._reject_keywords([prompt], code, message or "", self.ENDPOINT_KEY)
                rows.append(local_row(
                    prompt, spec.prompt_id, attempt=self._attempt(spec, opts),
                    message=message, status_code=code, task_id=tid,
                    location_code=opts.location_code, language_code=opts.language_code,
                    force_web_search=spec.forced))
            return self._frame(rows)

        def unit(chunk: list[_Spec]) -> pd.DataFrame | None:
            # The unit returns nothing so that saving these rows cannot be what raises
            # out of it: the rows go through _keep below instead.
            box: list[pd.DataFrame] = []
            run.run_unit([prompts_by_id[s.prompt_id] for s in chunk],
                         lambda: box.append(post_chunk(chunk)), mark_complete=False)
            return box[0] if box else None

        if len(chunks) == 1:
            frames = [unit(chunks[0])]
        else:
            with ThreadPoolExecutor(max_workers=self.POST_WORKERS) as pool:
                frames = list(pool.map(unit, chunks))
        low_balance = None
        for frame in frames:
            if frame is not None and not frame.empty:
                low_balance = self._keep(run, frame) or low_balance
        return low_balance

    def _ready_ids(self) -> list[str] | None:
        resp = self._client._get(f"{self._client.BASE_URL}/{self.READY_URL}",
                                 max_retries=1, retry_delay=0)
        if resp is None:
            return None
        return [entry["id"] for task in resp.get("tasks") or []
                for entry in (task or {}).get("result") or []
                if isinstance(entry, dict) and entry.get("id")]

    def _get_task(self, task_id: str) -> dict | None:
        return self._client._get(f"{self._client.BASE_URL}/{self.GET_URL}/{task_id}",
                                 max_retries=2, retry_delay=1)

    def _run_queue(self, run, prompts_by_id: dict, opts: _Options,
                   poll_interval: float | None) -> None:
        pending: dict[str, tuple[_Spec, float]] = {}
        budget = self._budget(prompts_by_id, opts)
        try:
            low_balance = self._submit(run, self._initial_specs(prompts_by_id, opts),
                                       prompts_by_id, opts, pending)
            low_balance = self._collect_pending(run, prompts_by_id, opts, pending, budget,
                                                poll_interval, low_balance)
        except BaseException as exc:
            # Whatever went wrong, the tasks still in `pending` are paid for. Name them.
            if pending and not getattr(exc, "task_ids", None):
                exc.task_ids = sorted(pending)
                exc.prompts = sorted({prompts_by_id[s.prompt_id] for s, _ in pending.values()})
            raise
        self._note_budget(budget)
        if pending:
            error = IncompleteTaskError(
                f"[{self.ENDPOINT_KEY}] {len(pending)} task(s) were not ready within "
                f"{self.config.task_total_timeout} seconds. What was collected is in "
                f"error.rows (and saved, if upload was on). Fetch the rest later, at no "
                f"cost, with collect(error.task_ids, job_id=...).",
                task_ids=sorted(pending))
            error.prompts = sorted({prompts_by_id[s.prompt_id] for s, _ in pending.values()})
            raise error
        if low_balance is not None:
            # Everything submitted was collected and saved; report the stop last.
            raise low_balance

    def _collect_pending(self, run, prompts_by_id: dict, opts: _Options, pending: dict,
                         budget: _RetryBudget, poll_interval: float | None,
                         low_balance) -> InsufficientBalanceError | None:
        poll = max(self.MIN_POLL_S, float(poll_interval if poll_interval is not None
                                          else min(self.DEFAULT_POLL_S,
                                                   self.config.task_poll_interval)))
        deadline = time.monotonic() + self.config.task_total_timeout
        get_failures: dict[str, int] = {}
        asked_directly: dict[str, float] = {}

        with ThreadPoolExecutor(max_workers=self.GET_WORKERS) as pool:
            while pending and time.monotonic() < deadline:
                if low_balance is not None:
                    budget.close()          # no more spending; keep collecting what is paid
                ready = set(self._ready_ids() or [])
                listed = [tid for tid in pending if tid in ready]
                now = time.monotonic()
                overdue = [tid for tid, (_, posted) in pending.items()
                           if tid not in ready
                           and now - max(posted, asked_directly.get(tid, 0.0)) >= self.DIRECT_AFTER_S]
                overdue = overdue[:self.TASKS_PER_POST]
                for tid in overdue:
                    asked_directly[tid] = now
                batch = [*listed, *overdue]
                retries: list[_Spec] = []
                for tid, raw in zip(batch, pool.map(self._get_task, batch)):
                    spec, _ = pending[tid]
                    prompt = prompts_by_id[spec.prompt_id]
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
                    elif self._not_ready(task, listed=tid in ready):
                        continue
                    else:
                        row = self._safe_parse(task, prompt)
                        row["prompt_id"] = spec.prompt_id
                        row["attempt"] = self._attempt(spec, opts)
                    low_balance = self._keep(run, self._frame([row])) or low_balance
                    del pending[tid]
                    if (self._is_empty(row) and spec.retry < opts.retry_empty
                            and low_balance is None and budget.take()):
                        retries.append(_Spec(spec.prompt_id, spec.sample, spec.retry + 1, True))
                if retries:
                    low_balance = self._submit(run, retries, prompts_by_id, opts,
                                               pending) or low_balance
                if pending:
                    self._sleep(poll)
        return low_balance

    def _post_collector(self, run, prompts_by_id: dict, opts: _Options, job_id: str,
                        proceed_at_pct: float) -> dict:
        pending: dict[str, tuple[_Spec, float]] = {}
        specs = self._initial_specs(prompts_by_id, opts)
        self._submit(run, specs, prompts_by_id, opts, pending)
        posted = [{"task_id": tid,
                   "keyword": make_tag(spec.prompt_id, self._attempt(spec, opts),
                                       opts.store_answer)}
                  for tid, (spec, _) in pending.items()]
        not_posted = len(specs) - len(posted)
        if not_posted:
            print(f"[{self.ENDPOINT_KEY}] WARNING: {not_posted} of {len(specs)} task(s) were "
                  f"not accepted at submit (refused by DataForSEO, or no response). Each has "
                  f"a row in {self.TABLE_NAME} with the reason; they are not being tracked.")
        summary = submit_and_wait(
            bq_client=self._client.bq_client, job_id=job_id,
            endpoint_key=self.COLLECTOR_ENDPOINT_KEY, posted_tasks=posted,
            proceed_at_pct=proceed_at_pct, poll_interval=self.config.task_poll_interval,
            max_wait=self.config.task_total_timeout, domain_id=None, domain=None)
        summary["not_posted"] = not_posted
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
                     session, budget: _RetryBudget) -> pd.DataFrame:
        rows = []
        for retry in range(opts.retry_empty + 1):
            spec = _Spec(prompt_id, sample, retry, True if retry else opts.force_web_search)
            row = self._live_call(prompt, spec, opts, session)
            rows.append(row)
            if not self._is_empty(row) or retry == opts.retry_empty or not budget.take():
                break
        return self._frame(rows)

    def _live_call(self, prompt: str, spec: _Spec, opts: _Options, session) -> dict:
        url = f"{self._client.BASE_URL}/{self.LIVE_URL}"
        payload = [self._task(prompt, spec, opts, standard=False)]
        backoffs = 3
        for i in range(backoffs + 1):
            resp, why = self._send(url, payload, session=session, timeout=self.LIVE_TIMEOUT_S)
            task = self._first_task(resp)
            if task is None:
                return local_row(
                    prompt, spec.prompt_id, attempt=self._attempt(spec, opts),
                    message=self._no_response_message(why or "refused, empty response",
                                                      "live request"),
                    location_code=opts.location_code, language_code=opts.language_code,
                    force_web_search=spec.forced)
            code = task.get("status_code")
            if code in _RATE_LIMITED and i < backoffs:
                # A rate-limit answer is DataForSEO declining to start: not billed.
                self._sleep(min(30.0, 2 ** i + random.random()))
                continue
            if isinstance(code, int) and 40000 <= code < 50000 and code not in _RATE_LIMITED:
                self._reject_keywords([prompt], code, task.get("status_message") or "",
                                      self.ENDPOINT_KEY)
            row = self._safe_parse(task, prompt)
            row["prompt_id"] = spec.prompt_id
            row["attempt"] = self._attempt(spec, opts)
            return row
