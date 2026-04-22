"""dataforseo_labs_google_ranked_keywords — v3/dataforseo_labs/google/ranked_keywords/live

Fetches ranked keywords for a domain.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pandas as pd

from skyward.data.dataforseo.base import _UNSET, BaseEndpoint
from skyward.functions import _validate_job_id


class DataforseoLabsGoogleRankedKeywords(BaseEndpoint):
    LIVE_URL = "dataforseo_labs/google/ranked_keywords/live"
    TABLE_NAME = "dataforseo_labs-google-ranked_keywords"

    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        return [{
            "target": target,
            "language_name": "English",
            "location_code": kwargs.get("location_code", self.config.location_code),
            "limit": kwargs.get("limit", 1000),
            "offset": kwargs.get("offset", 0),
            "load_rank_absolute": True,
            "order_by": ["ranked_serp_element.serp_item.etv,desc"],
            "filters": kwargs.get("filters"),
        }]

    def _parse_response(self, response: dict, target: str) -> pd.DataFrame:
        try:
            task = response["tasks"][0]
            task_id = task.get("id", "")
            items = task["result"][0]["items"]
            if not items:
                return pd.DataFrame(columns=self._get_schema() + ["task_id"])
        except (KeyError, IndexError, TypeError):
            return pd.DataFrame(columns=self._get_schema() + ["task_id"])

        rows = []
        for item in items:
            keyword_data = item.get("keyword_data", {}) or {}
            keyword_info = keyword_data.get("keyword_info", {}) or {}
            keyword_props = keyword_data.get("keyword_properties", {}) or {}
            serp_info = keyword_data.get("serp_info", {}) or {}
            avg_backlinks_info = keyword_data.get("avg_backlinks_info", {}) or {}
            intent_info = keyword_data.get("search_intent_info", {}) or {}

            ranked_serp_element = item.get("ranked_serp_element", {}) or {}
            serp_item = ranked_serp_element.get("serp_item", {}) or {}
            rank_changes = serp_item.get("rank_changes", {}) or {}
            serp_item_backlinks_info = serp_item.get("backlinks_info", {}) or {}
            serp_item_rank_info = serp_item.get("rank_info", {}) or {}

            keyword = keyword_data.get("keyword")
            ranked_url = serp_item.get("url")
            rank = serp_item.get("rank_group")
            search_volume = keyword_info.get("search_volume")

            if not (
                keyword
                and rank is not None
                and ranked_url
                and search_volume is not None
            ):
                continue

            search_volume_trend = keyword_info.get("search_volume_trend") or {}
            monthly_searches = keyword_info.get("monthly_searches")

            rows.append({
                "keyword": keyword,
                "rank": rank,
                "url": ranked_url,
                "search_volume": search_volume,
                "keyword_difficulty": keyword_props.get("keyword_difficulty"),
                "national_location_code": self.config.location_code,
                "traffic_volume": serp_item.get("etv"),
                "keyword_location_code": keyword_data.get("location_code"),
                "language_code": keyword_data.get("language_code"),
                "main_domain": serp_item.get("main_domain"),
                "cpc": keyword_info.get("cpc"),
                "low_top_of_page_bid": keyword_info.get("low_top_of_page_bid"),
                "high_top_of_page_bid": keyword_info.get("high_top_of_page_bid"),
                "competition": keyword_info.get("competition"),
                "competition_level": keyword_info.get("competition_level"),
                "categories": keyword_info.get("categories"),
                "monthly_searches": monthly_searches,
                "search_volume_trend_monthly": search_volume_trend.get("monthly"),
                "search_volume_trend_quarterly": search_volume_trend.get("quarterly"),
                "search_volume_trend_yearly": search_volume_trend.get("yearly"),
                "keyword_info_last_updated_time": keyword_info.get("last_updated_time"),
                "core_keyword": keyword_props.get("core_keyword"),
                "detected_language": keyword_props.get("detected_language"),
                "is_another_language": keyword_props.get("is_another_language"),
                "rank_absolute": serp_item.get("rank_absolute"),
                "position": serp_item.get("position"),
                "serp_keyword_difficulty": ranked_serp_element.get("keyword_difficulty"),
                "serp_item_types": serp_info.get("serp_item_types"),
                "se_results_count": serp_info.get("se_results_count"),
                "main_intent": intent_info.get("main_intent"),
                "foreign_intent": intent_info.get("foreign_intent"),
                "search_intent_last_updated_time": intent_info.get("last_updated_time"),
                "avg_backlinks": avg_backlinks_info.get("backlinks"),
                "avg_referring_domains": avg_backlinks_info.get("referring_domains"),
                "avg_referring_main_domains": avg_backlinks_info.get("referring_main_domains"),
                "avg_rank": avg_backlinks_info.get("rank"),
                "avg_main_domain_rank": avg_backlinks_info.get("main_domain_rank"),
                "avg_backlinks_last_updated_time": avg_backlinks_info.get("last_updated_time"),
                "serp_item_type": serp_item.get("type"),
                "serp_item_title": serp_item.get("title"),
                "serp_item_description": serp_item.get("description"),
                "is_featured_snippet": serp_item.get("is_featured_snippet"),
                "estimated_paid_traffic_cost": serp_item.get("estimated_paid_traffic_cost"),
                "previous_rank_absolute": rank_changes.get("previous_rank_absolute"),
                "rank_is_new": rank_changes.get("is_new"),
                "rank_is_up": rank_changes.get("is_up"),
                "rank_is_down": rank_changes.get("is_down"),
                "serp_item_referring_domains": serp_item_backlinks_info.get("referring_domains"),
                "serp_item_referring_main_domains": serp_item_backlinks_info.get("referring_main_domains"),
                "serp_item_backlinks": serp_item_backlinks_info.get("backlinks"),
                "serp_item_page_rank": serp_item_rank_info.get("page_rank"),
                "serp_item_main_domain_rank": serp_item_rank_info.get("main_domain_rank"),
                "is_lost": ranked_serp_element.get("is_lost"),
                "task_id": task_id,
            })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return [
            "keyword", "rank", "url", "search_volume", "keyword_difficulty",
            "national_location_code", "traffic_volume",
            "keyword_location_code", "language_code", "main_domain",
            "cpc", "low_top_of_page_bid", "high_top_of_page_bid",
            "competition", "competition_level", "categories", "monthly_searches",
            "search_volume_trend_monthly", "search_volume_trend_quarterly", "search_volume_trend_yearly",
            "keyword_info_last_updated_time",
            "core_keyword", "detected_language", "is_another_language",
            "rank_absolute", "position", "serp_keyword_difficulty",
            "serp_item_types", "se_results_count", "main_intent", "foreign_intent",
            "search_intent_last_updated_time",
            "avg_backlinks", "avg_referring_domains", "avg_referring_main_domains",
            "avg_rank", "avg_main_domain_rank",
            "avg_backlinks_last_updated_time",
            "serp_item_type", "serp_item_title", "serp_item_description",
            "is_featured_snippet", "estimated_paid_traffic_cost",
            "previous_rank_absolute",
            "rank_is_new", "rank_is_up", "rank_is_down",
            "serp_item_referring_domains", "serp_item_referring_main_domains",
            "serp_item_backlinks",
            "serp_item_page_rank", "serp_item_main_domain_rank",
            "is_lost",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["keyword", "job_id", "upload_id", "domain", "national_location_code"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = [
            "rank", "search_volume", "keyword_difficulty", "national_location_code",
            "keyword_location_code",
            "rank_absolute", "se_results_count", "serp_keyword_difficulty",
            "previous_rank_absolute",
            "serp_item_referring_domains", "serp_item_referring_main_domains",
            "serp_item_backlinks",
            "serp_item_page_rank", "serp_item_main_domain_rank",
        ]
        float_cols = [
            "traffic_volume", "cpc",
            "low_top_of_page_bid", "high_top_of_page_bid", "competition",
            "search_volume_trend_monthly", "search_volume_trend_quarterly", "search_volume_trend_yearly",
            "avg_backlinks", "avg_referring_domains", "avg_referring_main_domains",
            "avg_rank", "avg_main_domain_rank",
            "estimated_paid_traffic_cost",
        ]
        bool_cols = [
            "is_another_language",
            "is_featured_snippet",
            "rank_is_new", "rank_is_up", "rank_is_down",
            "is_lost",
        ]
        ts_cols = [
            "keyword_info_last_updated_time",
            "search_intent_last_updated_time",
            "avg_backlinks_last_updated_time",
        ]
        stringify_cols = ["categories", "serp_item_types", "monthly_searches"]

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
                )

        if "foreign_intent" in df.columns:
            def _normalize(v):
                if isinstance(v, list):
                    return ",".join(str(x) for x in v if x is not None)
                return v
            df["foreign_intent"] = df["foreign_intent"].apply(_normalize).astype("string")

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
        limit_per_domain: int = 10000,
        filters: list | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Fetch ranked keywords for multiple domains.

        This endpoint handles pagination internally since each domain can have
        many keywords.

        Args:
            targets: List of domains to query
            domain / domain_id: Domain-resolution args (exactly one required;
                pass `domain=None` to opt out of tagging). Stamped via
                `_stamp_fetch_metadata` after concat.
            job_id: UUID job identifier for the upload batch.
            interactive: If True, prompt on unknown domain.
            upload: If True, append to the BQ table and log upload event.
            limit_per_domain: Max keywords per domain
            filters: Optional API filters forwarded to `_build_payload`
            **kwargs: Additional parameters
        """
        _validate_job_id(job_id)
        resolved = self._resolve_domain(domain, domain_id, interactive)

        all_dfs: list[pd.DataFrame] = []

        for per_domain_target in targets:
            if self.config.debug:
                print(f"Fetching ranked keywords for {per_domain_target}...")

            df = await self._fetch_domain_keywords(
                per_domain_target, limit_per_domain, filters=filters, **kwargs
            )
            if df is not None and not df.empty:
                all_dfs.append(df)

        if not all_dfs:
            print("No rows returned. Skipping upload.")
            return pd.DataFrame(
                columns=self._get_schema() + ["task_id", "domain_id", "domain", "endpoint_mode"]
            )

        combined = pd.concat(all_dfs, ignore_index=True)
        combined = self._stamp_fetch_metadata(combined, resolved, endpoint_mode="live")

        if upload:
            self.upload(self._client.bq_client, combined, job_id=job_id)

        return combined

    async def _fetch_domain_keywords(
        self,
        domain: str,
        limit: int,
        **kwargs,
    ) -> pd.DataFrame:
        """Fetch all ranked keywords for a single domain with pagination."""
        page_size = 1000
        max_concurrent = kwargs.get("max_concurrent", 30)
        semaphore = asyncio.Semaphore(max_concurrent)

        results: list[pd.DataFrame] = []
        offset = 0
        consecutive_empty = 0

        while offset < limit and consecutive_empty < 2:
            remaining = min(page_size, limit - offset)

            async with semaphore:
                df = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda o=offset, r=remaining: self._fetch_live(
                        domain,
                        limit=r,
                        offset=o,
                        **kwargs,
                    )
                )

            if df.empty:
                consecutive_empty += 1
            else:
                consecutive_empty = 0
                results.append(df)

            offset += page_size

        if results:
            return pd.concat(results, ignore_index=True)
        return pd.DataFrame(columns=self._get_schema() + ["task_id"])
