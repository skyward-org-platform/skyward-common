"""Shared helpers and paginated base class for DataForSEO Backlinks endpoints."""

from __future__ import annotations

import json
import math
import time
from typing import Any

import pandas as pd

from skyward.data.dataforseo.base import BaseEndpoint

VALID_SUBJECTS = ("client", "rival")
DFS_MAX_OFFSET = 20000

BACKLINK_JSON_MAP_COLS: tuple[str, ...] = (
    "referring_links_tld",
    "referring_links_types",
    "referring_links_attributes",
    "referring_links_platform_types",
    "referring_links_semantic_locations",
    "referring_links_countries",
)

BACKLINK_PAGINATED_INT_COLS: tuple[str, ...] = (
    "rank",
    "backlinks",
    "backlinks_spam_score",
    "broken_backlinks",
    "broken_pages",
    "referring_domains",
    "referring_domains_nofollow",
    "referring_main_domains",
    "referring_main_domains_nofollow",
    "referring_ips",
    "referring_subnets",
    "referring_pages",
    "referring_pages_nofollow",
)

BACKLINK_PAGINATED_TIMESTAMP_COLS: tuple[str, ...] = ("first_seen", "lost_date")

_OPTIONAL_PAYLOAD_KEYS: tuple[str, ...] = (
    "internal_list_limit",
    "backlinks_status_type",
    "include_subdomains",
    "exclude_internal_backlinks",
    "backlinks_filters",
    "filters",
    "order_by",
    "rank_scale",
    "tag",
)


def validate_subject(subject: str) -> str:
    """Validate and return the subject role tag ('client' or 'rival')."""
    if subject not in VALID_SUBJECTS:
        raise ValueError(f"subject must be one of {VALID_SUBJECTS!r}, got {subject!r}")
    return subject


def json_dumps_or_none(value: Any) -> str | None:
    """Serialize nested dict/list values to JSON string, preserving None."""
    return json.dumps(value) if value is not None else None


def extract_backlink_paginated_metrics(
    item: dict,
    *,
    target: str,
    task_id: str,
    subject: str,
    entity_col: str,
    entity_value: Any,
) -> dict[str, Any]:
    """Build one parsed row shared across backlinks/referring_domains and backlinks/anchors."""
    row: dict[str, Any] = {
        "subject": subject,
        "target": target,
        "type": item.get("type"),
        entity_col: entity_value,
    }
    for col in BACKLINK_PAGINATED_INT_COLS:
        row[col] = item.get(col)
    for col in BACKLINK_PAGINATED_TIMESTAMP_COLS:
        row[col] = item.get(col)
    for col in BACKLINK_JSON_MAP_COLS:
        row[col] = json_dumps_or_none(item.get(col))
    row["task_id"] = task_id
    return row


