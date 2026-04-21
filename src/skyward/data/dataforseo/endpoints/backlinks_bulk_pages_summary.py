"""backlinks_bulk_pages_summary — v3/backlinks/bulk_pages_summary/live

Fetches backlink summary data (total backlinks, referring domains, spam score)
for up to 1000 target pages per API call.

Unlike other endpoints, this accepts multiple targets in a single request.
The live() method handles a single target; live_all() batches targets into
groups of up to 1000 per API call.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from typing import Any

import pandas as pd

from skyward.data.dataforseo.base import _UNSET, BaseEndpoint
from skyward.functions import _validate_job_id


class BacklinksBulkPagesSummary(BaseEndpoint):
    LIVE_URL = "backlinks/bulk_pages_summary/live"
    TABLE_NAME = "backlinks-bulk_pages_summary"

    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        """Build payload for a single target."""
        return [{"targets": [target]}]

    def _build_bulk_payload(self, targets: list[str]) -> list[dict]:
        """Build payload for multiple targets (up to 1000)."""
        return [{"targets": targets}]

    def _parse_response(self, response: dict, target: str) -> pd.DataFrame:
        """Parse API response into a DataFrame. Works for single and bulk responses.

        Response structure: tasks[0].result[0].items[{url, backlinks, ...}]
        """
        try:
            task = response["tasks"][0]
            task_id = task.get("id", "")
            result = task["result"][0]
            items = result.get("items") or []
            if not items:
                return pd.DataFrame(columns=self._get_schema() + ["task_id"])
        except (KeyError, IndexError, TypeError):
            return pd.DataFrame(columns=self._get_schema() + ["task_id"])

        rows = []
        for item in items:
            if not item:
                continue
            rows.append({
                "url": item.get("url"),
                "backlinks": item.get("backlinks"),
                "referring_domains": item.get("referring_domains"),
                "referring_main_domains": item.get("referring_main_domains"),
                "rank": item.get("rank"),
                "main_domain_rank": item.get("main_domain_rank"),
                "spam_score": item.get("backlinks_spam_score"),
                "referring_ips": item.get("referring_ips"),
                "referring_subnets": item.get("referring_subnets"),
                "referring_pages": item.get("referring_pages"),
                "dofollow": (item.get("backlinks") or 0) - (
                    (item.get("referring_links_attributes") or {}).get("nofollow", 0)
                ),
                "nofollow": (item.get("referring_links_attributes") or {}).get("nofollow", 0),
                "broken_backlinks": item.get("broken_backlinks"),
                "broken_pages": item.get("broken_pages"),
                "task_id": task_id,
            })

        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=self._get_schema() + ["task_id"])

    def _get_schema(self) -> list[str]:
        return [
            "url", "backlinks", "referring_domains", "referring_main_domains",
            "rank", "main_domain_rank", "spam_score",
            "referring_ips", "referring_subnets", "referring_pages",
            "dofollow", "nofollow", "broken_backlinks", "broken_pages",
        ]

    async def _fetch_batch_with_fallback(
        self,
        batch: list[str],
        max_retries: int,
        retry_delay: int,
        debug: bool,
        _depth: int = 0,
    ) -> pd.DataFrame | None:
        """
        Fetch a batch with error handling:
        - 40501 (Invalid Field): remove bad URL and retry
        - Timeout/500: after 2 consecutive failures, split batch in half
        - Max 5 recursive splits to prevent infinite recursion
        """
        MAX_SPLIT_DEPTH = 5

        if _depth > MAX_SPLIT_DEPTH:
            if debug:
                print(f"  {'  ' * _depth}Max split depth reached. Skipping {len(batch)} URLs.")
            return pd.DataFrame()

        indent = "  " * (_depth + 1)
        url = f"{self._client.BASE_URL}/{self.LIVE_URL}"
        current_batch = list(batch)
        consecutive_failures = 0

        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                time.sleep(retry_delay)

            payload = self._build_bulk_payload(current_batch)
            resp = self._client._post(url, payload, max_retries=1, retry_delay=0)

            if not resp:
                consecutive_failures += 1
                if debug:
                    print(f"{indent}No response. Attempt {attempt}/{max_retries} (failures: {consecutive_failures})")

                if consecutive_failures >= 2 and len(current_batch) > 1:
                    if debug:
                        print(f"{indent}2 consecutive failures. Splitting batch of {len(current_batch)} in half (depth {_depth + 1})...")
                    mid = len(current_batch) // 2
                    left = await self._fetch_batch_with_fallback(
                        current_batch[:mid], max_retries, retry_delay, debug, _depth + 1
                    )
                    right = await self._fetch_batch_with_fallback(
                        current_batch[mid:], max_retries, retry_delay, debug, _depth + 1
                    )
                    parts = [df for df in [left, right] if df is not None and not df.empty]
                    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
                continue

            # Check for API-level errors
            try:
                task = resp.get("tasks", [{}])[0]
                task_status = task.get("status_code", 0)

                # 40501: Invalid target — remove and retry
                if task_status == 40501:
                    msg = task.get("status_message", "")
                    invalid_url = self._extract_invalid_target(msg)
                    if invalid_url and invalid_url in current_batch:
                        current_batch.remove(invalid_url)
                        if debug:
                            print(f"{indent}Removed invalid target: {invalid_url} ({len(current_batch)} remaining)")
                        if not current_batch:
                            return pd.DataFrame()
                        continue
                    if debug:
                        print(f"{indent}API error 40501: {msg}")
                    continue

                # 500xx: Server error — split immediately (deterministic, retrying won't help)
                if task_status >= 50000:
                    if len(current_batch) > 1:
                        if debug:
                            print(f"{indent}Server error {task_status}. Splitting batch of {len(current_batch)} in half (depth {_depth + 1})...")
                        mid = len(current_batch) // 2
                        left = await self._fetch_batch_with_fallback(
                            current_batch[:mid], max_retries, retry_delay, debug, _depth + 1
                        )
                        right = await self._fetch_batch_with_fallback(
                            current_batch[mid:], max_retries, retry_delay, debug, _depth + 1
                        )
                        parts = [df for df in [left, right] if df is not None and not df.empty]
                        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
                    if debug:
                        print(f"{indent}Server error {task_status} on single URL. Skipping: {current_batch[0][:80]}")
                    return pd.DataFrame()
            except (KeyError, IndexError):
                pass

            # Try to parse the response
            try:
                df = self._parse_response(resp, target="bulk")
                if not df.empty:
                    return df
                if debug:
                    print(f"{indent}Empty result. Attempt {attempt}/{max_retries}")
            except Exception as e:
                if debug:
                    print(f"{indent}Parse error: {e}. Attempt {attempt}/{max_retries}")
                continue

        return pd.DataFrame()

    @staticmethod
    def _extract_invalid_target(error_message: str) -> str | None:
        """Extract the invalid URL from a DataForSEO 40501 error message.

        Example message: "Invalid Field: 'target' (https://example.com(not set)."
        Returns: "https://example.com(not set)"
        """
        match = re.search(r"'target' \((.+)\)\.$", error_message)
        if match:
            return match.group(1)
        # Fallback: try without the trailing ).
        match = re.search(r"'target' \((.+)\)", error_message)
        if match:
            return match.group(1)
        return None

    def _get_dedupe_keys(self) -> list[str]:
        return ["url"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = [
            "backlinks", "referring_domains", "referring_main_domains",
            "rank", "main_domain_rank", "referring_ips", "referring_subnets",
            "referring_pages", "dofollow", "nofollow",
            "broken_backlinks", "broken_pages",
        ]
        float_cols = ["spam_score"]
        bool_cols: list[str] = []

        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        for col in float_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        for col in bool_cols:
            if col in df.columns:
                df[col] = df[col].astype("boolean")

        return df

    def _fetch_live(self, target: str, **kwargs) -> pd.DataFrame:
        """Single-target wrapper around the bulk fetch machinery."""
        max_retries = kwargs.pop("max_retries", self.config.max_retries)
        retry_delay = kwargs.pop("retry_delay", self.config.retry_delay)
        debug = kwargs.pop("debug", self.config.debug)
        result = asyncio.run(
            self._fetch_batch_with_fallback([target], max_retries, retry_delay, debug)
        )
        if result is None or result.empty:
            return pd.DataFrame(columns=self._get_schema() + ["task_id"])
        return result

    async def live_all(
        self,
        targets: list[str],
        *,
        domain: Any = _UNSET,
        domain_id: Any = _UNSET,
        job_id: str,
        interactive: bool = False,
        upload: bool = True,
        batch_size: int | None = None,
        batch_delay: float | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        _validate_job_id(job_id)
        resolved = self._resolve_domain(domain, domain_id, interactive)

        batch_size = min(batch_size or 1000, 1000)
        batch_delay = batch_delay if batch_delay is not None else self.config.batch_delay
        max_retries = kwargs.pop("max_retries", None) or self.config.max_retries
        retry_delay = kwargs.pop("retry_delay", None) or self.config.retry_delay
        debug = kwargs.pop("debug", None)
        if debug is None:
            debug = self.config.debug

        total = len(targets)
        total_batches = math.ceil(total / batch_size)

        if debug:
            print(f"Starting BacklinksBulkPagesSummary for {total} targets in {total_batches} batch(es) of up to {batch_size}...")

        df_list: list[pd.DataFrame] = []

        for idx, batch_targets in enumerate(self._client._chunked(targets, batch_size), start=1):
            current_batch = list(batch_targets)
            if debug:
                print(f"Batch {idx}/{total_batches} ({len(current_batch)} targets)")

            result_df = await self._fetch_batch_with_fallback(
                current_batch, max_retries, retry_delay, debug
            )
            if result_df is not None and not result_df.empty:
                df_list.append(result_df)

            if idx < total_batches:
                await asyncio.sleep(batch_delay)

        if not df_list:
            print("No rows returned. Skipping upload.")
            return pd.DataFrame(columns=self._get_schema() + ["domain_id", "domain", "endpoint_mode"])

        combined = pd.concat(df_list, ignore_index=True)
        combined = self._stamp_fetch_metadata(combined, resolved, endpoint_mode="live")

        if upload:
            self.upload(self._client.bq_client, combined, job_id=job_id)

        return combined
