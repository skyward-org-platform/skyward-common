"""dataforseo_labs_google_keyword_overview — v3/dataforseo_labs/google/keyword_overview/live

Fetches keyword overview data for up to 700 keywords per request.
Includes search volume, CPC, competition, keyword difficulty,
search intent, trend, and average backlinks info.
"""

from __future__ import annotations

import asyncio
import functools
import json
import math
import time
from typing import Any

import pandas as pd

from skyward.data.dataforseo.base import _UNSET, BaseEndpoint
from skyward.functions import _validate_job_id


class DataforseoLabsGoogleKeywordOverview(BaseEndpoint):
    LIVE_URL = "dataforseo_labs/google/keyword_overview/live"
    POST_URL = None
    TABLE_NAME = "dataforseo_labs-google-keyword_overview"
    DATASET = "DataForSEO"

    def _build_payload(self, target: str | list[str], **kwargs) -> list[dict]:
        keywords = target if isinstance(target, list) else [target]
        return [{
            "keywords": keywords,
            "location_code": kwargs.get("location_code", self.config.location_code),
            "language_code": kwargs.get("language_code", self.config.language_code),
        }]

    def _parse_response(self, response: dict, target: str | list[str]) -> pd.DataFrame:
        try:
            tasks = response.get("tasks") or []
            if not tasks:
                return pd.DataFrame(columns=self._get_schema() + ["task_id"])

            task = tasks[0]
            task_id = task.get("id", "")
            results = task.get("result", []) or []
            if not results:
                return pd.DataFrame(columns=self._get_schema() + ["task_id"])

            items = results[0].get("items", []) or [] if results[0] else []
            if not items:
                return pd.DataFrame(columns=self._get_schema() + ["task_id"])

        except Exception:
            return pd.DataFrame(columns=self._get_schema() + ["task_id"])

        result0 = results[0] or {}
        language_code_val = result0.get("language_code")

        rows = []
        for item in items:
            keyword_info = item.get("keyword_info") or {}
            keyword_properties = item.get("keyword_properties") or {}
            search_intent_info = item.get("search_intent_info") or {}
            avg_backlinks_info = item.get("avg_backlinks_info") or {}
            search_volume_trend = keyword_info.get("search_volume_trend") or {}

            rows.append({
                "keyword": item.get("keyword"),
                "location_code": item.get("location_code"),
                "language_code": item.get("language_code", language_code_val),
                "search_volume": keyword_info.get("search_volume"),
                "competition": keyword_info.get("competition"),
                "competition_level": keyword_info.get("competition_level"),
                "cpc": keyword_info.get("cpc"),
                "low_top_of_page_bid": keyword_info.get("low_top_of_page_bid"),
                "high_top_of_page_bid": keyword_info.get("high_top_of_page_bid"),
                "categories": keyword_info.get("categories"),
                "monthly_searches": keyword_info.get("monthly_searches"),
                "search_volume_trend_monthly": search_volume_trend.get("monthly"),
                "search_volume_trend_quarterly": search_volume_trend.get("quarterly"),
                "search_volume_trend_yearly": search_volume_trend.get("yearly"),
                "keyword_info_last_updated_time": keyword_info.get("last_updated_time"),
                "core_keyword": keyword_properties.get("core_keyword"),
                "keyword_difficulty": keyword_properties.get("keyword_difficulty"),
                "detected_language": keyword_properties.get("detected_language"),
                "is_another_language": keyword_properties.get("is_another_language"),
                "main_intent": search_intent_info.get("main_intent"),
                "foreign_intent": search_intent_info.get("foreign_intent"),
                "search_intent_last_updated_time": search_intent_info.get("last_updated_time"),
                "avg_backlinks": avg_backlinks_info.get("backlinks"),
                "avg_dofollow": avg_backlinks_info.get("dofollow"),
                "avg_referring_pages": avg_backlinks_info.get("referring_pages"),
                "avg_referring_domains": avg_backlinks_info.get("referring_domains"),
                "avg_referring_main_domains": avg_backlinks_info.get("referring_main_domains"),
                "avg_rank": avg_backlinks_info.get("rank"),
                "avg_main_domain_rank": avg_backlinks_info.get("main_domain_rank"),
                "avg_backlinks_last_updated_time": avg_backlinks_info.get("last_updated_time"),
                "task_id": task_id,
            })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return [
            "keyword", "location_code", "language_code",
            "search_volume", "competition", "competition_level", "cpc",
            "low_top_of_page_bid", "high_top_of_page_bid",
            "categories", "monthly_searches",
            "search_volume_trend_monthly", "search_volume_trend_quarterly",
            "search_volume_trend_yearly",
            "keyword_info_last_updated_time",
            "core_keyword",
            "keyword_difficulty", "detected_language", "is_another_language",
            "main_intent", "foreign_intent",
            "search_intent_last_updated_time",
            "avg_backlinks", "avg_dofollow", "avg_referring_pages",
            "avg_referring_domains", "avg_referring_main_domains",
            "avg_rank", "avg_main_domain_rank",
            "avg_backlinks_last_updated_time",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["keyword", "location_code", "language_code"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = ["search_volume", "keyword_difficulty", "location_code"]
        float_cols = [
            "competition", "cpc", "low_top_of_page_bid", "high_top_of_page_bid",
            "search_volume_trend_monthly", "search_volume_trend_quarterly",
            "search_volume_trend_yearly",
            "avg_backlinks", "avg_dofollow", "avg_referring_pages",
            "avg_referring_domains", "avg_referring_main_domains",
            "avg_rank", "avg_main_domain_rank",
        ]
        bool_cols = ["is_another_language"]
        ts_cols = [
            "keyword_info_last_updated_time",
            "search_intent_last_updated_time",
            "avg_backlinks_last_updated_time",
        ]
        stringify_cols = ["categories", "monthly_searches"]

        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

        for col in float_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        for col in bool_cols:
            if col in df.columns:
                df[col] = df[col].astype("boolean")

        for col in ts_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

        for col in stringify_cols:
            if col in df.columns:
                df[col] = df[col].apply(
                    lambda v: json.dumps(v) if isinstance(v, (list, dict)) else v
                ).astype("string")

        if "foreign_intent" in df.columns:
            def _normalize(v):
                if isinstance(v, list):
                    return ",".join(str(x) for x in v if x is not None)
                return v
            df["foreign_intent"] = df["foreign_intent"].apply(_normalize).astype("string")

        return df

    def _fetch_live(self, target, **kwargs) -> pd.DataFrame:
        cfg = self.config
        # TODO(debug-logs): wire run-scoped collector into this loop (ClickUp 86babz7xp).
        kwargs.pop("_debug_collector", None)  # accepted, not yet captured
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
                    print(f"[keyword_overview] Invalid response. Attempt {attempt}/{max_retries}")
                continue
            try:
                df = self._parse_response(resp, target)
                if not df.empty:
                    return df
                if debug:
                    print(f"[keyword_overview] Empty result. Attempt {attempt}/{max_retries}")
            except Exception as e:
                if debug:
                    print(f"[keyword_overview] Parse error: {e}. Attempt {attempt}/{max_retries}")
                continue

        return pd.DataFrame(columns=self._get_schema() + ["task_id"])

    async def live_all(self, *args, **kwargs) -> pd.DataFrame:
        """Async wrapper — delegates to _live_all_sync in a thread executor
        so callers can `await` uniformly across all endpoints."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(self._live_all_sync, *args, **kwargs),
        )

    def _live_all_sync(
        self,
        keywords: list[str],
        *,
        domain: Any = _UNSET,
        domain_id: Any = _UNSET,
        job_id: str,
        interactive: bool = False,
        upload: bool = True,
        batch_size: int = 700,
        batch_delay: float = 0.2,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Fetch keyword overview data for an arbitrary number of keywords.

        Chunks the keyword list into batches of up to 700 and calls _fetch_live()
        for each batch sequentially. Honors the BaseEndpoint contract: validates
        job_id, resolves domain, stamps fetch metadata, and uploads unless
        upload=False.

        Args:
            keywords: Full list of keywords
            domain / domain_id: Exactly one must be provided (or domain=None to opt out)
            job_id: Required job identifier
            interactive: If True, prompt on unknown domain
            upload: If True, append rows to BQ
            batch_size: Keywords per API call (max 700)
            batch_delay: Delay in seconds between batches
            **kwargs: Passed to _fetch_live()

        Returns:
            Combined DataFrame with metadata columns stamped.
        """
        _validate_job_id(job_id)
        resolved = self._resolve_domain(domain, domain_id, interactive)

        batch_size = min(batch_size, 700)
        total_batches = math.ceil(len(keywords) / batch_size) if keywords else 0
        df_list: list[pd.DataFrame] = []

        if self.config.debug:
            print(f"Starting keyword_overview processing of {len(keywords)} keywords in {total_batches} batches of {batch_size}...")

        for idx, chunk in enumerate(self._client._chunked(keywords, batch_size), start=1):
            if self.config.debug:
                print(f"Processing batch {idx}/{total_batches} ({len(chunk)} keywords)")

            df = self._fetch_live(chunk, **kwargs)
            if not df.empty:
                df_list.append(df)

            if idx < total_batches:
                time.sleep(batch_delay)

        if not df_list:
            print("No rows returned. Skipping upload.")
            return pd.DataFrame(columns=self._get_schema() + ["domain_id", "domain", "endpoint_mode"])

        combined = pd.concat(df_list, ignore_index=True)
        combined = self._stamp_fetch_metadata(combined, resolved, endpoint_mode="live")

        if upload:
            self.upload(self._client.bq_client, combined, job_id=job_id)

        return combined
