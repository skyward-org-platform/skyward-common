"""dataforseo_labs_google_related_keywords — v3/dataforseo_labs/google/related_keywords/live"""

from __future__ import annotations

import json
import time

import pandas as pd

from skyward.data.dataforseo.base import BaseEndpoint


class DataforseoLabsGoogleRelatedKeywords(BaseEndpoint):
    LIVE_URL = "dataforseo_labs/google/related_keywords/live"
    TABLE_NAME = "dataforseo_labs-google-related_keywords"

    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        return [{
            "keyword": target,
            "location_code": kwargs.get("location_code", self.config.location_code),
            "language_code": kwargs.get("language_code", self.config.language_code),
            "depth": kwargs.get("depth", 1),
            "limit": kwargs.get("limit", 20),
            "include_seed_keyword": kwargs.get("include_seed_keyword", False),
            "filters": kwargs.get("filters") or [["keyword_data.keyword_info.search_volume", ">", 10]],
            "order_by": ["keyword_data.keyword_info.search_volume,desc"],
        }]

    def _parse_response(self, response: dict, target: str) -> pd.DataFrame:
        try:
            task = response["tasks"][0]
            results = task.get("result", [])
            if not results:
                return pd.DataFrame(columns=self._get_schema())

            result = results[0]
            items = result.get("items", [])
            if not items:
                return pd.DataFrame(columns=self._get_schema())

        except Exception:
            return pd.DataFrame(columns=self._get_schema())

        task_id = task.get("id")
        seed_keyword = result.get("seed_keyword", target)
        location_code_val = result.get("location_code")
        language_code_val = result.get("language_code")

        rows = []
        for item in items:
            kd = item.get("keyword_data", {}) or {}
            kw_info = kd.get("keyword_info", {}) or {}
            kw_props = kd.get("keyword_properties", {}) or {}
            serp_info = kd.get("serp_info", {}) or {}
            backlinks_info = kd.get("avg_backlinks_info", {}) or {}
            intent_info = kd.get("search_intent_info", {}) or {}
            search_volume_trend = kw_info.get("search_volume_trend") or {}

            related_keyword = kd.get("keyword") or item.get("keyword")
            depth = item.get("depth")

            rows.append({
                "task_id": task_id,
                "seed_keyword": seed_keyword,
                "related_keyword": related_keyword,
                "depth": depth,
                "location_code": location_code_val,
                "language_code": language_code_val,
                "search_volume": kw_info.get("search_volume"),
                "cpc": kw_info.get("cpc"),
                "competition": kw_info.get("competition"),
                "competition_level": kw_info.get("competition_level"),
                "low_top_of_page_bid": kw_info.get("low_top_of_page_bid"),
                "high_top_of_page_bid": kw_info.get("high_top_of_page_bid"),
                "monthly_searches": kw_info.get("monthly_searches"),
                "search_volume_trend_monthly": search_volume_trend.get("monthly"),
                "search_volume_trend_quarterly": search_volume_trend.get("quarterly"),
                "search_volume_trend_yearly": search_volume_trend.get("yearly"),
                "keyword_info_last_updated_time": kw_info.get("last_updated_time"),
                "core_keyword": kw_props.get("core_keyword"),
                "keyword_difficulty": kw_props.get("keyword_difficulty"),
                "detected_language": kw_props.get("detected_language"),
                "is_other_language": kw_props.get("is_another_language"),
                "serp_item_types": serp_info.get("serp_item_types"),
                "se_results_count": serp_info.get("se_results_count"),
                "serp_last_updated_time": serp_info.get("last_updated_time"),
                "serp_info_check_url": serp_info.get("check_url"),
                "serp_info_previous_updated_time": serp_info.get("previous_updated_time"),
                "backlinks": backlinks_info.get("backlinks"),
                "dofollow": backlinks_info.get("dofollow"),
                "referring_pages": backlinks_info.get("referring_pages"),
                "referring_domains": backlinks_info.get("referring_domains"),
                "referring_main_domains": backlinks_info.get("referring_main_domains"),
                "main_domain_rank": backlinks_info.get("main_domain_rank"),
                "avg_rank": backlinks_info.get("rank"),
                "avg_backlinks_last_updated_time": backlinks_info.get("last_updated_time"),
                "search_intent_main": intent_info.get("main_intent"),
                "foreign_intent": intent_info.get("foreign_intent"),
                "search_intent_last_updated_time": intent_info.get("last_updated_time"),
                "related_keywords": item.get("related_keywords"),
            })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return [
            "task_id", "seed_keyword", "related_keyword", "depth",
            "location_code", "language_code",
            "search_volume", "cpc", "competition", "competition_level",
            "low_top_of_page_bid", "high_top_of_page_bid",
            "monthly_searches",
            "search_volume_trend_monthly", "search_volume_trend_quarterly", "search_volume_trend_yearly",
            "keyword_info_last_updated_time",
            "core_keyword",
            "keyword_difficulty",
            "detected_language", "is_other_language",
            "serp_item_types", "se_results_count", "serp_last_updated_time",
            "serp_info_check_url", "serp_info_previous_updated_time",
            "backlinks", "dofollow", "referring_pages",
            "referring_domains", "referring_main_domains", "main_domain_rank",
            "avg_rank",
            "avg_backlinks_last_updated_time",
            "search_intent_main", "foreign_intent",
            "search_intent_last_updated_time",
            "related_keywords",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["task_id", "related_keyword", "depth"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = ["depth", "location_code", "search_volume", "se_results_count", "keyword_difficulty"]
        float_cols = [
            "cpc", "competition", "low_top_of_page_bid", "high_top_of_page_bid",
            "search_volume_trend_monthly", "search_volume_trend_quarterly", "search_volume_trend_yearly",
            "backlinks", "dofollow", "referring_pages",
            "referring_domains", "referring_main_domains", "main_domain_rank",
            "avg_rank",
        ]
        bool_cols = ["is_other_language"]
        ts_cols = [
            "serp_last_updated_time",
            "serp_info_previous_updated_time",
            "keyword_info_last_updated_time",
            "search_intent_last_updated_time",
            "avg_backlinks_last_updated_time",
        ]
        stringify_cols = ["monthly_searches", "serp_item_types", "related_keywords"]

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

        return pd.DataFrame(columns=self._get_schema())
