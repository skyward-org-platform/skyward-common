"""keywords_data_google_ads_search_volume — v3/keywords_data/google_ads/search_volume/live

Fetches search volume data for keywords. Supports both Live and POST/GET workflows.

Note: Live endpoints are limited to 12 requests/minute. For high-volume use,
prefer the POST/GET workflow via post_all().
"""

from __future__ import annotations

import asyncio
import functools
import json
import time
from typing import Any

import pandas as pd

from skyward.data.dataforseo.base import BaseEndpoint, _UNSET
from skyward.data.dataforseo.collector.producer import parse_task_post, submit_and_wait
from skyward.data.dataforseo.exceptions import IncompleteTaskError
from skyward.functions import _validate_job_id


class KeywordsDataGoogleAdsSearchVolume(BaseEndpoint):
    LIVE_URL = "keywords_data/google_ads/search_volume/live"
    POST_URL = "keywords_data/google_ads/search_volume/task_post"
    READY_URL = "keywords_data/google_ads/search_volume/tasks_ready"
    GET_URL = "keywords_data/google_ads/search_volume/task_get"
    TABLE_NAME = "keywords_data-google_ads-search_volume"
    COLLECTOR_ENDPOINT_KEY = "keywords_data_google_ads_search_volume"

    def _build_payload(self, target: str | list[str], **kwargs) -> list[dict]:
        keywords = [target] if isinstance(target, str) else target
        return [{
            "language_name": "English",
            "location_code": kwargs.get("location_code", self.config.location_code),
            "keywords": keywords,
        }]

    def _parse_response(self, response: dict, target: str | list[str]) -> pd.DataFrame:
        try:
            task = response["tasks"][0]
            task_id = task.get("id", "")
            task_data = task.get("data") or {}
            task_location_code = task_data.get("location_code")
            items = task["result"]
            if not items:
                return pd.DataFrame(columns=self._get_schema() + ["task_id"])
        except (KeyError, IndexError, TypeError):
            return pd.DataFrame(columns=self._get_schema() + ["task_id"])

        rows = []
        for item in items:
            keyword = item.get("keyword")
            if not keyword:
                continue
            # Prefer per-row location_code from the API; fall back to the task-level echo
            row_location_code = item.get("location_code")
            if row_location_code is None:
                row_location_code = task_location_code

            monthly_searches = item.get("monthly_searches")
            rows.append({
                "keyword": keyword,
                "search_volume": item.get("search_volume"),
                "location_code": row_location_code,
                "cpc": item.get("cpc"),
                "competition": item.get("competition"),
                "competition_index": item.get("competition_index"),
                "low_top_of_page_bid": item.get("low_top_of_page_bid"),
                "high_top_of_page_bid": item.get("high_top_of_page_bid"),
                "monthly_searches": json.dumps(monthly_searches) if monthly_searches else None,
                "task_id": task_id,
            })

        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=self._get_schema() + ["task_id"])

    def _get_schema(self) -> list[str]:
        return [
            "keyword",
            "search_volume",
            "location_code",
            "cpc",
            "competition",
            "competition_index",
            "low_top_of_page_bid",
            "high_top_of_page_bid",
            "monthly_searches",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["keyword", "location_code"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = ["search_volume", "location_code", "competition_index"]
        float_cols = ["cpc", "low_top_of_page_bid", "high_top_of_page_bid"]
        stringify_cols = ["monthly_searches"]

        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        for col in float_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        for col in stringify_cols:
            if col in df.columns:
                df[col] = df[col].apply(
                    lambda v: json.dumps(v) if isinstance(v, (list, dict)) else v
                )
        return df

    def _fetch_live(self, target: str | list[str], **kwargs) -> pd.DataFrame:
        # TODO(debug-logs): wire run-scoped collector into this loop (ClickUp 86babz7xp).
        kwargs.pop("_debug_collector", None)  # accepted, not yet captured
        keywords = [target] if isinstance(target, str) else target

        if len(keywords) > 1000:
            raise ValueError("Maximum 1000 keywords per request")

        url = f"{self._client.BASE_URL}/{self.LIVE_URL}"
        payload = self._build_payload(keywords, **kwargs)

        max_retries = kwargs.get("max_retries") or self.config.max_retries
        retry_delays = [3, 5, 15, 30]

        for attempt in range(max_retries):
            delay = retry_delays[attempt] if attempt < len(retry_delays) else retry_delays[-1]
            if attempt > 0:
                time.sleep(delay)

            resp = self._client._post(url, payload, max_retries=1, retry_delay=0)
            if not resp:
                continue

            try:
                status_code = resp["tasks"][0].get("status_code")
                if status_code == 20000:
                    return self._parse_response(resp, keywords)
                if self.config.debug:
                    print(f"Attempt {attempt + 1}: status_code {status_code}")
            except (KeyError, IndexError):
                continue

        return pd.DataFrame(columns=self._get_schema() + ["task_id"])

    async def live_all(
        self,
        targets: list[str],
        *,
        domain: Any = _UNSET,
        domain_id: Any = _UNSET,
        job_id: str,
        interactive: bool = False,
        upload: bool = True,
        batch_size: int = 1000,
        batch_delay: float = 2.0,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Fetch search volume for many keywords in batches.

        Args:
            targets: List of keywords
            domain / domain_id: Domain attribution (mutually exclusive; omit both to opt out)
            job_id: Required — tagged onto uploaded rows for lineage
            interactive: Whether to prompt the user when resolving an unknown domain
            upload: If True (default), BQ upload happens after fetch
            batch_size: Keywords per request (max 1000)
            batch_delay: Delay between batches to respect rate limits
            **kwargs: Additional parameters forwarded to `_fetch_live`

        Returns:
            Combined DataFrame with all search volumes, stamped with fetch metadata.
        """
        _validate_job_id(job_id)
        resolved = self._resolve_domain(domain, domain_id, interactive)

        batch_size = min(batch_size, 1000)  # API max
        batches = list(self._client._chunked(targets, batch_size))
        total_batches = len(batches)

        if self.config.debug:
            print(f"Fetching search volume for {len(targets)} keywords in {total_batches} batches...")

        results: list[pd.DataFrame] = []
        start_time = time.monotonic()

        for idx, batch in enumerate(batches, 1):
            df = self._fetch_live(batch, **kwargs)
            if not df.empty:
                results.append(df)

            if self.config.debug:
                elapsed = time.monotonic() - start_time
                print(f"Progress: {idx}/{total_batches} batches completed. Time: {elapsed:.1f}s")

            if idx < total_batches:
                await asyncio.sleep(batch_delay)

        if not results:
            print("No rows returned. Skipping upload.")
            return pd.DataFrame(columns=self._get_schema() + ["domain_id", "domain", "endpoint_mode"])

        combined = pd.concat(results, ignore_index=True)
        combined = self._stamp_fetch_metadata(combined, resolved, endpoint_mode="live")

        if upload:
            self.upload(self._client.bq_client, combined, job_id=job_id)

        return combined

    # -------------------------------------------------------------------------
    # POST/GET workflow methods
    # -------------------------------------------------------------------------

    def _task_post(
        self,
        keywords: list[str],
        location_code: int | None = None,
        language_name: str = "English",
        debug: bool = False,
        tag: str | None = None,
    ) -> list[str]:
        """Submit a batch of keywords for async processing.

        Returns the list of DataForSEO task_ids created.
        """
        cfg = self.config
        location_code = location_code or cfg.location_code

        url = f"{self._client.BASE_URL}/{self.POST_URL}"
        payload = [{
            "keywords": keywords,
            "language_name": language_name,
            "location_code": location_code,
        }]
        if tag is not None:
            payload[0]["tag"] = tag

        resp = self._client._post(url, payload)
        if not resp:
            if debug:
                print("[search_volume] task_post returned empty response")
            return []

        task_ids = []
        for task in resp.get("tasks", []) or []:
            tid = task.get("id")
            if tid:
                task_ids.append(tid)
        if debug:
            print(f"[search_volume] submitted {len(task_ids)} tasks: {task_ids}")
        return task_ids

    def _tasks_ready(self, debug: bool = False) -> list[str]:
        """Poll DataForSEO for completed task_ids."""
        url = f"{self._client.BASE_URL}/{self.READY_URL}"
        resp = self._client._get(url)
        if not resp:
            return []
        ready = []
        for task in resp.get("tasks", []) or []:
            for entry in task.get("result") or []:
                tid = entry.get("id")
                if tid:
                    ready.append(tid)
        if debug:
            print(f"[search_volume] {len(ready)} tasks ready")
        return ready

    def _task_get(self, task_id: str, debug: bool = False) -> pd.DataFrame:
        """Retrieve a completed task by id. Reuses _parse_response so task_id is stamped per row."""
        url = f"{self._client.BASE_URL}/{self.GET_URL}/{task_id}"
        resp = self._client._get(url)
        if not resp:
            if debug:
                print(f"[search_volume] task_get({task_id}) returned empty")
            return pd.DataFrame(columns=self._get_schema() + ["task_id"])
        return self._parse_response(resp, target=None)

    def post(
        self,
        target,
        *,
        domain=None,
        domain_id=None,
        job_id: str,
        interactive: bool = False,
        upload: bool = True,
        location_code: int | None = None,
        language_name: str = "English",
        **kwargs,
    ) -> pd.DataFrame:
        """Single-batch POST/GET workflow. Raises IncompleteTaskError on 2h timeout."""
        _validate_job_id(job_id)

        # Resolve domain (mutually exclusive domain/domain_id; both None = opt out)
        if domain is not None and domain_id is not None:
            raise ValueError("Must pass exactly one of `domain=` or `domain_id=`, not both.")
        if domain is None and domain_id is None:
            resolved = None
        elif domain is not None:
            resolved = self._resolve_domain(domain, _UNSET, interactive)
        else:
            resolved = self._resolve_domain(_UNSET, domain_id, interactive)

        keywords = [target] if isinstance(target, str) else list(target)

        # 1. Submit
        task_ids = self._task_post(
            keywords=keywords,
            location_code=location_code,
            language_name=language_name,
            debug=self.config.debug,
            tag=kwargs.get("tag"),
        )
        if not task_ids:
            print("No task_ids returned from task_post. Skipping upload.")
            return pd.DataFrame(columns=self._get_schema() + ["task_id", "domain_id", "domain", "endpoint_mode"])

        # 2. Poll until all complete or timeout
        pending = set(task_ids)
        deadline = time.monotonic() + self.config.task_total_timeout
        while pending and time.monotonic() < deadline:
            ready = set(self._tasks_ready(debug=self.config.debug))
            complete = pending & ready
            pending -= complete
            if not pending:
                break
            time.sleep(self.config.task_poll_interval)

        if pending:
            raise IncompleteTaskError(
                f"{len(pending)} of {len(task_ids)} tasks did not complete within "
                f"{self.config.task_total_timeout} seconds",
                task_ids=sorted(pending),
            )

        # 3. Retrieve
        frames = []
        for tid in task_ids:
            df_part = self._task_get(tid, debug=self.config.debug)
            if not df_part.empty:
                frames.append(df_part)

        if not frames:
            print("All tasks completed but returned no rows. Skipping upload.")
            return pd.DataFrame(columns=self._get_schema() + ["task_id", "domain_id", "domain", "endpoint_mode"])

        df = pd.concat(frames, ignore_index=True)
        df = self._stamp_fetch_metadata(df, resolved, endpoint_mode="standard")

        if upload:
            self.upload(self._client.bq_client, df, job_id=job_id)

        return df

    def _post_all_collector(
        self,
        targets: list[str],
        *,
        job_id: str,
        resolved: dict | None,
        proceed_at_pct: float = 1.0,
        keywords_per_task: int = 1000,
        location_code: int | None = None,
        language_name: str = "English",
    ) -> dict:
        """Collector path: task_post keyword batches, record tracking rows, wait on BQ.

        One task covers ~keywords_per_task keywords, so tracking is at task granularity
        (the canonical table holds the per-keyword rows). Returns a receipt incl. 40200
        reject counts. The collector drains results into the canonical table.
        """
        location_code = location_code or self.config.location_code
        url = f"{self._client.BASE_URL}/{self.POST_URL}"

        posted: list[dict] = []
        rejected_payment = 0
        rejected_other = 0
        for i in range(0, len(targets), keywords_per_task):
            chunk = targets[i:i + keywords_per_task]
            payload = [{
                "keywords": chunk,
                "language_name": language_name,
                "location_code": location_code,
            }]
            parsed = parse_task_post(self._client._post(url, payload))
            posted.extend(parsed["posted"])
            rejected_payment += parsed["rejected_payment"]
            rejected_other += parsed["rejected_other"]

        if rejected_payment:
            print(f"[search_volume] WARNING: DFS rejected {rejected_payment} posts at submit "
                  f"(40200 Payment Required — likely balance). Those batches were skipped "
                  f"(not charged, not queued); rerun them later.")

        summary = submit_and_wait(
            bq_client=self._client.bq_client,
            job_id=job_id,
            endpoint_key=self.COLLECTOR_ENDPOINT_KEY,
            posted_tasks=posted,
            proceed_at_pct=proceed_at_pct,
            poll_interval=self.config.task_poll_interval,
            max_wait=self.config.task_total_timeout,
            domain_id=(resolved or {}).get("domain_id"),
            domain=(resolved or {}).get("domain"),
        )
        summary["rejected_payment"] = rejected_payment
        summary["rejected_other"] = rejected_other
        return summary

    async def post_all(
        self,
        targets: list[str],
        *,
        domain=None,
        domain_id=None,
        job_id: str,
        interactive: bool = False,
        upload: bool = True,
        location_code: int | None = None,
        language_name: str = "English",
        keywords_per_task: int = 1000,
        use_collector: bool | None = None,
        proceed_at_pct: float = 1.0,
        **kwargs,
    ):
        """Multi-batch POST/GET.

        Legacy (default): poll in-process, returns a DataFrame.
        Collector mode (`use_collector=True` or `config.use_collector`): submit + track and
        wait on dfs_job_summary; returns a summary receipt dict. See ClickUp 86bac9q9y.
        """
        _validate_job_id(job_id)

        # Resolve domain
        if domain is not None and domain_id is not None:
            raise ValueError("Must pass exactly one of `domain=` or `domain_id=`, not both.")
        if domain is None and domain_id is None:
            resolved = None
        elif domain is not None:
            resolved = self._resolve_domain(domain, _UNSET, interactive)
        else:
            resolved = self._resolve_domain(_UNSET, domain_id, interactive)

        use_collector = self.config.use_collector if use_collector is None else use_collector
        if use_collector:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, functools.partial(
                self._post_all_collector, targets, job_id=job_id, resolved=resolved,
                proceed_at_pct=proceed_at_pct, keywords_per_task=keywords_per_task,
                location_code=location_code, language_name=language_name,
            ))

        # 1. Chunk + submit
        chunks = [targets[i : i + keywords_per_task] for i in range(0, len(targets), keywords_per_task)]
        all_task_ids: list[str] = []

        loop = asyncio.get_running_loop()
        submit_tasks = [
            loop.run_in_executor(
                None,
                lambda c=chunk: self._task_post(
                    keywords=c,
                    location_code=location_code,
                    language_name=language_name,
                    debug=self.config.debug,
                ),
            )
            for chunk in chunks
        ]
        submit_results = await asyncio.gather(*submit_tasks)
        for tids in submit_results:
            all_task_ids.extend(tids)

        if not all_task_ids:
            print("No task_ids returned from task_post. Skipping upload.")
            return pd.DataFrame(columns=self._get_schema() + ["task_id", "domain_id", "domain", "endpoint_mode"])

        # 2. Poll
        pending = set(all_task_ids)
        deadline = time.monotonic() + self.config.task_total_timeout
        while pending and time.monotonic() < deadline:
            ready = set(self._tasks_ready(debug=self.config.debug))
            pending -= ready
            if not pending:
                break
            await asyncio.sleep(self.config.task_poll_interval)

        if pending:
            raise IncompleteTaskError(
                f"{len(pending)} of {len(all_task_ids)} tasks did not complete within "
                f"{self.config.task_total_timeout} seconds",
                task_ids=sorted(pending),
            )

        # 3. Retrieve in parallel
        retrieve_tasks = [
            loop.run_in_executor(None, lambda tid=tid: self._task_get(tid, debug=self.config.debug))
            for tid in all_task_ids
        ]
        retrieved = await asyncio.gather(*retrieve_tasks)
        frames = [df for df in retrieved if not df.empty]

        if not frames:
            print("All tasks completed but returned no rows. Skipping upload.")
            return pd.DataFrame(columns=self._get_schema() + ["task_id", "domain_id", "domain", "endpoint_mode"])

        df = pd.concat(frames, ignore_index=True)
        df = self._stamp_fetch_metadata(df, resolved, endpoint_mode="standard")

        if upload:
            self.upload(self._client.bq_client, df, job_id=job_id)

        return df
