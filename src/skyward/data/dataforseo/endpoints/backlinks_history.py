"""backlinks_history — v3/backlinks/history/live

Fetches historical month-by-month backlink metrics for a single target (domain or URL).
Returns multiple rows per target (one per monthly snapshot).
"""

from __future__ import annotations

import time

import pandas as pd

from skyward.data.dataforseo.base import BaseEndpoint


class BacklinksHistory(BaseEndpoint):
    LIVE_URL = "backlinks/history/live"
    TABLE_NAME = "backlinks-history"

    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        payload: dict = {
            "target": target,
        }
        if "date_from" in kwargs:
            payload["date_from"] = kwargs["date_from"]
        if "date_to" in kwargs:
            payload["date_to"] = kwargs["date_to"]
        return [payload]

    def plan(self, targets, *, endpoint_mode="live", **kwargs):
        n = len(targets)
        # Typically returns ~12 monthly snapshots per year of history requested.
        return self._make_plan(targets, endpoint_mode, requests=n, items=n, max_rows=n * 120)

    def _parse_response(self, response: dict, target: str, **kwargs) -> pd.DataFrame:
        try:
            task = response["tasks"][0]
            task_id = task.get("id", "")
            result_list = task.get("result") or []
        except (KeyError, IndexError):
            return pd.DataFrame(columns=self._get_schema() + ["task_id"])

        subject = kwargs.get("subject", "client")
        rows = []
        for result in result_list:
            target_str = result.get("target") or target
            stripped = target_str.split("://", 1)[-1] if target_str else ""
            target_type = "url" if "/" in stripped else "domain"
            items = result.get("items") or []
            for item in items:
                rows.append({
                    "target": target_str,
                    "target_type": target_type,
                    "subject": subject,
                    "date": item.get("date"),
                    "rank": item.get("rank"),
                    "backlinks": item.get("backlinks"),
                    "new_backlinks": item.get("new_backlinks"),
                    "lost_backlinks": item.get("lost_backlinks"),
                    "new_referring_domains": item.get("new_referring_domains"),
                    "lost_referring_domains": item.get("lost_referring_domains"),
                    "crawled_pages": item.get("crawled_pages"),
                    "internal_links_count": item.get("internal_links_count"),
                    "external_links_count": item.get("external_links_count"),
                    "broken_backlinks": item.get("broken_backlinks"),
                    "broken_pages": item.get("broken_pages"),
                    "referring_domains": item.get("referring_domains"),
                    "referring_domains_nofollow": item.get("referring_domains_nofollow"),
                    "referring_main_domains": item.get("referring_main_domains"),
                    "referring_main_domains_nofollow": item.get("referring_main_domains_nofollow"),
                    "referring_ips": item.get("referring_ips"),
                    "referring_subnets": item.get("referring_subnets"),
                    "referring_pages": item.get("referring_pages"),
                    "referring_pages_nofollow": item.get("referring_pages_nofollow"),
                    "task_id": task_id,
                })

        if not rows:
            return pd.DataFrame(columns=self._get_schema() + ["task_id"])
        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return [
            "target", "target_type", "subject", "date", "rank", "backlinks",
            "new_backlinks", "lost_backlinks",
            "new_referring_domains", "lost_referring_domains",
            "crawled_pages", "internal_links_count", "external_links_count",
            "broken_backlinks", "broken_pages",
            "referring_domains", "referring_domains_nofollow",
            "referring_main_domains", "referring_main_domains_nofollow",
            "referring_ips", "referring_subnets",
            "referring_pages", "referring_pages_nofollow",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["target", "date"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        int_cols = [
            "rank", "backlinks", "new_backlinks", "lost_backlinks",
            "new_referring_domains", "lost_referring_domains",
            "crawled_pages", "internal_links_count", "external_links_count",
            "broken_backlinks", "broken_pages",
            "referring_domains", "referring_domains_nofollow",
            "referring_main_domains", "referring_main_domains_nofollow",
            "referring_ips", "referring_subnets",
            "referring_pages", "referring_pages_nofollow",
        ]
        ts_cols = ["date"]

        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        for col in ts_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
        return df

    def _fetch_live(self, target: str, **kwargs) -> pd.DataFrame:
        cfg = self.config
        kwargs.pop("_debug_collector", None)
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
            tasks = resp.get("tasks") or []
            task = tasks[0] if tasks else {}
            status_code = task.get("status_code")
            if status_code is not None and status_code != 20000:
                if debug:
                    print(f"[{target}] DFS task status {status_code}: {task.get('status_message')}. Attempt {attempt}/{max_retries}")
                continue
            try:
                return self._parse_response(resp, target, **kwargs)
            except Exception as e:
                if debug:
                    print(f"[{target}] Parse error: {e}. Attempt {attempt}/{max_retries}")
                continue

        return pd.DataFrame(columns=self._get_schema() + ["task_id"])

