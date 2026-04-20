"""dataforseo_labs_google_ranked_keywords — v3/dataforseo_labs/google/ranked_keywords/live

Fetches ranked keywords for a domain.
"""

from __future__ import annotations

import asyncio
import json
import time

import pandas as pd

from skyward.data.dataforseo.base import BaseEndpoint


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

            keyword = keyword_data.get("keyword")
            ranked_url = serp_item.get("url")
            rank = serp_item.get("rank_group")
            keyword_difficulty = keyword_props.get("keyword_difficulty")
            search_volume = keyword_info.get("search_volume")

            if not all([keyword, rank is not None, ranked_url, search_volume is not None, keyword_difficulty is not None]):
                continue

            search_volume_trend = keyword_info.get("search_volume_trend") or {}
            monthly_searches = keyword_info.get("monthly_searches")

            rows.append({
                "keyword": keyword,
                "rank": rank,
                "url": ranked_url,
                "search_volume": search_volume,
                "keyword_difficulty": keyword_difficulty,
                "national_location_code": self.config.location_code,
                "traffic_volume": serp_item.get("etv"),
                "cost_per_click": keyword_info.get("low_top_of_page_bid") or keyword_info.get("cpc"),
                "keyword_location_code": keyword_data.get("location_code"),
                "language_code": keyword_data.get("language_code"),
                "main_domain": serp_item.get("main_domain"),
                "cpc_raw": keyword_info.get("cpc"),
                "low_top_of_page_bid": keyword_info.get("low_top_of_page_bid"),
                "high_top_of_page_bid": keyword_info.get("high_top_of_page_bid"),
                "competition": keyword_info.get("competition"),
                "competition_level": keyword_info.get("competition_level"),
                "categories": keyword_info.get("categories"),
                "monthly_searches": json.dumps(monthly_searches) if monthly_searches else None,
                "search_volume_trend_monthly": search_volume_trend.get("monthly"),
                "search_volume_trend_quarterly": search_volume_trend.get("quarterly"),
                "search_volume_trend_yearly": search_volume_trend.get("yearly"),
                "rank_absolute": serp_item.get("rank_absolute"),
                "position": serp_item.get("position"),
                "serp_keyword_difficulty": ranked_serp_element.get("keyword_difficulty"),
                "serp_item_types": serp_info.get("serp_item_types"),
                "se_results_count": serp_info.get("se_results_count"),
                "main_intent": intent_info.get("main_intent"),
                "foreign_intent": intent_info.get("foreign_intent"),
                "avg_backlinks": avg_backlinks_info.get("backlinks"),
                "avg_referring_domains": avg_backlinks_info.get("referring_domains"),
                "avg_referring_main_domains": avg_backlinks_info.get("referring_main_domains"),
                "avg_rank": avg_backlinks_info.get("rank"),
                "avg_main_domain_rank": avg_backlinks_info.get("main_domain_rank"),
                "task_id": task_id,
            })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return [
            "keyword", "rank", "url", "search_volume", "keyword_difficulty",
            "national_location_code", "traffic_volume", "cost_per_click",
            "keyword_location_code", "language_code", "main_domain",
            "cpc_raw", "low_top_of_page_bid", "high_top_of_page_bid",
            "competition", "competition_level", "categories", "monthly_searches",
            "search_volume_trend_monthly", "search_volume_trend_quarterly", "search_volume_trend_yearly",
            "rank_absolute", "position", "serp_keyword_difficulty",
            "serp_item_types", "se_results_count", "main_intent", "foreign_intent",
            "avg_backlinks", "avg_referring_domains", "avg_referring_main_domains",
            "avg_rank", "avg_main_domain_rank",
            # Metadata columns added by live_all()
            "company_id", "domain", "filters",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["keyword", "job_id", "upload_id", "domain", "national_location_code"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = [
            "rank", "search_volume", "keyword_difficulty", "national_location_code",
            "keyword_location_code", "search_volume_trend_monthly",
            "search_volume_trend_quarterly", "search_volume_trend_yearly",
            "rank_absolute", "se_results_count",
        ]
        float_cols = [
            "traffic_volume", "cost_per_click", "cpc_raw",
            "low_top_of_page_bid", "high_top_of_page_bid", "competition",
            "avg_backlinks", "avg_referring_domains", "avg_referring_main_domains",
            "avg_rank", "avg_main_domain_rank",
        ]

        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

        for col in float_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

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
        company_id: str | None = None,
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
            company_id: Optional company identifier to add to all rows (links to Meta.companies)
            limit_per_domain: Max keywords per domain
            filters: Optional API filters (also stored in output for tracking)
            **kwargs: Additional parameters

        Note:
            The original `get_dataforseo_labs_google_ranked_keywords_all` took a single
            (company, domain) pair. This method processes multiple domains. If you need
            the single-domain pattern with company metadata, pass a single-item list
            with the company_id parameter.
        """
        all_dfs = []

        for domain in targets:
            if self.config.debug:
                print(f"Fetching ranked keywords for {domain}...")

            df = await self._fetch_domain_keywords(domain, limit_per_domain, filters=filters, **kwargs)
            if not df.empty:
                df["domain"] = domain
                df["company_id"] = company_id
                df["filters"] = str(filters) if filters else None
                all_dfs.append(df)

        if all_dfs:
            return pd.concat(all_dfs, ignore_index=True)
        return pd.DataFrame(columns=self._get_schema())

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
        return pd.DataFrame(columns=self._get_schema())
