"""dataforseo_labs_google_domain_rank_overview — v3/dataforseo_labs/google/domain_rank_overview/live

Fetches domain ranking overview metrics including total keyword counts,
estimated traffic, and ranking distribution.
"""

from __future__ import annotations

import time

import pandas as pd

from skyward.data.dataforseo.base import BaseEndpoint


class DataforseoLabsGoogleDomainRankOverview(BaseEndpoint):
    """
    Endpoint: v3/dataforseo_labs/google/domain_rank_overview/live

    Fetches domain ranking overview metrics including total keyword counts,
    estimated traffic, and ranking distribution.
    """

    LIVE_URL = "dataforseo_labs/google/domain_rank_overview/live"
    TABLE_NAME = "dataforseo_labs-google-domain_rank_overview"

    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        return [{
            "target": target,
            "location_code": kwargs.get("location_code", self.config.location_code),
            "language_code": kwargs.get("language_code", self.config.language_code),
        }]

    def _parse_response(self, response: dict, target: str) -> pd.DataFrame:
        try:
            task = response["tasks"][0]
            task_id = task.get("id", "")
            result = task["result"][0]
            items = result.get("items") or []
        except (KeyError, IndexError):
            return pd.DataFrame(columns=self._get_schema() + ["task_id"])

        rows = []
        for item in items:
            metrics = item.get("metrics", {}) or {}
            organic = metrics.get("organic", {}) or {}
            paid = metrics.get("paid", {}) or {}
            local_pack = metrics.get("local_pack", {}) or {}
            featured_snippet = metrics.get("featured_snippet", {}) or {}

            rows.append({
                "target": target,
                "location_code": item.get("location_code"),
                "language_code": item.get("language_code"),
                # Organic metrics
                "organic_count": organic.get("count"),
                "organic_etv": organic.get("etv"),
                "organic_impressions_etv": organic.get("impressions_etv"),
                "organic_estimated_paid_traffic_cost": organic.get("estimated_paid_traffic_cost"),
                "organic_is_new": organic.get("is_new"),
                "organic_is_up": organic.get("is_up"),
                "organic_is_down": organic.get("is_down"),
                "organic_is_lost": organic.get("is_lost"),
                "organic_pos_1": organic.get("pos_1"),
                "organic_pos_2_3": organic.get("pos_2_3"),
                "organic_pos_4_10": organic.get("pos_4_10"),
                "organic_pos_11_20": organic.get("pos_11_20"),
                "organic_pos_21_30": organic.get("pos_21_30"),
                "organic_pos_31_40": organic.get("pos_31_40"),
                "organic_pos_41_50": organic.get("pos_41_50"),
                "organic_pos_51_60": organic.get("pos_51_60"),
                "organic_pos_61_70": organic.get("pos_61_70"),
                "organic_pos_71_80": organic.get("pos_71_80"),
                "organic_pos_81_90": organic.get("pos_81_90"),
                "organic_pos_91_100": organic.get("pos_91_100"),
                # Paid metrics
                "paid_count": paid.get("count"),
                "paid_etv": paid.get("etv"),
                "paid_impressions_etv": paid.get("impressions_etv"),
                "paid_estimated_paid_traffic_cost": paid.get("estimated_paid_traffic_cost"),
                "paid_is_new": paid.get("is_new"),
                "paid_is_up": paid.get("is_up"),
                "paid_is_down": paid.get("is_down"),
                "paid_is_lost": paid.get("is_lost"),
                # Local pack metrics
                "local_pack_count": local_pack.get("count"),
                "local_pack_etv": local_pack.get("etv"),
                # Featured snippet metrics
                "featured_snippet_count": featured_snippet.get("count"),
                "featured_snippet_etv": featured_snippet.get("etv"),
                "task_id": task_id,
            })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return [
            "target", "location_code", "language_code",
            "organic_count", "organic_etv", "organic_impressions_etv",
            "organic_estimated_paid_traffic_cost",
            "organic_is_new", "organic_is_up", "organic_is_down", "organic_is_lost",
            "organic_pos_1", "organic_pos_2_3", "organic_pos_4_10",
            "organic_pos_11_20", "organic_pos_21_30", "organic_pos_31_40",
            "organic_pos_41_50", "organic_pos_51_60", "organic_pos_61_70",
            "organic_pos_71_80", "organic_pos_81_90", "organic_pos_91_100",
            "paid_count", "paid_etv", "paid_impressions_etv",
            "paid_estimated_paid_traffic_cost",
            "paid_is_new", "paid_is_up", "paid_is_down", "paid_is_lost",
            "local_pack_count", "local_pack_etv",
            "featured_snippet_count", "featured_snippet_etv",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["target", "location_code", "language_code"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = [
            "location_code",
            "organic_count", "organic_is_new", "organic_is_up", "organic_is_down", "organic_is_lost",
            "organic_pos_1", "organic_pos_2_3", "organic_pos_4_10",
            "organic_pos_11_20", "organic_pos_21_30", "organic_pos_31_40",
            "organic_pos_41_50", "organic_pos_51_60", "organic_pos_61_70",
            "organic_pos_71_80", "organic_pos_81_90", "organic_pos_91_100",
            "paid_count", "paid_is_new", "paid_is_up", "paid_is_down", "paid_is_lost",
            "local_pack_count", "featured_snippet_count",
        ]
        float_cols = [
            "organic_etv", "organic_impressions_etv", "organic_estimated_paid_traffic_cost",
            "paid_etv", "paid_impressions_etv", "paid_estimated_paid_traffic_cost",
            "local_pack_etv", "featured_snippet_etv",
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

    def get_keyword_count(self, domain: str, location_code: int | None = None, *, job_id: str | None = None) -> int:
        """
        Get the total number of organic ranked keywords for a domain.

        This is a convenience method that extracts just the keyword count.

        Args:
            domain: The domain to analyze (e.g., "example.com")
            location_code: Location code for targeting (default from config)
            job_id: Optional job_id; auto-generated if not provided.

        Returns:
            Total number of ranked keywords, or -1 on failure
        """
        from skyward.functions import generate_job_id
        job_id = job_id or generate_job_id()
        df = self.live(domain, domain=None, job_id=job_id, location_code=location_code, upload=False)
        if df.empty:
            return -1
        try:
            return int(df["organic_count"].iloc[0])
        except (KeyError, IndexError, TypeError, ValueError):
            return -1
