"""backlinks_backlinks — v3/backlinks/backlinks/live

Fetches individual backlinks pointing to a target URL or domain.
"""

from __future__ import annotations

import json
import time

import pandas as pd

from skyward.data.dataforseo.base import BaseEndpoint


class BacklinksBacklinks(BaseEndpoint):
    LIVE_URL = "backlinks/backlinks/live"
    TABLE_NAME = "backlinks-backlinks"

    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        return [{
            "target": target,
            "limit": kwargs.get("limit", 100),
            "filters": kwargs.get("filters") or [["dofollow", "=", True]],
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
            ranked_kw_info = item.get("ranked_keywords_info", {}) or {}
            rows.append({
                "url": target,
                "domain": item.get("domain_to"),
                "type": item.get("type"),
                "item_type": item.get("item_type"),
                "attributes": item.get("attributes"),
                "domain_from": item.get("domain_from"),
                "url_from": item.get("url_from"),
                "url_from_https": item.get("url_from_https"),
                "tld_from": item.get("tld_from"),
                "domain_from_rank": item.get("domain_from_rank"),
                "domain_from_platform_type": json.dumps(item.get("domain_from_platform_type")),
                "domain_from_is_ip": item.get("domain_from_is_ip"),
                "domain_from_ip": item.get("domain_from_ip"),
                "domain_from_country": item.get("domain_from_country"),
                "domain_to": item.get("domain_to"),
                "backlink_to": item.get("url_to"),
                "backlink_to_https": item.get("url_to_https"),
                "backlink_to_status_code": item.get("url_to_status_code"),
                "backlink_to_spam_score": item.get("url_to_spam_score"),
                "backlink_to_redirect_target": item.get("url_to_redirect_target"),
                "dofollow": item.get("dofollow"),
                "backlink_spam_score": item.get("backlink_spam_score"),
                "is_broken": item.get("is_broken"),
                "is_indirect_link": item.get("is_indirect_link"),
                "indirect_link_path": json.dumps(item.get("indirect_link_path")),
                "anchor": item.get("anchor"),
                "alt": item.get("alt"),
                "image_url": item.get("image_url"),
                "text_pre": item.get("text_pre"),
                "text_post": item.get("text_post"),
                "semantic_location": item.get("semantic_location"),
                "first_seen": item.get("first_seen"),
                "prev_seen": item.get("prev_seen"),
                "last_seen": item.get("last_seen"),
                "is_new": item.get("is_new"),
                "is_lost": item.get("is_lost"),
                "rank": item.get("rank"),
                "page_from_rank": item.get("page_from_rank"),
                "page_from_keywords_count_top_3": ranked_kw_info.get("page_from_keywords_count_top_3"),
                "page_from_keywords_count_top_10": ranked_kw_info.get("page_from_keywords_count_top_10"),
                "page_from_keywords_count_top_100": ranked_kw_info.get("page_from_keywords_count_top_100"),
                "page_from_title": item.get("page_from_title"),
                "page_from_status_code": item.get("page_from_status_code"),
                "page_from_external_links": item.get("page_from_external_links"),
                "page_from_internal_links": item.get("page_from_internal_links"),
                "page_from_size": item.get("page_from_size"),
                "page_from_encoding": item.get("page_from_encoding"),
                "page_from_language": item.get("page_from_language"),
                "links_count": item.get("links_count"),
                "group_count": item.get("group_count"),
                "task_id": task_id,
            })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return [
            "url", "domain", "type", "item_type", "attributes",
            "domain_from", "url_from", "url_from_https", "tld_from",
            "domain_from_rank", "domain_from_platform_type", "domain_from_is_ip",
            "domain_from_ip", "domain_from_country", "domain_to",
            "backlink_to", "backlink_to_https", "backlink_to_status_code",
            "backlink_to_spam_score", "backlink_to_redirect_target",
            "dofollow", "backlink_spam_score", "is_broken", "is_indirect_link",
            "indirect_link_path", "anchor", "alt", "image_url",
            "text_pre", "text_post", "semantic_location",
            "first_seen", "prev_seen", "last_seen", "is_new", "is_lost",
            "rank", "page_from_rank",
            "page_from_keywords_count_top_3", "page_from_keywords_count_top_10",
            "page_from_keywords_count_top_100", "page_from_title",
            "page_from_status_code", "page_from_external_links",
            "page_from_internal_links", "page_from_size",
            "page_from_encoding", "page_from_language",
            "links_count", "group_count",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["url_from", "backlink_to", "item_type", "anchor"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = [
            "domain_from_rank", "backlink_to_status_code", "backlink_to_spam_score",
            "backlink_spam_score", "rank", "page_from_rank",
            "page_from_keywords_count_top_3", "page_from_keywords_count_top_10",
            "page_from_keywords_count_top_100", "page_from_status_code",
            "page_from_external_links", "page_from_internal_links",
            "page_from_size", "links_count", "group_count",
        ]
        bool_cols = [
            "url_from_https", "domain_from_is_ip", "backlink_to_https",
            "dofollow", "is_broken", "is_indirect_link", "is_new", "is_lost",
        ]
        ts_cols = ["first_seen", "prev_seen", "last_seen"]

        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        for col in bool_cols:
            if col in df.columns:
                df[col] = df[col].astype("boolean")
        for col in ts_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

        stringify_cols = ["attributes", "domain_from_platform_type", "indirect_link_path"]
        for col in stringify_cols:
            if col in df.columns:
                df[col] = df[col].apply(
                    lambda v: json.dumps(v) if isinstance(v, (list, dict)) else v
                )
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