class PaginatedBacklinksEndpoint(BaseEndpoint):
    """Base for offset-paginated DataForSEO Backlinks endpoints (referring_domains, anchors)."""

    ITEM_KEY: str = ""
    ENTITY_COL: str = ""

    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        payload: dict[str, Any] = {
            "target": target,
            "limit": kwargs.get("limit", 1000),
            "offset": kwargs.get("offset", 0),
        }
        for key in _OPTIONAL_PAYLOAD_KEYS:
            value = kwargs.get(key)
            if value is not None:
                payload[key] = value
        return [payload]

    def plan(self, targets, *, endpoint_mode="live", **kwargs):
        page_size = min(kwargs.get("page_size", 1000), 1000)
        limit = kwargs.get("limit")
        per_target = DFS_MAX_OFFSET if limit is None else min(limit, DFS_MAX_OFFSET)
        pages = math.ceil(per_target / page_size) if per_target > 0 else 0
        n = len(targets)
        return self._make_plan(
            targets,
            endpoint_mode,
            requests=n * pages,
            items=n * per_target,
            max_rows=n * per_target,
            limit=limit,
            page_size=page_size,
        )

    def _get_schema(self) -> list[str]:
        return [
            "subject",
            "target",
            "type",
            self.ENTITY_COL,
            "rank",
            "backlinks",
            "first_seen",
            "lost_date",
            "backlinks_spam_score",
            "broken_backlinks",
            "broken_pages",
            "referring_domains",
            "referring_domains_nofollow",
            "referring_main_domains",
            "referring_main_domains_nofollow",
            "referring_ips",
            "referring_subnets",
            "referring_pages",
            "referring_pages_nofollow",
            *BACKLINK_JSON_MAP_COLS,
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["target", self.ENTITY_COL]

    def _parse_response(self, response: dict, target: str, **kwargs) -> pd.DataFrame:
        subject = validate_subject(kwargs.get("subject", "client"))
        try:
            task = response["tasks"][0]
            task_id = task.get("id", "")
            result = task["result"][0]
            items = result.get("items") or []
            echoed_target = result.get("target") or target
        except (KeyError, IndexError, TypeError):
            return pd.DataFrame(columns=self._get_schema() + ["task_id"])

        rows = [
            extract_backlink_paginated_metrics(
                item,
                target=echoed_target,
                task_id=task_id,
                subject=subject,
                entity_col=self.ENTITY_COL,
                entity_value=item.get(self.ITEM_KEY),
            )
            for item in items
        ]
        if not rows:
            return pd.DataFrame(columns=self._get_schema() + ["task_id"])
        return pd.DataFrame(rows)

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.copy()
        for col in BACKLINK_PAGINATED_INT_COLS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        for col in BACKLINK_PAGINATED_TIMESTAMP_COLS:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
        str_cols = ("subject", "target", "type", self.ENTITY_COL, *BACKLINK_JSON_MAP_COLS, "task_id")
        for col in str_cols:
            if col in df.columns:
                df[col] = df[col].astype("string")
        return df

    def _fetch_single_page(
        self,
        url: str,
        payload: list[dict],
        target: str,
        subject: str,
        *,
        max_retries: int,
        retry_delay: float,
        debug: bool,
    ) -> tuple[pd.DataFrame | None, int | None]:
        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                time.sleep(retry_delay)
            resp = self._client._post(url, payload, max_retries=1, retry_delay=0)
            if not resp:
                if debug:
                    print(f"[{target}] Invalid response. Attempt {attempt}/{max_retries}")
                continue
            try:
                tasks = resp.get("tasks") or []
                task = tasks[0] if tasks else {}
                status_code = task.get("status_code")
                if status_code is not None and status_code != 20000:
                    if debug:
                        print(f"[{target}] DFS task status {status_code}: {task.get('status_message')}")
                    return pd.DataFrame(columns=self._get_schema() + ["task_id"]), None
                results = task.get("result") or []
                total_count = results[0].get("total_count") if results else None
                df = self._parse_response(resp, target, subject=subject)
                return df, total_count
            except Exception as e:
                if debug:
                    print(f"[{target}] Parse error: {e}. Attempt {attempt}/{max_retries}")
                continue
        return None, None

    def _fetch_live(self, target: str, **kwargs) -> pd.DataFrame:
        cfg = self.config
        kwargs.pop("_debug_collector", None)
        subject = validate_subject(kwargs.pop("subject", "client"))
        max_retries = kwargs.pop("max_retries", cfg.max_retries)
        retry_delay = kwargs.pop("retry_delay", cfg.retry_delay)
        debug = kwargs.pop("debug", cfg.debug)

        limit = kwargs.pop("limit", None)
        if limit is not None and limit < 0:
            raise ValueError(f"limit must be >= 0 or None, got {limit}")
        page_size = min(kwargs.pop("page_size", 1000), 1000)
        if page_size < 1:
            raise ValueError(f"page_size must be >= 1, got {page_size}")
        offset = kwargs.pop("offset", 0)
        if limit == 0:
            return pd.DataFrame(columns=self._get_schema() + ["task_id"])

        url = f"{self._client.BASE_URL}/{self.LIVE_URL}"
        all_pages: list[pd.DataFrame] = []
        total_rows = 0

        while offset < DFS_MAX_OFFSET:
            remaining = (limit - total_rows) if limit is not None else page_size
            current_page_size = min(page_size, remaining, DFS_MAX_OFFSET - offset)
            if current_page_size <= 0:
                break
            payload = self._build_payload(
                target, limit=current_page_size, offset=offset, **kwargs
            )
            page_df, total_count = self._fetch_single_page(
                url,
                payload,
                target,
                subject,
                max_retries=max_retries,
                retry_delay=retry_delay,
                debug=debug,
            )
            if page_df is None or page_df.empty:
                break

            all_pages.append(page_df)
            total_rows += len(page_df)
            offset += len(page_df)

            if limit is not None and total_rows >= limit:
                break
            if total_count is not None and offset >= total_count:
                break
            if len(page_df) < current_page_size:
                break

        if all_pages:
            return pd.concat(all_pages, ignore_index=True)
        return pd.DataFrame(columns=self._get_schema() + ["task_id"])

