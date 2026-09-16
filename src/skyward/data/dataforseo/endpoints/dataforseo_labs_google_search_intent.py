"""dataforseo_labs_google_search_intent — v3/dataforseo_labs/google/search_intent/live

Fetches search intent classification for up to 1000 keywords per request.
Returns intent label, probability, and secondary intents.
"""

from __future__ import annotations

import asyncio
import functools
import json
import math
import time
from typing import Any

import pandas as pd

from skyward.data.dataforseo.base import _UNSET, BaseEndpoint
from skyward.data.dataforseo.run import DEFAULT_BALANCE_BUFFER
from skyward.functions import _validate_job_id


class DataforseoLabsGoogleSearchIntent(BaseEndpoint):
    LIVE_URL = "dataforseo_labs/google/search_intent/live"
    POST_URL = None
    TABLE_NAME = "dataforseo_labs-google-search_intent"
    DATASET = "DataForSEO"

    def _build_payload(self, target: str | list[str], **kwargs) -> list[dict]:
        keywords = target if isinstance(target, list) else [target]
        return [{
            "keywords": keywords,
            "language_code": kwargs.get("language_code", self.config.language_code),
        }]

    def plan(self, targets, *, endpoint_mode="live", **kwargs):
        batch = min(kwargs.get("batch_size") or 1000, 1000)
        n = len(targets)
        return self._make_plan(targets, endpoint_mode, requests=math.ceil(n / batch),
                               items=n, max_rows=n, batch_size=batch)

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

        language_code_val = results[0].get("language_code") if results[0] else None

        rows = []
        for item in items:
            keyword_intent = item.get("keyword_intent") or {}
            # Preserve the full [{label, probability}, ...] structure so probability
            # ranking — the primary analytic signal for intent-mapping workflows —
            # isn't lost. Stringified to JSON in _cast_types. `None` passes through.
            secondary_keyword_intents = item.get("secondary_keyword_intents")

            rows.append({
                "keyword": item.get("keyword"),
                "search_intent": keyword_intent.get("label"),
                "intent_probability": keyword_intent.get("probability"),
                "secondary_keyword_intents": secondary_keyword_intents,
                "language_code": item.get("language_code", language_code_val),
                "task_id": task_id,
            })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return [
            "keyword", "search_intent", "intent_probability",
            "secondary_keyword_intents", "language_code",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["keyword", "language_code"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        if "intent_probability" in df.columns:
            df["intent_probability"] = pd.to_numeric(df["intent_probability"], errors="coerce")

        stringify_cols = ["secondary_keyword_intents"]
        for col in stringify_cols:
            if col in df.columns:
                df[col] = df[col].apply(
                    lambda v: json.dumps(v) if isinstance(v, (list, dict)) else v
                )

        for col in ["keyword", "search_intent", "secondary_keyword_intents", "language_code"]:
            if col in df.columns:
                df[col] = df[col].astype("string")

        return df

    def _fetch_live(self, target, **kwargs) -> pd.DataFrame:
        cfg = self.config
        # TODO(debug-logs): wire run-scoped collector into this loop (ClickUp 86babz7xp).
        kwargs.pop("_debug_collector", None)  # accepted, not yet captured
        max_retries = kwargs.pop("max_retries", cfg.max_retries)
        retry_delay = kwargs.pop("retry_delay", cfg.retry_delay)
        debug = kwargs.pop("debug", cfg.debug)

        keywords = target if isinstance(target, list) else [target]

        url = f"{self._client.BASE_URL}/{self.LIVE_URL}"
        payload = self._build_payload(keywords, **kwargs)

        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                time.sleep(retry_delay)

            resp = self._client._post(url, payload, max_retries=1, retry_delay=0)

            if not resp:
                if debug:
                    print(f"[search_intent] Invalid response. Attempt {attempt}/{max_retries}")
                continue

            try:
                df = self._parse_response(resp, keywords)
                if not df.empty:
                    return df
                if debug:
                    print(f"[search_intent] Empty result. Attempt {attempt}/{max_retries}")
            except Exception as e:
                if debug:
                    print(f"[search_intent] Parse error: {e}. Attempt {attempt}/{max_retries}")
                continue

        return pd.DataFrame(columns=self._get_schema() + ["task_id"])

    async def live_all(self, *args, **kwargs) -> pd.DataFrame:
        """Async wrapper — delegates to _live_all_sync in a thread executor
        so callers can `await` uniformly across all endpoints."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(self._live_all_sync, *args, **kwargs),
        )

    def _live_all_sync(
        self,
        keywords: list[str],
        *,
        domain: Any = _UNSET,
        domain_id: Any = _UNSET,
        job_id: str,
        interactive: bool = False,
        upload: bool = True,
        batch_size: int = 1000,
        batch_delay: float = 0.2,
        balance_buffer: float = DEFAULT_BALANCE_BUFFER,
        ignore_balance_check: bool = False,
        ignore_location_check: bool = False,
        upload_batch_rows: int | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        """Chunks keywords into batches of up to 1000 and fetches each sequentially.

        Every request is cost-logged; rows are saved in windows and the combined
        DataFrame is returned."""
        _validate_job_id(job_id)
        resolved = self._resolve_domain(domain, domain_id, interactive)

        batch_size = min(batch_size, 1000)
        total_batches = math.ceil(len(keywords) / batch_size)

        run = self._start_run(
            list(keywords), job_id=job_id, resolved=resolved, endpoint_mode="live",
            upload=upload, balance_buffer=balance_buffer,
            ignore_balance_check=ignore_balance_check,
            ignore_location_check=ignore_location_check, upload_batch_rows=upload_batch_rows,
            plan_kwargs={**kwargs, "batch_size": batch_size},
            empty_columns=self._get_schema() + ["domain_id", "domain", "endpoint_mode"],
        )

        if self.config.debug:
            print(f"Starting search_intent processing of {len(keywords)} keywords in {total_batches} batches of {batch_size}...")

        try:
            for idx, chunk in enumerate(self._client._chunked(keywords, batch_size), start=1):
                if self.config.debug:
                    print(f"Processing batch {idx}/{total_batches} ({len(chunk)} keywords)")
                run.run_unit(chunk, lambda c=chunk: self._fetch_live(c, **kwargs))
                if idx < total_batches:
                    time.sleep(batch_delay)
        except BaseException as exc:
            run.close(error=exc)
            raise
        return run.close()
