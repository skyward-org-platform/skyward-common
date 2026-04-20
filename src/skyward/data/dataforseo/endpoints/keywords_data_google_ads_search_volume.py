"""keywords_data_google_ads_search_volume — v3/keywords_data/google_ads/search_volume/live

Fetches search volume data for keywords. Supports both Live and POST/GET workflows.

Note: Live endpoints are limited to 12 requests/minute. For high-volume use,
prefer the POST/GET workflow via post_all().
"""

from __future__ import annotations

import asyncio
import time

import pandas as pd

from skyward.data.dataforseo.base import BaseEndpoint


class KeywordsDataGoogleAdsSearchVolume(BaseEndpoint):
    LIVE_URL = "keywords_data/google_ads/search_volume/live"
    POST_URL = "keywords_data/google_ads/search_volume/task_post"
    READY_URL = "keywords_data/google_ads/search_volume/tasks_ready"
    GET_URL = "keywords_data/google_ads/search_volume/task_get"
    TABLE_NAME = "keywords_data-google_ads-search_volume"

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
            items = task["result"]
            if not items:
                return pd.DataFrame(columns=self._get_schema() + ["task_id"])
        except (KeyError, IndexError, TypeError):
            return pd.DataFrame(columns=self._get_schema() + ["task_id"])

        rows = []
        for item in items:
            keyword = item.get("keyword")
            search_volume = item.get("search_volume")
            if keyword and search_volume is not None:
                rows.append({
                    "keyword": keyword,
                    "local_search_volume": search_volume,
                    "local_location_code": self.config.location_code,
                    "task_id": task_id,
                })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return ["keyword", "local_search_volume", "local_location_code"]

    def _get_dedupe_keys(self) -> list[str]:
        return ["keyword", "local_location_code"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        if "local_search_volume" in df.columns:
            df["local_search_volume"] = pd.to_numeric(df["local_search_volume"], errors="coerce").astype("Int64")
        if "local_location_code" in df.columns:
            df["local_location_code"] = pd.to_numeric(df["local_location_code"], errors="coerce").astype("Int64")
        return df

    def _fetch_live(self, target: str | list[str], **kwargs) -> pd.DataFrame:
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
        batch_size: int = 1000,
        batch_delay: float = 2.0,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Fetch search volume for many keywords in batches.

        Args:
            targets: List of keywords
            batch_size: Keywords per request (max 1000)
            batch_delay: Delay between batches to respect rate limits
            **kwargs: Additional parameters

        Returns:
            Combined DataFrame with all search volumes
        """
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

        if results:
            return pd.concat(results, ignore_index=True)
        return pd.DataFrame(columns=self._get_schema() + ["task_id"])

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
        import time as _time
        from skyward.data.dataforseo.base import _UNSET
        from skyward.data.dataforseo.exceptions import IncompleteTaskError
        from skyward.functions import _validate_job_id

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
        deadline = _time.monotonic() + self.config.task_total_timeout
        while pending and _time.monotonic() < deadline:
            ready = set(self._tasks_ready(debug=self.config.debug))
            complete = pending & ready
            pending -= complete
            if not pending:
                break
            _time.sleep(self.config.task_poll_interval)

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
        **kwargs,
    ) -> pd.DataFrame:
        """Multi-batch POST/GET. Chunks targets, submits all, polls, retrieves in parallel."""
        import asyncio as _asyncio
        import time as _time
        from skyward.data.dataforseo.base import _UNSET
        from skyward.data.dataforseo.exceptions import IncompleteTaskError
        from skyward.functions import _validate_job_id

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

        # 1. Chunk + submit
        chunks = [targets[i : i + keywords_per_task] for i in range(0, len(targets), keywords_per_task)]
        all_task_ids: list[str] = []

        loop = _asyncio.get_running_loop()
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
        submit_results = await _asyncio.gather(*submit_tasks)
        for tids in submit_results:
            all_task_ids.extend(tids)

        if not all_task_ids:
            print("No task_ids returned from task_post. Skipping upload.")
            return pd.DataFrame(columns=self._get_schema() + ["task_id", "domain_id", "domain", "endpoint_mode"])

        # 2. Poll
        pending = set(all_task_ids)
        deadline = _time.monotonic() + self.config.task_total_timeout
        while pending and _time.monotonic() < deadline:
            ready = set(self._tasks_ready(debug=self.config.debug))
            pending -= ready
            if not pending:
                break
            await _asyncio.sleep(self.config.task_poll_interval)

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
        retrieved = await _asyncio.gather(*retrieve_tasks)
        frames = [df for df in retrieved if not df.empty]

        if not frames:
            print("All tasks completed but returned no rows. Skipping upload.")
            return pd.DataFrame(columns=self._get_schema() + ["task_id", "domain_id", "domain", "endpoint_mode"])

        df = pd.concat(frames, ignore_index=True)
        df = self._stamp_fetch_metadata(df, resolved, endpoint_mode="standard")

        if upload:
            self.upload(self._client.bq_client, df, job_id=job_id)

        return df
