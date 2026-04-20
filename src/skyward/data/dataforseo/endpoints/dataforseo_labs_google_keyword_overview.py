"""dataforseo_labs_google_keyword_overview — v3/dataforseo_labs/google/keyword_overview/live

Fetches keyword overview data for up to 700 keywords per request.
Includes search volume, CPC, competition, keyword difficulty,
search intent, and impressions data.
"""

from __future__ import annotations

import json
import math
import time

import pandas as pd

from skyward.data.dataforseo.base import BaseEndpoint


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

        rows = []
        for item in items:
            keyword_info = item.get("keyword_info") or {}
            keyword_properties = item.get("keyword_properties") or {}
            search_intent_info = item.get("search_intent_info") or {}
            impressions_info = item.get("impressions_info") or {}

            # Serialize complex fields
            categories = keyword_info.get("categories")
            categories_str = json.dumps(categories) if categories else None

            monthly_searches = keyword_info.get("monthly_searches")
            monthly_searches_str = json.dumps(monthly_searches) if monthly_searches else None

            foreign_intent = search_intent_info.get("foreign_intent")
            if isinstance(foreign_intent, list):
                foreign_intent_str = ",".join(str(x) for x in foreign_intent if x is not None)
            else:
                foreign_intent_str = foreign_intent

            rows.append({
                "keyword": item.get("keyword"),
                "location_code": item.get("location_code"),
                "search_volume": keyword_info.get("search_volume"),
                "competition": keyword_info.get("competition"),
                "competition_level": keyword_info.get("competition_level"),
                "cpc": keyword_info.get("cpc"),
                "low_top_of_page_bid": keyword_info.get("low_top_of_page_bid"),
                "high_top_of_page_bid": keyword_info.get("high_top_of_page_bid"),
                "categories": categories_str,
                "monthly_searches": monthly_searches_str,
                "keyword_difficulty": keyword_properties.get("keyword_difficulty"),
                "detected_language": keyword_properties.get("detected_language"),
                "is_another_language": keyword_properties.get("is_another_language"),
                "main_intent": search_intent_info.get("main_intent"),
                "foreign_intent": foreign_intent_str,
                "ad_position_min": impressions_info.get("ad_position_min"),
                "ad_position_max": impressions_info.get("ad_position_max"),
                "ad_position_average": impressions_info.get("ad_position_average"),
                "cpc_min": impressions_info.get("cpc_min"),
                "cpc_max": impressions_info.get("cpc_max"),
                "cpc_average": impressions_info.get("cpc_average"),
                "daily_impressions_min": impressions_info.get("daily_impressions_min"),
                "daily_impressions_max": impressions_info.get("daily_impressions_max"),
                "daily_impressions_average": impressions_info.get("daily_impressions_average"),
                "task_id": task_id,
            })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return [
            "keyword", "location_code",
            "search_volume", "competition", "competition_level", "cpc",
            "low_top_of_page_bid", "high_top_of_page_bid",
            "categories", "monthly_searches",
            "keyword_difficulty", "detected_language", "is_another_language",
            "main_intent", "foreign_intent",
            "ad_position_min", "ad_position_max", "ad_position_average",
            "cpc_min", "cpc_max", "cpc_average",
            "daily_impressions_min", "daily_impressions_max", "daily_impressions_average",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["keyword", "location_code"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = ["search_volume", "keyword_difficulty", "location_code"]
        float_cols = [
            "competition", "cpc", "low_top_of_page_bid", "high_top_of_page_bid",
            "ad_position_min", "ad_position_max", "ad_position_average",
            "cpc_min", "cpc_max", "cpc_average",
            "daily_impressions_min", "daily_impressions_max", "daily_impressions_average",
        ]
        bool_cols = ["is_another_language"]

        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

        for col in float_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        for col in bool_cols:
            if col in df.columns:
                df[col] = df[col].astype("boolean")

        for col in ["categories", "monthly_searches", "foreign_intent"]:
            if col in df.columns:
                df[col] = df[col].astype("string")

        return df

    def _fetch_live(self, target, **kwargs) -> pd.DataFrame:
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

    def live_all(
        self,
        keywords: list[str],
        *,
        batch_size: int = 700,
        batch_delay: float = 0.2,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Fetch keyword overview data for an arbitrary number of keywords.

        Chunks the keyword list into batches of up to 700 and calls _fetch_live()
        for each batch sequentially.

        Args:
            keywords: Full list of keywords
            batch_size: Keywords per API call (max 700)
            batch_delay: Delay in seconds between batches
            **kwargs: Passed to _fetch_live()

        Returns:
            Combined DataFrame of all results
        """
        batch_size = min(batch_size, 700)
        total_batches = math.ceil(len(keywords) / batch_size)
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

        if df_list:
            return pd.concat(df_list, ignore_index=True)
        return pd.DataFrame(columns=self._get_schema() + ["task_id"])
