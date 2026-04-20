"""dataforseo_labs_google_search_intent — v3/dataforseo_labs/google/search_intent/live

Fetches search intent classification for up to 1000 keywords per request.
Returns intent label, probability, and secondary intents.
"""

from __future__ import annotations

import math
import time

import pandas as pd

from skyward.data.dataforseo.base import BaseEndpoint


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
            secondary = item.get("secondary_keyword_intents") or []
            secondary_str = ",".join(
                si.get("label", "") for si in secondary if si.get("label")
            ) if secondary else None

            rows.append({
                "keyword": item.get("keyword"),
                "search_intent": keyword_intent.get("label"),
                "intent_probability": keyword_intent.get("probability"),
                "secondary_intents": secondary_str,
                "language_code": item.get("language_code", language_code_val),
                "task_id": task_id,
            })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return [
            "keyword", "search_intent", "intent_probability",
            "secondary_intents", "language_code",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["keyword", "language_code"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        if "intent_probability" in df.columns:
            df["intent_probability"] = pd.to_numeric(df["intent_probability"], errors="coerce")

        for col in ["keyword", "search_intent", "secondary_intents", "language_code"]:
            if col in df.columns:
                df[col] = df[col].astype("string")

        return df

    def _fetch_live(self, target, **kwargs) -> pd.DataFrame:
        cfg = self.config
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

    def live_all(
        self,
        keywords: list[str],
        *,
        batch_size: int = 1000,
        batch_delay: float = 0.2,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Fetch search intent data for an arbitrary number of keywords.

        Chunks the keyword list into batches of up to 1000 and calls _fetch_live()
        for each batch sequentially.

        Args:
            keywords: Full list of keywords
            batch_size: Keywords per API call (max 1000)
            batch_delay: Delay in seconds between batches
            **kwargs: Passed to _fetch_live()

        Returns:
            Combined DataFrame of all results
        """
        batch_size = min(batch_size, 1000)
        total_batches = math.ceil(len(keywords) / batch_size)
        df_list: list[pd.DataFrame] = []

        if self.config.debug:
            print(f"Starting search_intent processing of {len(keywords)} keywords in {total_batches} batches of {batch_size}...")

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
