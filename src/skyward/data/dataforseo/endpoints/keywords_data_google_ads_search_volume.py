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
    # POST/GET workflow methods (to be implemented)
    # -------------------------------------------------------------------------

    def _task_post(self, keywords: list[str], location_code: int, debug: bool = False) -> list[dict]:
        """POST keywords to task_post endpoint. TODO: Implement."""
        raise NotImplementedError("POST/GET workflow not yet implemented")

    def _tasks_ready(self, debug: bool = False) -> list[str]:
        """Check which tasks are ready. TODO: Implement."""
        raise NotImplementedError("POST/GET workflow not yet implemented")

    def _task_get(self, task_id: str, debug: bool = False) -> pd.DataFrame:
        """GET results for a completed task. TODO: Implement."""
        raise NotImplementedError("POST/GET workflow not yet implemented")

    def post(self, target: str | list[str], **kwargs) -> pd.DataFrame:
        """Submit keywords using POST/GET workflow. TODO: Implement."""
        raise NotImplementedError("POST/GET workflow not yet implemented")

    async def post_all(self, targets: list[str], **kwargs) -> pd.DataFrame:
        """Submit keywords using POST/GET workflow. TODO: Implement."""
        raise NotImplementedError("POST/GET workflow not yet implemented")
