"""dataforseo_labs_google_keyword_suggestions — v3/dataforseo_labs/google/keyword_suggestions/live"""

from __future__ import annotations

import json
import time

import pandas as pd

from skyward.data.dataforseo.base import BaseEndpoint


class DataforseoLabsGoogleKeywordSuggestions(BaseEndpoint):
    LIVE_URL = "dataforseo_labs/google/keyword_suggestions/live"
    TABLE_NAME = "dataforseo_labs-google-keyword_suggestions"

    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        return [{
            "keyword": target,
            "location_code": kwargs.get("location_code", self.config.location_code),
            "language_code": kwargs.get("language_code", self.config.language_code),
            "limit": kwargs.get("limit", 50),
            "include_seed_keyword": kwargs.get("include_seed_keyword", False),
            "filters": kwargs.get("filters") or [["keyword_info.search_volume", ">", 10]],
            "order_by": ["keyword_info.search_volume,desc"],
        }]

    def _parse_response(self, response: dict, target: str) -> pd.DataFrame:
        try:
            tasks = response.get("tasks") or []
            if not tasks:
                return pd.DataFrame(columns=self._get_schema() + ["task_id"])

            task = tasks[0]
            task_id = task.get("id", "")
            results = task.get("result", []) or []
            if not results:
                return pd.DataFrame(columns=self._get_schema() + ["task_id"])

            suggestions_block = next((r for r in results if "items" in r), None)
            if not suggestions_block:
                return pd.DataFrame(columns=self._get_schema() + ["task_id"])

            items = suggestions_block.get("items", []) or []
            if not items:
                return pd.DataFrame(columns=self._get_schema() + ["task_id"])

        except Exception:
            return pd.DataFrame(columns=self._get_schema() + ["task_id"])

        se_type = suggestions_block.get("se_type", "google")
        seed_keyword = suggestions_block.get("seed_keyword", target)
        location_code_val = suggestions_block.get("location_code")
        language_code_val = suggestions_block.get("language_code")

        rows = []
        for item in items:
            kw_str = item.get("keyword")
            if not kw_str:
                kd = item.get("keyword_data") or {}
                kw_str = kd.get("keyword")
            if not kw_str:
                continue

            keyword_info = item.get("keyword_info") or {}
            keyword_properties = item.get("keyword_properties") or {}
            search_intent_info = item.get("search_intent_info") or {}
            avg_backlinks_info = item.get("avg_backlinks_info") or {}
            search_volume_trend = keyword_info.get("search_volume_trend") or {}

            rows.append({
                "se_type": se_type,
                "seed_keyword": seed_keyword,
                "keyword": kw_str,
                "location_code": item.get("location_code", location_code_val),
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
                "words_count": keyword_properties.get("words_count"),
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
            "se_type", "seed_keyword", "keyword", "location_code", "language_code",
            "search_volume", "competition", "competition_level", "cpc",
            "low_top_of_page_bid", "high_top_of_page_bid", "categories",
            "monthly_searches",
            "search_volume_trend_monthly", "search_volume_trend_quarterly", "search_volume_trend_yearly",
            "keyword_info_last_updated_time",
            "core_keyword",
            "keyword_difficulty", "detected_language", "is_another_language", "words_count",
            "main_intent", "foreign_intent",
            "search_intent_last_updated_time",
            "avg_backlinks", "avg_dofollow", "avg_referring_pages",
            "avg_referring_domains", "avg_referring_main_domains",
            "avg_rank", "avg_main_domain_rank",
            "avg_backlinks_last_updated_time",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["seed_keyword", "keyword", "location_code", "language_code"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = ["location_code", "search_volume", "keyword_difficulty", "words_count"]
        float_cols = [
            "competition", "cpc", "low_top_of_page_bid", "high_top_of_page_bid",
            "search_volume_trend_monthly", "search_volume_trend_quarterly", "search_volume_trend_yearly",
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
