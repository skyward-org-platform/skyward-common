"""serp_google_organic — v3/serp/google/organic/live/advanced

Fetches SERP results for a keyword. Supports both live and POST/GET workflows.
"""

from __future__ import annotations

import json
import time
from queue import Queue, Empty
from threading import Thread, Lock
from typing import Any

import pandas as pd
import requests

from skyward.data.dataforseo.base import _UNSET, BaseEndpoint
from skyward.functions import _validate_job_id


class SerpGoogleOrganic(BaseEndpoint):
    LIVE_URL = "serp/google/organic/live/advanced"
    POST_URL = "serp/google/organic/task_post"
    READY_URL = "serp/google/organic/tasks_ready"
    GET_URL = "serp/google/organic/task_get/advanced"
    FIXED_URL = "serp/google/organic/tasks_fixed"
    ENDPOINT_BASE = "serp/google/organic"
    TABLE_NAME = "serp-google-organic"

    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        payload = {
            "keyword": target,
            "location_code": kwargs.get("location_code", self.config.location_code),
            "language_code": kwargs.get("language_code", self.config.language_code),
        }
        if kwargs.get("tag"):
            payload["tag"] = kwargs["tag"]
        return [payload]

    def _parse_response(self, response: dict, target: str) -> pd.DataFrame:
        try:
            task = response["tasks"][0]
            result = task["result"][0]
            items = result["items"]
            if items is None:
                return pd.DataFrame(columns=self._get_schema())
        except (KeyError, IndexError, TypeError):
            return pd.DataFrame(columns=self._get_schema())

        task_id = task.get("id")
        serp_datetime = result.get("datetime")
        se_domain = result.get("se_domain")
        se_results_count = result.get("se_results_count")
        check_url = result.get("check_url")
        item_types = result.get("item_types")
        refinement_chips = result.get("refinement_chips")

        data_dict = task.get("data", {})
        location_code_val = data_dict.get("location_code")
        language_code_val = data_dict.get("language_code")
        device = data_dict.get("device")
        os_val = data_dict.get("os")

        rows = []
        for item in items:
            rows.append({
                "task_id": task_id,
                "keyword": target,
                "serp_datetime": serp_datetime,
                "se_domain": se_domain,
                "location_code": location_code_val,
                "language_code": language_code_val,
                "device": device,
                "os": os_val,
                "se_results_count": se_results_count,
                "check_url": check_url,
                "item_types": item_types,
                "refinement_chips": refinement_chips,
                "item_type": item.get("type"),
                "rank_group": item.get("rank_group"),
                "rank_absolute": item.get("rank_absolute"),
                "page": item.get("page"),
                "position": item.get("position"),
                "data": {k: v for k, v in result.items() if k != "items"},
                "item": item,
            })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return [
            "task_id", "keyword", "serp_datetime", "se_domain",
            "location_code", "language_code", "device", "os",
            "se_results_count", "check_url", "item_types", "refinement_chips",
            "item_type", "rank_group", "rank_absolute",
            "page", "position", "data", "item",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["task_id", "rank_absolute", "item_type"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = ["location_code", "se_results_count", "rank_group", "rank_absolute", "page"]
        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

        if "serp_datetime" in df.columns:
            df["serp_datetime"] = pd.to_datetime(df["serp_datetime"], utc=True)

        for col in ["data", "item", "item_types", "refinement_chips"]:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: json.dumps(x) if isinstance(x, (dict, list)) else x)

        return df

    def _fetch_live(self, target: str, **kwargs) -> pd.DataFrame:
        cfg = self.config
        max_retries = kwargs.pop("max_retries", cfg.max_retries)
        retry_delay = kwargs.pop("retry_delay", cfg.retry_delay)
        debug = kwargs.pop("debug", cfg.debug)

        url = f"{self._client.BASE_URL}/{self.LIVE_URL}"
        payload = self._build_payload(target, **kwargs)

        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                time.sleep(retry_delay)
            resp = self._client._post(url, payload, max_retries=1, retry_delay=0)
            if not resp:
                if debug:
                    print(f"[{target}] Invalid response. Attempt {attempt}/{max_retries}")
                continue
            try:
                df = self._parse_response(resp, target)
                if not df.empty:
                    return df
            except Exception as e:
                if debug:
                    print(f"[{target}] Parse error: {e}. Attempt {attempt}/{max_retries}")
                continue

        return pd.DataFrame(columns=self._get_schema())

    # -------------------------------------------------------------------------
    # POST/GET workflow methods
    # -------------------------------------------------------------------------

    def _task_post(
        self,
        keywords: list[str],
        location_code: int | None = None,
        language_code: str | None = None,
        debug: bool | None = None,
    ) -> list[dict]:
        """POST up to 100 keywords to task_post. Returns [{"id": task_id, "keyword": kw}]."""
        if len(keywords) > 100:
            raise ValueError("Maximum 100 keywords per request")

        location_code = location_code or self.config.location_code
        language_code = language_code or self.config.language_code
        debug = debug if debug is not None else self.config.debug

        url = f"{self._client.BASE_URL}/{self.POST_URL}"
        payload = [
            {"keyword": kw, "location_code": location_code, "language_code": language_code, "tag": kw}
            for kw in keywords
        ]

        resp = self._client._post(url, payload)
        if not resp:
            if debug:
                print("Failed to post tasks")
            return []

        tasks = []
        for task in resp.get("tasks", []):
            task_id = task.get("id")
            status = task.get("status_code")
            tag = task.get("data", {}).get("tag", "")
            if status == 20100 and task_id and tag:
                tasks.append({"id": task_id, "keyword": tag})
            elif debug:
                print(f"Task failed for '{tag}': {task.get('status_message')}")

        if debug:
            print(f"Posted {len(tasks)} tasks successfully")
        return tasks

    def _task_get(
        self,
        task_id: str,
        keyword: str,
        session: requests.Session | None = None,
    ) -> tuple[pd.DataFrame | None, str | None]:
        url = f"{self._client.BASE_URL}/{self.GET_URL}/{task_id}"
        task_data, error = self._client.get_task_result(url, session)

        if error:
            return (None, error)
        if task_data is None:
            return (None, None)

        response = {"tasks": [task_data]}
        df = self._parse_response(response, keyword)
        return (df, None)

    def post(
        self,
        target,
        *,
        domain: Any = _UNSET,
        domain_id: Any = _UNSET,
        job_id: str,
        interactive: bool = False,
        upload: bool = True,
        location_code: int | None = None,
        language_code: str | None = None,
        max_wait: int = 300,
        debug: bool | None = None,
    ) -> pd.DataFrame:
        """POST/GET workflow for ≤100 keywords."""
        _validate_job_id(job_id)
        resolved = self._resolve_domain(domain, domain_id, interactive)

        keywords = [target] if isinstance(target, str) else list(target)
        if len(keywords) > 100:
            raise ValueError("Maximum 100 keywords. Use post_all() for larger batches.")

        debug = debug if debug is not None else self.config.debug

        empty_cols = self._get_schema() + ["domain_id", "domain", "endpoint_mode"]

        tasks = self._task_post(keywords, location_code, language_code, debug)
        if not tasks:
            return pd.DataFrame(columns=empty_cols)

        task_map = {t["id"]: t["keyword"] for t in tasks}
        pending = set(task_map.keys())
        results = []
        start_time = time.time()

        while pending and (time.time() - start_time) < max_wait:
            for task_id in list(pending):
                keyword = task_map[task_id]
                df, error = self._task_get(task_id, keyword)

                if df is not None:
                    results.append(df)
                    pending.remove(task_id)
                elif error:
                    if debug:
                        print(f"[{keyword}] Failed: {error}")
                    pending.remove(task_id)

            if pending:
                time.sleep(1)

        if pending and debug:
            print(f"Warning: {len(pending)} tasks did not complete within {max_wait}s")

        if not results:
            return pd.DataFrame(columns=empty_cols)

        combined = pd.concat(results, ignore_index=True)
        combined = self._stamp_fetch_metadata(combined, resolved, endpoint_mode="standard")

        if upload:
            self.upload(self._client.bq_client, combined, job_id=job_id)

        return combined

    def post_all(
        self,
        targets: list[str],
        *,
        domain: Any = _UNSET,
        domain_id: Any = _UNSET,
        job_id: str,
        interactive: bool = False,
        upload: bool = True,
        batch_size: int = 100,
        num_workers: int = 10,
        max_wait: int = 18000,
        max_error_retries: int = 3,
        location_code: int | None = None,
        language_code: str | None = None,
        debug: bool | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """High-volume POST/GET. Returns (results_df, failed_df)."""
        _validate_job_id(job_id)
        resolved = self._resolve_domain(domain, domain_id, interactive)

        if batch_size > 100:
            batch_size = 100

        debug = debug if debug is not None else self.config.debug

        empty_cols = self._get_schema() + ["domain_id", "domain", "endpoint_mode"]

        task_queue: Queue = Queue()
        results: list[pd.DataFrame] = []
        failed_rows: list[dict] = []
        results_lock = Lock()
        failed_lock = Lock()

        stats = {
            "collected": 0,
            "get_requests": 0,
            "not_ready_cycles": 0,
            "start_time": time.time(),
            "stop": False,
        }
        stats_lock = Lock()

        sessions = [self._client._create_session() for _ in range(num_workers)]

        task_id_to_keyword: dict[str, str] = {}
        batches = list(self._client._chunked(targets, batch_size))

        if debug:
            print(f"Submitting {len(targets):,} keywords in {len(batches)} batches...")

        for i, batch in enumerate(batches):
            tasks = self._task_post(batch, location_code, language_code, debug=False)
            for t in tasks:
                task_id_to_keyword[t["id"]] = t["keyword"]
            if debug and (i + 1) % 100 == 0:
                print(f"  Submitted {i+1}/{len(batches)} batches...")

        if not task_id_to_keyword:
            print("ERROR: No tasks submitted")
            return pd.DataFrame(columns=empty_cols), pd.DataFrame(columns=["keyword", "task_id", "reason"])

        total_tasks = len(task_id_to_keyword)

        for task_id, keyword in task_id_to_keyword.items():
            task_queue.put({"task_id": task_id, "keyword": keyword, "error_retries": 0})

        if debug:
            print(f"Submitted {total_tasks:,} tasks. Starting {num_workers} workers...")

        def worker(worker_id: int):
            session = sessions[worker_id]
            while not stats["stop"]:
                try:
                    task = task_queue.get(timeout=2)
                except Empty:
                    if stats["stop"]:
                        break
                    with stats_lock:
                        if stats["collected"] + len(failed_rows) >= total_tasks:
                            break
                    continue

                task_id = task["task_id"]
                keyword = task["keyword"]
                error_retries = task["error_retries"]

                with stats_lock:
                    stats["get_requests"] += 1

                try:
                    url = f"{self._client.BASE_URL}/{self.GET_URL}/{task_id}"
                    resp = session.get(url, timeout=30)
                    data = resp.json()

                    tasks_list = data.get("tasks", [])
                    if not tasks_list:
                        with stats_lock:
                            stats["not_ready_cycles"] += 1
                        task_queue.put(task)
                        task_queue.task_done()
                        continue

                    task_data = tasks_list[0]
                    task_status = task_data.get("status_code", 0)

                    if task_status in (40102, 40601, 40602, 40202):
                        with stats_lock:
                            stats["not_ready_cycles"] += 1
                        task_queue.put(task)
                        task_queue.task_done()
                        continue

                    if task_status == 40402:
                        with failed_lock:
                            failed_rows.append({
                                "keyword": keyword,
                                "task_id": task_id,
                                "reason": "task_not_found (40402)",
                            })
                        task_queue.task_done()
                        continue

                    if task_status != 20000:
                        if error_retries < max_error_retries:
                            task["error_retries"] += 1
                            task_queue.put(task)
                        else:
                            with failed_lock:
                                failed_rows.append({
                                    "keyword": keyword,
                                    "task_id": task_id,
                                    "reason": f"status_{task_status}_after_{max_error_retries}_retries",
                                })
                        task_queue.task_done()
                        continue

                    result = task_data.get("result", [])
                    if not result or result[0] is None:
                        with stats_lock:
                            stats["not_ready_cycles"] += 1
                        task_queue.put(task)
                        task_queue.task_done()
                        continue

                    items = result[0].get("items")
                    if items is None:
                        with stats_lock:
                            stats["not_ready_cycles"] += 1
                        task_queue.put(task)
                        task_queue.task_done()
                        continue

                    response = {"tasks": [task_data]}
                    df = self._parse_response(response, keyword)

                    with results_lock:
                        results.append(df)
                    with stats_lock:
                        stats["collected"] += 1
                    task_queue.task_done()

                except Exception as e:
                    if error_retries < max_error_retries:
                        task["error_retries"] += 1
                        task_queue.put(task)
                    else:
                        with failed_lock:
                            failed_rows.append({
                                "keyword": keyword,
                                "task_id": task_id,
                                "reason": f"exception_after_{max_error_retries}_retries: {str(e)[:100]}",
                            })
                    task_queue.task_done()

        def monitor():
            while not stats["stop"]:
                time.sleep(10)
                with stats_lock:
                    collected = stats["collected"]
                    get_requests = stats["get_requests"]
                    not_ready = stats["not_ready_cycles"]
                    elapsed = time.time() - stats["start_time"]

                avg_rate = collected / elapsed * 60 if elapsed > 0 else 0
                remaining = total_tasks - collected - len(failed_rows)
                eta_min = remaining / avg_rate if avg_rate > 0 else 0

                if debug:
                    print(
                        f"  [{int(elapsed)}s] Collected {collected:,}/{total_tasks:,}, "
                        f"queue: {task_queue.qsize():,}, "
                        f"avg: {avg_rate:.0f}/min, ETA: {eta_min:.1f}min, "
                        f"GETs: {get_requests:,}, not_ready: {not_ready:,}, "
                        f"failed: {len(failed_rows)}"
                    )

                with stats_lock:
                    if stats["collected"] + len(failed_rows) >= total_tasks:
                        break

                if time.time() - stats["start_time"] >= max_wait:
                    break

        worker_threads = [Thread(target=worker, args=(i,), daemon=True) for i in range(num_workers)]
        monitor_thread = Thread(target=monitor, daemon=True)

        for t in worker_threads:
            t.start()
        monitor_thread.start()

        start = time.time()
        while time.time() - start < max_wait:
            with stats_lock:
                if stats["collected"] + len(failed_rows) >= total_tasks:
                    break
            time.sleep(1)

        stats["stop"] = True

        for t in worker_threads:
            t.join(timeout=5)

        remaining_in_queue = 0
        while not task_queue.empty():
            try:
                task = task_queue.get_nowait()
                failed_rows.append({
                    "keyword": task["keyword"],
                    "task_id": task["task_id"],
                    "reason": "timeout",
                })
                remaining_in_queue += 1
            except Empty:
                break

        if remaining_in_queue > 0:
            print(f"WARNING: {remaining_in_queue:,} tasks timed out")

        failed_df = pd.DataFrame(failed_rows, columns=["keyword", "task_id", "reason"])

        if not failed_df.empty:
            print(f"WARNING: {len(failed_df):,} keywords failed")

        if results:
            results_df = pd.concat(results, ignore_index=True)
            results_df = self._stamp_fetch_metadata(results_df, resolved, endpoint_mode="standard")
            elapsed = (time.time() - stats["start_time"]) / 60
            if debug:
                print(f"Done. {len(results_df):,} rows for {stats['collected']:,} keywords in {elapsed:.1f}min")
                print(f"Total GET requests: {stats['get_requests']:,}")
                print(f"Not-ready cycles: {stats['not_ready_cycles']:,}")
                if elapsed > 0:
                    print(f"Effective rate: {stats['collected'] / elapsed:.0f}/min")
            if upload:
                self.upload(self._client.bq_client, results_df, job_id=job_id)
            return results_df, failed_df

        return pd.DataFrame(columns=empty_cols), failed_df

    def extract_paa(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or "item_type" not in df.columns:
            return pd.DataFrame(columns=["keyword", "question", "answer", "url", "title"])

        paa_rows = df[df["item_type"] == "people_also_ask"]
        if paa_rows.empty:
            return pd.DataFrame(columns=["keyword", "question", "answer", "url", "title"])

        results = []
        for _, row in paa_rows.iterrows():
            keyword = row.get("keyword", "")
            item = row.get("item", {})
            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except json.JSONDecodeError:
                    continue

            items = item.get("items", []) or []
            for paa_item in items:
                results.append({
                    "keyword": keyword,
                    "question": paa_item.get("title", ""),
                    "answer": paa_item.get("expanded_element", [{}])[0].get("description", "") if paa_item.get("expanded_element") else "",
                    "url": paa_item.get("url", ""),
                    "title": paa_item.get("title", ""),
                })

        return pd.DataFrame(results)
