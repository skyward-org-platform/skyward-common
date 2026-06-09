"""backlinks_summary — v3/backlinks/summary/live

Fetches aggregate backlink metrics for a single target (domain or URL).
Returns one row per target.
"""

from __future__ import annotations

import json
import time

import pandas as pd

from skyward.data.dataforseo.base import BaseEndpoint


class BacklinksSummary(BaseEndpoint):
    LIVE_URL = "backlinks/summary/live"
    TABLE_NAME = "backlinks-summary"

    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        return [{
            "target": target,
            "internal_list_limit": kwargs.get("internal_list_limit", 10),
            "include_subdomains": kwargs.get("include_subdomains", True),
            "backlinks_status_type": kwargs.get("backlinks_status_type", "live"),
        }]

    def _parse_response(self, response: dict, target: str) -> pd.DataFrame:
        try:
            task = response["tasks"][0]
            task_id = task.get("id", "")
            result_list = task.get("result") or []
        except (KeyError, IndexError):
            return pd.DataFrame(columns=self._get_schema() + ["task_id"])

        rows = []
        for result in result_list:
            info = result.get("info") or {}
            target_str = result.get("target") or target
            # target_type: "url" if the target has a path component (e.g. example.com/page),
            # "domain" otherwise. DFS doesn't return this; we derive it locally.
            stripped = target_str.split("://", 1)[-1] if target_str else ""
            target_type = "url" if "/" in stripped else "domain"
            rows.append({
                "target": target_str,
                "target_type": target_type,
                "rank": result.get("rank"),
                "backlinks": result.get("backlinks"),
                "backlinks_spam_score": result.get("backlinks_spam_score"),
                "target_spam_score": info.get("target_spam_score"),
                "crawled_pages": result.get("crawled_pages"),
                "referring_domains": result.get("referring_domains"),
                "referring_domains_nofollow": result.get("referring_domains_nofollow"),
                "referring_main_domains": result.get("referring_main_domains"),
                "referring_main_domains_nofollow": result.get("referring_main_domains_nofollow"),
                "referring_ips": result.get("referring_ips"),
                "referring_subnets": result.get("referring_subnets"),
                "referring_pages": result.get("referring_pages"),
                "referring_pages_nofollow": result.get("referring_pages_nofollow"),
                "referring_links_tld": result.get("referring_links_tld"),
                "referring_links_types": result.get("referring_links_types"),
                "referring_links_attributes": result.get("referring_links_attributes"),
                "referring_links_platform_types": result.get("referring_links_platform_types"),
                "referring_links_semantic_locations": result.get("referring_links_semantic_locations"),
                "referring_links_countries": result.get("referring_links_countries"),
                "internal_links_count": result.get("internal_links_count"),
                "external_links_count": result.get("external_links_count"),
                "broken_backlinks": result.get("broken_backlinks"),
                "broken_pages": result.get("broken_pages"),
                "first_seen": result.get("first_seen"),
                "lost_date": result.get("lost_date"),
                "task_id": task_id,
            })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return [
            "target", "target_type", "rank", "backlinks", "backlinks_spam_score",
            "target_spam_score", "crawled_pages",
            "referring_domains", "referring_domains_nofollow",
            "referring_main_domains", "referring_main_domains_nofollow",
            "referring_ips", "referring_subnets",
            "referring_pages", "referring_pages_nofollow",
            "referring_links_tld", "referring_links_types",
            "referring_links_attributes", "referring_links_platform_types",
            "referring_links_semantic_locations", "referring_links_countries",
            "internal_links_count", "external_links_count",
            "broken_backlinks", "broken_pages",
            "first_seen", "lost_date",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["target"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = [
            "rank", "backlinks", "backlinks_spam_score",
            "target_spam_score", "crawled_pages",
            "referring_domains", "referring_domains_nofollow",
            "referring_main_domains", "referring_main_domains_nofollow",
            "referring_ips", "referring_subnets",
            "referring_pages", "referring_pages_nofollow",
            "internal_links_count", "external_links_count",
            "broken_backlinks", "broken_pages",
        ]
        ts_cols = ["first_seen", "lost_date"]
        stringify_cols = [
            "referring_links_tld", "referring_links_types",
            "referring_links_attributes", "referring_links_platform_types",
            "referring_links_semantic_locations", "referring_links_countries",
        ]

        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        for col in ts_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
        for col in stringify_cols:
            if col in df.columns:
                df[col] = df[col].apply(
                    lambda v: json.dumps(v) if isinstance(v, (list, dict)) else v
                )
        return df

    def _fetch_live(self, target: str, **kwargs) -> pd.DataFrame:
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
