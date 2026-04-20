"""
DataForSEO API Client - Refactored Class-Based Interface

This module provides a clean, class-based interface to the DataForSEO API where each
endpoint has standardized methods: live(), live_all(), post(), post_all(), upload().

Usage:
    from skyward.data.dataforseo import DataForSEOClient, ClientConfig

    client = DataForSEOClient(
        username=cfg.dataforseo_username,
        password=cfg.dataforseo_password,
        config=ClientConfig(location_code=2840, debug=True),
    )

    # Backlinks (individual)
    df = client.backlinks_backlinks.live("https://example.com", limit=100)
    df = await client.backlinks_backlinks.live_all(urls, batch_size=10)
    client.backlinks_backlinks.upload(bq_client, df, job_id="job-123")

    # Backlinks (bulk summary — up to 1000 targets per call)
    df = await client.backlinks_bulk_pages_summary.live_all(urls, batch_size=1000)
    client.backlinks_bulk_pages_summary.upload(bq_client, df, job_id="job-123")

    # SERP with POST/GET workflow
    df = await client.serp_google_organic.post_all(keywords)
    paa_df = client.serp_google_organic.extract_paa(df)
"""

from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generator, List, Optional, TYPE_CHECKING

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3.util.retry import Retry
from queue import Queue, Empty
from threading import Thread, Lock

if TYPE_CHECKING:
    from google.cloud import bigquery
    from skyward.data.bigquery import BigQueryClient


# =============================================================================
# TODO: BigQuery Table Renames
# =============================================================================
# The following tables need to be renamed to remove the "live" / "live_advanced"
# suffix since they are not dependent on the endpoint type:
#
# | Current Table Name                          | New Table Name                      |
# |---------------------------------------------|-------------------------------------|
# | backlinks_backlinks_live                    | backlinks_backlinks                 |
# | backlinks_bulk_pages_summary_live           | backlinks_bulk_pages_summary        |
# | serp_google_organic_live_advanced           | serp_google_organic                 |
# | google_keyword-suggestions_live             | google_keyword-suggestions          |
# | google_related-keywords_live                | google_related-keywords             |
#
# Tables that don't need renaming (no "live" suffix):
# - dataforseo_labs-google-ranked_keywords
# - keywords_data_google_ads_search_volume
#
# After renaming in BigQuery, update TABLE_NAME in each endpoint class below.
# =============================================================================

# =============================================================================
# Configuration
# =============================================================================


@dataclass
class ClientConfig:
    """Configuration for DataForSEO client behavior."""

    location_code: int = 2840  # Default: United States
    language_code: str = "en"
    max_retries: int = 5
    retry_delay: int = 5
    batch_size: int = 10
    batch_delay: float = 1.0
    debug: bool = False
    # Task-mode (POST/GET) timeouts — Phase 1 default 2h. Phase 2 will change
    # timeout behavior from "raise" to "hand off to detached mode"; these
    # fields are likely to remain.
    task_total_timeout: int = 7200  # 2 hours in seconds
    task_poll_interval: int = 30  # seconds between tasks_ready polls


# =============================================================================
# Parent Client
# =============================================================================


class DataForSEOClient:
    """
    Parent client that provides access to all DataForSEO API endpoints.

    Each endpoint is accessed via a property that returns a lazily-initialized
    endpoint class with standardized methods: live(), live_all(), post(),
    post_all(), and upload().
    """

    BASE_URL = "https://api.dataforseo.com/v3"

    def __init__(
        self,
        username: str,
        password: str,
        config: ClientConfig | None = None,
        bq_client: "BigQueryClient | None" = None,
    ):
        if not username or not password:
            raise RuntimeError(
                "Missing DataForSEO credentials. Set DATAFORSEO_API_LOGIN and "
                "DATAFORSEO_API_PASSWORD in your environment or .env file."
            )

        self._username = username
        self._password = password
        self._auth = HTTPBasicAuth(username, password)
        self.config = config or ClientConfig()
        self.bq_client = bq_client
        self._meta_client = None

        # Create default session with retry logic
        self._session = self._create_session()

        # Lazy-initialized endpoint instances (imported on first property access)
        self._backlinks_backlinks = None
        self._backlinks_bulk_pages_summary = None
        self._backlinks_summary = None
        self._serp_google_organic = None
        self._dataforseo_labs_google_keyword_suggestions = None
        self._dataforseo_labs_google_related_keywords = None
        self._dataforseo_labs_google_ranked_keywords = None
        self._dataforseo_labs_google_keyword_overview = None
        self._dataforseo_labs_google_search_intent = None
        self._dataforseo_labs_google_domain_rank_overview = None
        self._keywords_data_google_ads_search_volume = None

    @property
    def meta_client(self):
        if self.bq_client is None:
            return None
        if self._meta_client is None:
            from skyward.data.meta import MetaClient
            self._meta_client = MetaClient(self.bq_client)
        return self._meta_client

    # -------------------------------------------------------------------------
    # Low-level HTTP methods
    # -------------------------------------------------------------------------

    def _create_session(self) -> requests.Session:
        """Create a new session with auth and retry logic for 429/5xx errors."""
        session = requests.Session()
        session.auth = self._auth
        retry = Retry(
            total=self.config.max_retries,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session

    def _post(
        self,
        endpoint: str,
        payload: list,
        session: requests.Session | None = None,
        max_retries: int | None = None,
        retry_delay: int | None = None,
    ) -> dict | None:
        """Send a POST request to a DataForSEO endpoint with retry logic."""
        sess = session or self._session
        max_retries = max_retries or self.config.max_retries
        retry_delay = retry_delay or self.config.retry_delay

        for attempt in range(max_retries):
            try:
                resp = sess.post(endpoint, json=payload, timeout=30)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if self.config.debug:
                    print(f"Attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
        return None

    def _get(
        self,
        endpoint: str,
        session: requests.Session | None = None,
        max_retries: int | None = None,
        retry_delay: int | None = None,
    ) -> dict | None:
        """Send a GET request to a DataForSEO endpoint with retry logic."""
        sess = session or self._session
        max_retries = max_retries or self.config.max_retries
        retry_delay = retry_delay or self.config.retry_delay

        for attempt in range(max_retries):
            try:
                resp = sess.get(endpoint, timeout=30)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if self.config.debug:
                    print(f"Attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
        return None

    # -------------------------------------------------------------------------
    # POST/GET workflow methods (generic)
    # -------------------------------------------------------------------------

    def get_task_result(
        self,
        url: str,
        session: requests.Session | None = None,
    ) -> tuple[dict | None, str | None]:
        """
        GET a task result and handle common POST/GET workflow status codes.

        Args:
            url: Full URL to task_get endpoint (with task_id)
            session: Optional session (uses default if not provided)

        Returns:
            (task_data, None) - success, task_data contains result to parse
            (None, None) - not ready, caller should retry
            (None, "error message") - permanent error
        """
        data = self._get(url, session, max_retries=1, retry_delay=0)

        if not data:
            return (None, None)  # Treat as not ready

        tasks_list = data.get("tasks", [])
        if not tasks_list:
            return (None, None)  # Not ready

        task_data = tasks_list[0]
        status = task_data.get("status_code", 0)

        # Not ready - task still processing
        # 40601 = "Task Handed" - received but not yet queued
        # 40602 = "Task In Queue" - queued but not yet processed
        # 40202 = "Rate-limit per minute exceeded"
        if status in (40601, 40602, 40202):
            return (None, None)

        # Permanent error - task not found
        if status == 40402:
            return (None, "task_not_found (40402)")

        # Other error
        if status != 20000:
            return (None, f"status_{status}")

        # Success - check if result has data
        result = task_data.get("result", [])
        if not result or result[0] is None:
            return (None, None)  # Not ready

        items = result[0].get("items")
        if items is None:
            return (None, None)  # Not ready

        # Success with data - return task_data for endpoint to parse
        return (task_data, None)

    def tasks_ready(
        self,
        endpoint: str,
        session: requests.Session | None = None,
    ) -> list[str]:
        """
        Check which tasks are ready for any POST/GET endpoint.

        Args:
            endpoint: Base endpoint path (e.g., "serp/google/organic")

        Returns:
            List of task IDs that are ready (up to 1000 per call)
        """
        url = f"{self.BASE_URL}/{endpoint}/tasks_ready"
        data = self._get(url, session, max_retries=1, retry_delay=0)

        if not data:
            return []

        ready_ids = []
        for task in data.get("tasks", []):
            result = task.get("result")
            if result:
                for item in result:
                    task_id = item.get("id")
                    if task_id:
                        ready_ids.append(task_id)
        return ready_ids

    def tasks_fixed(
        self,
        endpoint: str,
        session: requests.Session | None = None,
    ) -> list[dict]:
        """
        Check which tasks failed/errored for any POST/GET endpoint.

        Args:
            endpoint: Base endpoint path (e.g., "serp/google/organic")

        Returns:
            List of dicts with {id, status_code, status_message}
        """
        url = f"{self.BASE_URL}/{endpoint}/tasks_fixed"
        data = self._get(url, session, max_retries=1, retry_delay=0)

        if not data:
            return []

        fixed_tasks = []
        for task in data.get("tasks", []):
            result = task.get("result")
            if result:
                for item in result:
                    fixed_tasks.append({
                        "id": item.get("id"),
                        "status_code": item.get("status_code"),
                        "status_message": item.get("status_message"),
                    })
        return fixed_tasks

    # -------------------------------------------------------------------------
    # Utility methods
    # -------------------------------------------------------------------------

    @staticmethod
    def _chunked(iterable: List[str], size: int) -> Generator[List[str], None, None]:
        """Yield successive chunks of length `size` from iterable."""
        for i in range(0, len(iterable), size):
            yield iterable[i : i + size]

    # -------------------------------------------------------------------------
    # Endpoint properties (lazy initialization)
    # -------------------------------------------------------------------------

    @property
    def backlinks_backlinks(self):
        """Access the backlinks/backlinks/live endpoint."""
        if self._backlinks_backlinks is None:
            from skyward.data.dataforseo.endpoints.backlinks_backlinks import BacklinksBacklinks
            self._backlinks_backlinks = BacklinksBacklinks(self)
        return self._backlinks_backlinks

    @property
    def backlinks_bulk_pages_summary(self):
        """Access the backlinks/bulk_pages_summary/live endpoint."""
        if self._backlinks_bulk_pages_summary is None:
            from skyward.data.dataforseo.endpoints.backlinks_bulk_pages_summary import BacklinksBulkPagesSummary
            self._backlinks_bulk_pages_summary = BacklinksBulkPagesSummary(self)
        return self._backlinks_bulk_pages_summary

    @property
    def backlinks_summary(self):
        """Access the backlinks/summary/live endpoint."""
        if self._backlinks_summary is None:
            from skyward.data.dataforseo.endpoints.backlinks_summary import BacklinksSummary
            self._backlinks_summary = BacklinksSummary(self)
        return self._backlinks_summary

    @property
    def serp_google_organic(self):
        """Access the serp/google/organic endpoint (live and POST/GET)."""
        if self._serp_google_organic is None:
            from skyward.data.dataforseo.endpoints.serp_google_organic import SerpGoogleOrganic
            self._serp_google_organic = SerpGoogleOrganic(self)
        return self._serp_google_organic

    @property
    def dataforseo_labs_google_keyword_suggestions(self):
        """Access the dataforseo_labs/google/keyword_suggestions/live endpoint."""
        if self._dataforseo_labs_google_keyword_suggestions is None:
            from skyward.data.dataforseo.endpoints.dataforseo_labs_google_keyword_suggestions import DataforseoLabsGoogleKeywordSuggestions
            self._dataforseo_labs_google_keyword_suggestions = DataforseoLabsGoogleKeywordSuggestions(self)
        return self._dataforseo_labs_google_keyword_suggestions

    @property
    def dataforseo_labs_google_related_keywords(self):
        """Access the dataforseo_labs/google/related_keywords/live endpoint."""
        if self._dataforseo_labs_google_related_keywords is None:
            from skyward.data.dataforseo.endpoints.dataforseo_labs_google_related_keywords import DataforseoLabsGoogleRelatedKeywords
            self._dataforseo_labs_google_related_keywords = DataforseoLabsGoogleRelatedKeywords(self)
        return self._dataforseo_labs_google_related_keywords

    @property
    def dataforseo_labs_google_ranked_keywords(self):
        """Access the dataforseo_labs/google/ranked_keywords/live endpoint."""
        if self._dataforseo_labs_google_ranked_keywords is None:
            from skyward.data.dataforseo.endpoints.dataforseo_labs_google_ranked_keywords import DataforseoLabsGoogleRankedKeywords
            self._dataforseo_labs_google_ranked_keywords = DataforseoLabsGoogleRankedKeywords(self)
        return self._dataforseo_labs_google_ranked_keywords

    @property
    def dataforseo_labs_google_keyword_overview(self):
        """Access the dataforseo_labs/google/keyword_overview/live endpoint."""
        if self._dataforseo_labs_google_keyword_overview is None:
            from skyward.data.dataforseo.endpoints.dataforseo_labs_google_keyword_overview import DataforseoLabsGoogleKeywordOverview
            self._dataforseo_labs_google_keyword_overview = DataforseoLabsGoogleKeywordOverview(self)
        return self._dataforseo_labs_google_keyword_overview

    @property
    def dataforseo_labs_google_search_intent(self):
        """Access the dataforseo_labs/google/search_intent/live endpoint."""
        if self._dataforseo_labs_google_search_intent is None:
            from skyward.data.dataforseo.endpoints.dataforseo_labs_google_search_intent import DataforseoLabsGoogleSearchIntent
            self._dataforseo_labs_google_search_intent = DataforseoLabsGoogleSearchIntent(self)
        return self._dataforseo_labs_google_search_intent

    @property
    def dataforseo_labs_google_domain_rank_overview(self):
        """Access the dataforseo_labs/google/domain_rank_overview/live endpoint."""
        if self._dataforseo_labs_google_domain_rank_overview is None:
            from skyward.data.dataforseo.endpoints.dataforseo_labs_google_domain_rank_overview import DataforseoLabsGoogleDomainRankOverview
            self._dataforseo_labs_google_domain_rank_overview = DataforseoLabsGoogleDomainRankOverview(self)
        return self._dataforseo_labs_google_domain_rank_overview

    @property
    def keywords_data_google_ads_search_volume(self):
        """Access the keywords_data/google_ads/search_volume/live endpoint."""
        if self._keywords_data_google_ads_search_volume is None:
            from skyward.data.dataforseo.endpoints.keywords_data_google_ads_search_volume import KeywordsDataGoogleAdsSearchVolume
            self._keywords_data_google_ads_search_volume = KeywordsDataGoogleAdsSearchVolume(self)
        return self._keywords_data_google_ads_search_volume

    # -------------------------------------------------------------------------
    # Legacy compatibility properties
    # -------------------------------------------------------------------------

    @property
    def usr(self) -> str:
        """Legacy compatibility: username."""
        return self._username

    @property
    def pwd(self) -> str:
        """Legacy compatibility: password."""
        return self._password

    def post(self, endpoint: str, payload: list) -> dict | None:
        """Legacy compatibility: POST method."""
        return self._post(endpoint, payload)

    # -------------------------------------------------------------------------
    # Location helper methods
    # -------------------------------------------------------------------------

    def get_serp_locations(self) -> list[dict]:
        """
        Retrieve all SERP-supported locations from DataForSEO.

        Returns:
            List of location entries with keys like 'location_code', 'location_name',
            'location_type', 'country_iso_code', etc. Returns empty list on failure.
        """
        url = f"{self.BASE_URL}/serp/google/locations"
        resp = self._get(url)
        if resp:
            try:
                return resp["tasks"][0]["result"]
            except (KeyError, IndexError):
                pass
        return []

    def find_code(self, name: str, location_list: list[dict]) -> list[dict]:
        """
        Search for a location by name in the location list.

        Args:
            name: Name or partial name to search (e.g., "New York")
            location_list: List of location dictionaries from get_serp_locations()

        Returns:
            All matching entries with 'location_name' and 'location_code'
        """
        name_lower = name.lower()
        return [
            loc for loc in location_list
            if name_lower in loc["location_name"].lower()
        ]

    def find_location_name(self, code: int) -> str | None:
        """
        Return the human-readable location name for a given location code.

        Args:
            code: The DataForSEO location code to search for

        Returns:
            The matching location name, or None if not found
        """
        locations = self.get_serp_locations()
        for location in locations:
            if location["location_code"] == code:
                return location["location_name"]
        return None

    def find_city_code(self, city_name: str, location_list: list[dict]) -> list[dict]:
        """
        Search for a city by name in the location list.

        Args:
            city_name: Name or partial name of the city (e.g., "New York")
            location_list: List of location dictionaries from get_serp_locations()

        Returns:
            All matching city entries
        """
        city_name_lower = city_name.lower()
        return [
            loc for loc in location_list
            if loc["location_type"].lower() == "city"
            and city_name_lower in loc["location_name"].split(",")[0].lower()
        ]

    def find_state_code(self, state_name: str, location_list: list[dict]) -> list[dict]:
        """
        Search for a state by name in the location list.

        Args:
            state_name: Name or partial name of the state (e.g., "Texas")
            location_list: List of location dictionaries from get_serp_locations()

        Returns:
            All matching state entries
        """
        state_name_lower = state_name.lower()
        return [
            loc for loc in location_list
            if loc["location_type"].lower() == "state"
            and state_name_lower in loc["location_name"].lower()
        ]

    def find_country_code(self, country_name: str, location_list: list[dict]) -> list[dict]:
        """
        Search for a country by name in the location list.

        Args:
            country_name: Name or partial name of the country (e.g., "United States")
            location_list: List of location dictionaries from get_serp_locations()

        Returns:
            All matching country entries
        """
        country_name_lower = country_name.lower()
        return [
            loc for loc in location_list
            if loc["location_type"].lower() == "country"
            and country_name_lower in loc["location_name"].lower()
        ]

# =============================================================================
# Base Endpoint Class
# =============================================================================


class BaseEndpoint(ABC):
    """
    Abstract base class for all DataForSEO endpoint implementations.

    Subclasses must define:
        - LIVE_URL: The live endpoint URL path (relative to BASE_URL)
        - TABLE_NAME: BigQuery table name for uploads
        - _build_payload(): Build the request payload
        - _parse_response(): Parse the API response into a DataFrame
        - _get_schema(): Return the column schema
        - _get_dedupe_keys(): Return keys for BigQuery MERGE deduplication
        - _cast_types(): Cast DataFrame columns to proper types
    """

    LIVE_URL: str
    POST_URL: str | None = None  # None if POST/GET not supported
    READY_URL: str | None = None
    GET_URL: str | None = None
    TABLE_NAME: str
    DATASET: str = "DataForSEO"

    def __init__(self, client: DataForSEOClient):
        self._client = client

    @property
    def config(self) -> ClientConfig:
        return self._client.config

    # -------------------------------------------------------------------------
    # Abstract methods (must be implemented by subclasses)
    # -------------------------------------------------------------------------

    @abstractmethod
    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        """Build the API request payload for a single target."""
        pass

    @abstractmethod
    def _parse_response(self, response: dict, target: str) -> pd.DataFrame:
        """Parse the API response into a DataFrame."""
        pass

    @abstractmethod
    def _get_schema(self) -> list[str]:
        """Return the list of column names for empty DataFrame creation."""
        pass

    @abstractmethod
    def _get_dedupe_keys(self) -> list[str]:
        """Return the keys used for BigQuery MERGE deduplication."""
        pass

    @abstractmethod
    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        """Cast DataFrame columns to proper types for BigQuery upload."""
        pass

    # -------------------------------------------------------------------------
    # Standard implementations
    # -------------------------------------------------------------------------

    def live(
        self,
        target: str,
        *,
        location_code: int | None = None,
        language_code: str | None = None,
        limit: int = 100,
        filters: list | None = None,
        max_retries: int | None = None,
        retry_delay: int | None = None,
        debug: bool | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Fetch data for a single target using the live endpoint.

        Args:
            target: The target URL, keyword, or domain to query
            location_code: DataForSEO location code (default from config)
            language_code: DataForSEO language code (default from config)
            limit: Maximum results to return
            filters: Optional API filters
            max_retries: Override default retry count
            retry_delay: Override default retry delay
            debug: Override default debug setting
            **kwargs: Additional endpoint-specific parameters

        Returns:
            DataFrame with results, or empty DataFrame on failure
        """
        location_code = location_code or self.config.location_code
        language_code = language_code or self.config.language_code
        max_retries = max_retries or self.config.max_retries
        retry_delay = retry_delay or self.config.retry_delay
        debug = debug if debug is not None else self.config.debug

        url = f"{self._client.BASE_URL}/{self.LIVE_URL}"
        payload = self._build_payload(
            target,
            location_code=location_code,
            language_code=language_code,
            limit=limit,
            filters=filters,
            **kwargs,
        )

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
                if debug:
                    print(f"[{target}] Empty result. Attempt {attempt}/{max_retries}")
            except Exception as e:
                if debug:
                    print(f"[{target}] Parse error: {e}. Attempt {attempt}/{max_retries}")
                continue

        return pd.DataFrame(columns=self._get_schema())

    async def live_all(
        self,
        targets: list[str],
        *,
        batch_size: int | None = None,
        batch_delay: float | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Fetch data for multiple targets concurrently using the live endpoint.

        Args:
            targets: List of targets to query
            batch_size: Number of concurrent requests per batch
            batch_delay: Delay between batches in seconds
            **kwargs: Passed to live() for each target

        Returns:
            Combined DataFrame of all results
        """
        batch_size = batch_size or self.config.batch_size
        batch_delay = batch_delay if batch_delay is not None else self.config.batch_delay

        total = len(targets)
        total_batches = math.ceil(total / batch_size)

        if self.config.debug:
            print(f"Starting {self.__class__.__name__} processing of {total} targets in batches of {batch_size}...")

        loop = asyncio.get_running_loop()
        df_list: list[pd.DataFrame] = []

        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            for idx, batch in enumerate(self._client._chunked(targets, batch_size), start=1):
                if self.config.debug:
                    print(f"Processing batch {idx}/{total_batches} ({len(batch)} targets)")

                tasks = [
                    loop.run_in_executor(executor, lambda t=t: self.live(t, **kwargs))
                    for t in batch
                ]

                batch_results = await asyncio.gather(*tasks)

                for df in batch_results:
                    if isinstance(df, pd.DataFrame) and not df.empty:
                        df_list.append(df)

                if idx < total_batches:
                    await asyncio.sleep(batch_delay)

        if df_list:
            return pd.concat(df_list, ignore_index=True)
        return pd.DataFrame(columns=self._get_schema())

    def post(self, target: str, **kwargs) -> pd.DataFrame:
        """
        Submit a single target using the POST/GET workflow.

        Raises NotImplementedError if the endpoint doesn't support POST/GET.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support POST/GET workflow")

    async def post_all(self, targets: list[str], **kwargs) -> pd.DataFrame:
        """
        Submit multiple targets using the POST/GET workflow.

        Raises NotImplementedError if the endpoint doesn't support POST/GET.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support POST/GET workflow")

    def upload(
        self,
        bq_client: "BigQueryClient",
        df: pd.DataFrame,
        job_id: str,
        client_id: str = None,
    ) -> None:
        """
        Upload DataFrame to BigQuery with append-only semantics.

        Args:
            bq_client: BigQueryClient instance
            df: DataFrame to upload
            job_id: Job identifier for tracking
            client_id: Optional client identifier for log tracking
        """
        from google.cloud import bigquery

        if df is None or df.empty:
            print("Skipping upload - DataFrame is empty.")
            return

        df = df.copy()

        # Add metadata columns
        if "ingest_timestamp" not in df.columns:
            df["ingest_timestamp"] = pd.Timestamp.utcnow()
        df["ingest_timestamp"] = pd.to_datetime(df["ingest_timestamp"], utc=True)

        upload_id = str(uuid.uuid4())
        df["upload_id"] = upload_id
        df["job_id"] = job_id

        # Cast types
        df = self._cast_types(df)

        # Prepare identifiers
        full_table_id = f"{bq_client.client.project}.{self.DATASET}.{self.TABLE_NAME}"
        row_count = len(df)
        timestamp = df["ingest_timestamp"].iloc[0]

        try:
            # Check table exists
            try:
                bq_client.client.get_table(full_table_id)
            except Exception:
                print(f"Table {full_table_id} does not exist. Create it before uploading.")
                return

            # Append the full run so job_id/upload_id remain complete run snapshots.
            job_config = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_APPEND)
            load_job = bq_client.client.load_table_from_dataframe(df, full_table_id, job_config=job_config)
            load_job.result()

            # Log upload
            bq_client.log_upload_event(
                job_id=job_id,
                upload_id=upload_id,
                source="dataforseo",
                source_program=f"upload_{self.__class__.__name__.lower()}",
                dataset=self.DATASET,
                table=self.TABLE_NAME,
                row_count=row_count,
                timestamp=timestamp,
                client_id=client_id,
            )
            print(f"Upload complete: {row_count} rows appended into {full_table_id}.")

        except Exception as e:
            print(f"Upload failed: {e}")

    def _build_merge_query(
        self,
        full_table_id: str,
        temp_table: str,
        columns: list[str],
    ) -> str:
        """Deprecated helper kept temporarily for compatibility."""
        dedupe_keys = self._get_dedupe_keys()
        on_clause = " AND ".join([f"T.{k} = S.{k}" for k in dedupe_keys])
        col_list = ", ".join(columns)
        val_list = ", ".join([f"S.{c}" for c in columns])

        return f"""
        MERGE `{full_table_id}` T
        USING `{temp_table}` S
        ON {on_clause}
        WHEN NOT MATCHED THEN
        INSERT ({col_list})
        VALUES ({val_list})
        """


# =============================================================================
# Endpoint Implementations
# =============================================================================


class BacklinksBacklinks(BaseEndpoint):
    """
    Endpoint: v3/backlinks/backlinks/live

    Fetches backlinks for a target URL.
    """

    LIVE_URL = "backlinks/backlinks/live"
    TABLE_NAME = "backlinks_backlinks_live"

    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        return [{
            "target": target,
            "limit": kwargs.get("limit", 100),
            "filters": kwargs.get("filters") or [["dofollow", "=", True]],
        }]

    def _parse_response(self, response: dict, target: str) -> pd.DataFrame:
        try:
            result = response["tasks"][0]["result"][0]
            items = result.get("items") or []
        except (KeyError, IndexError):
            return pd.DataFrame(columns=self._get_schema())

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

        # Stringify array/dict fields
        stringify_cols = ["attributes", "domain_from_platform_type", "indirect_link_path"]
        for col in stringify_cols:
            if col in df.columns:
                df[col] = df[col].apply(
                    lambda v: json.dumps(v) if isinstance(v, (list, dict)) else v
                )

        return df


class SerpGoogleOrganic(BaseEndpoint):
    """
    Endpoint: v3/serp/google/organic/live/advanced

    Fetches SERP results for a keyword. Supports both live and POST/GET workflows.
    """

    LIVE_URL = "serp/google/organic/live/advanced"
    POST_URL = "serp/google/organic/task_post"
    READY_URL = "serp/google/organic/tasks_ready"
    GET_URL = "serp/google/organic/task_get/advanced"
    FIXED_URL = "serp/google/organic/tasks_fixed"
    ENDPOINT_BASE = "serp/google/organic"  # For generic client methods
    TABLE_NAME = "serp_google_organic_live_advanced"

    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        payload = {
            "keyword": target,
            "location_code": kwargs.get("location_code", self.config.location_code),
            "language_code": kwargs.get("language_code", self.config.language_code),
        }
        if kwargs.get("tag"):
            payload["tag"] = kwargs["tag"]
        return [payload]

    def _parse_response(self, response: dict, target: str) -> pd.DataFrame:
        try:
            task = response["tasks"][0]
            result = task["result"][0]
            items = result["items"]
            if items is None:
                return pd.DataFrame(columns=self._get_schema())
        except (KeyError, IndexError, TypeError):
            return pd.DataFrame(columns=self._get_schema())

        task_id = task.get("id")
        serp_datetime = result.get("datetime")
        se_domain = result.get("se_domain")
        se_results_count = result.get("se_results_count")

        data_dict = task.get("data", {})
        location_code_val = data_dict.get("location_code")
        language_code_val = data_dict.get("language_code")
        device = data_dict.get("device")
        os_val = data_dict.get("os")

        rows = []
        for item in items:
            rows.append({
                "task_id": task_id,
                "keyword": target,
                "serp_datetime": serp_datetime,
                "se_domain": se_domain,
                "location_code": location_code_val,
                "language_code": language_code_val,
                "device": device,
                "os": os_val,
                "se_results_count": se_results_count,
                "item_type": item.get("type"),
                "rank_group": item.get("rank_group"),
                "rank_absolute": item.get("rank_absolute"),
                "page": item.get("page"),
                "position": item.get("position"),
                "data": {k: v for k, v in result.items() if k != "items"},
                "item": item,
            })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return [
            "task_id", "keyword", "serp_datetime", "se_domain",
            "location_code", "language_code", "device", "os",
            "se_results_count", "item_type", "rank_group", "rank_absolute",
            "page", "position", "data", "item",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["task_id", "rank_absolute"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = ["location_code", "se_results_count", "rank_group", "rank_absolute", "page"]
        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

        if "serp_datetime" in df.columns:
            df["serp_datetime"] = pd.to_datetime(df["serp_datetime"], utc=True)

        # Stringify dict fields
        for col in ["data", "item"]:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: json.dumps(x) if isinstance(x, (dict, list)) else x)

        return df

    # -------------------------------------------------------------------------
    # POST/GET workflow methods
    # -------------------------------------------------------------------------

    def _task_post(
        self,
        keywords: list[str],
        location_code: int | None = None,
        language_code: str | None = None,
        debug: bool | None = None,
    ) -> list[dict]:
        """
        POST up to 100 keywords to task_post endpoint.

        Returns:
            List of {"id": task_id, "keyword": keyword} for successfully created tasks
        """
        if len(keywords) > 100:
            raise ValueError("Maximum 100 keywords per request")

        location_code = location_code or self.config.location_code
        language_code = language_code or self.config.language_code
        debug = debug if debug is not None else self.config.debug

        url = f"{self._client.BASE_URL}/{self.POST_URL}"
        payload = [
            {"keyword": kw, "location_code": location_code, "language_code": language_code, "tag": kw}
            for kw in keywords
        ]

        resp = self._client._post(url, payload)
        if not resp:
            if debug:
                print("Failed to post tasks")
            return []

        tasks = []
        for task in resp.get("tasks", []):
            task_id = task.get("id")
            status = task.get("status_code")
            tag = task.get("data", {}).get("tag", "")
            if status == 20100 and task_id and tag:  # Task created successfully
                tasks.append({"id": task_id, "keyword": tag})
            elif debug:
                print(f"Task failed for '{tag}': {task.get('status_message')}")

        if debug:
            print(f"Posted {len(tasks)} tasks successfully")
        return tasks

    def _task_get(
        self,
        task_id: str,
        keyword: str,
        session: requests.Session | None = None,
    ) -> tuple[pd.DataFrame | None, str | None]:
        """
        GET results for a single task.

        Returns:
            (DataFrame, None) - success with data
            (None, None) - not ready yet (caller should retry)
            (None, "error message") - permanent failure
        """
        url = f"{self._client.BASE_URL}/{self.GET_URL}/{task_id}"

        # Client handles status codes, returns (task_data, error)
        task_data, error = self._client.get_task_result(url, session)

        if error:
            return (None, error)
        if task_data is None:
            return (None, None)  # Not ready

        # Parse using endpoint-specific logic
        response = {"tasks": [task_data]}
        df = self._parse_response(response, keyword)
        return (df, None)

    def post(
        self,
        target: str | list[str],
        *,
        location_code: int | None = None,
        language_code: str | None = None,
        max_wait: int = 300,
        debug: bool | None = None,
    ) -> pd.DataFrame:
        """
        POST/GET workflow for ≤100 keywords using direct approach.

        Args:
            target: Single keyword or list of keywords (max 100)
            max_wait: Maximum seconds to wait for all results (default 300)

        Returns:
            DataFrame with same schema as live()
        """
        keywords = [target] if isinstance(target, str) else list(target)
        if len(keywords) > 100:
            raise ValueError("Maximum 100 keywords. Use post_all() for larger batches.")

        debug = debug if debug is not None else self.config.debug

        # POST tasks
        tasks = self._task_post(keywords, location_code, language_code, debug)
        if not tasks:
            return pd.DataFrame(columns=self._get_schema())

        task_map = {t["id"]: t["keyword"] for t in tasks}
        pending = set(task_map.keys())
        results = []
        start_time = time.time()

        # Poll until all done or timeout (uses default session)
        while pending and (time.time() - start_time) < max_wait:
            for task_id in list(pending):
                keyword = task_map[task_id]
                df, error = self._task_get(task_id, keyword)

                if df is not None:
                    results.append(df)
                    pending.remove(task_id)
                elif error:
                    if debug:
                        print(f"[{keyword}] Failed: {error}")
                    pending.remove(task_id)
                # else: not ready, keep in pending

            if pending:
                time.sleep(1)  # Brief pause between cycles

        if pending and debug:
            print(f"Warning: {len(pending)} tasks did not complete within {max_wait}s")

        if results:
            return pd.concat(results, ignore_index=True)
        return pd.DataFrame(columns=self._get_schema())

    def post_all(
        self,
        targets: list[str],
        *,
        batch_size: int = 100,
        num_workers: int = 10,
        max_wait: int = 18000,
        max_error_retries: int = 3,
        location_code: int | None = None,
        language_code: str | None = None,
        debug: bool | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        High-volume POST/GET using direct approach (no /tasks_ready polling).

        Architecture:
        - POST all keywords, collect task_ids into a queue
        - Worker threads pop from queue, directly GET each task
        - Success (has data) → add to results
        - Not ready (still processing) → back to queue (no penalty)
        - Transient error (network, 429) → retry counter, fail after max
        - Permanent error (not found) → add to failed

        Args:
            targets: List of keywords (no limit)
            batch_size: Keywords per POST request (max 100)
            num_workers: Concurrent GET workers (default 10)
            max_wait: Maximum seconds (default 18000 = 5 hours)
            max_error_retries: Retries for transient errors (default 3)
            location_code: DataForSEO location code (default from config)
            language_code: DataForSEO language code (default from config)
            debug: Print progress messages

        Returns:
            (results_df, failed_df) where failed_df has columns [keyword, task_id, reason]
        """
        if batch_size > 100:
            batch_size = 100

        debug = debug if debug is not None else self.config.debug

        # Shared state (thread-safe)
        task_queue: Queue = Queue()
        results: list[pd.DataFrame] = []
        failed_rows: list[dict] = []
        results_lock = Lock()
        failed_lock = Lock()

        # Stats for monitoring
        stats = {
            "collected": 0,
            "get_requests": 0,
            "not_ready_cycles": 0,
            "start_time": time.time(),
            "stop": False,
        }
        stats_lock = Lock()

        # Create sessions for each worker
        sessions = [self._client._create_session() for _ in range(num_workers)]

        # =====================================================================
        # STEP 1: Submit all batches via POST
        # =====================================================================

        task_id_to_keyword: dict[str, str] = {}
        batches = list(self._client._chunked(targets, batch_size))

        if debug:
            print(f"Submitting {len(targets):,} keywords in {len(batches)} batches...")

        for i, batch in enumerate(batches):
            tasks = self._task_post(batch, location_code, language_code, debug=False)
            for t in tasks:
                task_id_to_keyword[t["id"]] = t["keyword"]

            if debug and (i + 1) % 100 == 0:
                print(f"  Submitted {i+1}/{len(batches)} batches...")

        if not task_id_to_keyword:
            print("ERROR: No tasks submitted")
            return pd.DataFrame(columns=self._get_schema()), pd.DataFrame(columns=["keyword", "task_id", "reason"])

        total_tasks = len(task_id_to_keyword)

        # Add all tasks to queue
        for task_id, keyword in task_id_to_keyword.items():
            task_queue.put({"task_id": task_id, "keyword": keyword, "error_retries": 0})

        if debug:
            print(f"Submitted {total_tasks:,} tasks. Starting {num_workers} workers...")

        # =====================================================================
        # STEP 2: Worker function - GET tasks directly
        # =====================================================================

        def worker(worker_id: int):
            session = sessions[worker_id]

            while not stats["stop"]:
                try:
                    task = task_queue.get(timeout=2)
                except Empty:
                    if stats["stop"]:
                        break
                    with stats_lock:
                        if stats["collected"] + len(failed_rows) >= total_tasks:
                            break
                    continue

                task_id = task["task_id"]
                keyword = task["keyword"]
                error_retries = task["error_retries"]

                with stats_lock:
                    stats["get_requests"] += 1

                try:
                    url = f"{self._client.BASE_URL}/{self.GET_URL}/{task_id}"
                    resp = session.get(url, timeout=30)
                    data = resp.json()

                    tasks_list = data.get("tasks", [])
                    if not tasks_list:
                        with stats_lock:
                            stats["not_ready_cycles"] += 1
                        task_queue.put(task)
                        task_queue.task_done()
                        continue

                    task_data = tasks_list[0]
                    task_status = task_data.get("status_code", 0)

                    # Not ready - cycle back to queue
                    if task_status in (40102, 40601, 40602, 40202):
                        with stats_lock:
                            stats["not_ready_cycles"] += 1
                        task_queue.put(task)
                        task_queue.task_done()
                        continue

                    # Permanent error - task not found
                    if task_status == 40402:
                        with failed_lock:
                            failed_rows.append({
                                "keyword": keyword,
                                "task_id": task_id,
                                "reason": "task_not_found (40402)",
                            })
                        task_queue.task_done()
                        continue

                    # Other error status
                    if task_status != 20000:
                        if error_retries < max_error_retries:
                            task["error_retries"] += 1
                            task_queue.put(task)
                        else:
                            with failed_lock:
                                failed_rows.append({
                                    "keyword": keyword,
                                    "task_id": task_id,
                                    "reason": f"status_{task_status}_after_{max_error_retries}_retries",
                                })
                        task_queue.task_done()
                        continue

                    # Success - extract results
                    result = task_data.get("result", [])
                    if not result or result[0] is None:
                        with stats_lock:
                            stats["not_ready_cycles"] += 1
                        task_queue.put(task)
                        task_queue.task_done()
                        continue

                    items = result[0].get("items")
                    if items is None:
                        with stats_lock:
                            stats["not_ready_cycles"] += 1
                        task_queue.put(task)
                        task_queue.task_done()
                        continue

                    # Parse response using endpoint logic
                    response = {"tasks": [task_data]}
                    df = self._parse_response(response, keyword)

                    with results_lock:
                        results.append(df)

                    with stats_lock:
                        stats["collected"] += 1

                    task_queue.task_done()

                except Exception as e:
                    if error_retries < max_error_retries:
                        task["error_retries"] += 1
                        task_queue.put(task)
                    else:
                        with failed_lock:
                            failed_rows.append({
                                "keyword": keyword,
                                "task_id": task_id,
                                "reason": f"exception_after_{max_error_retries}_retries: {str(e)[:100]}",
                            })
                    task_queue.task_done()

        # =====================================================================
        # STEP 3: Progress monitor
        # =====================================================================

        def monitor():
            while not stats["stop"]:
                time.sleep(10)

                with stats_lock:
                    collected = stats["collected"]
                    get_requests = stats["get_requests"]
                    not_ready = stats["not_ready_cycles"]
                    elapsed = time.time() - stats["start_time"]

                avg_rate = collected / elapsed * 60 if elapsed > 0 else 0
                remaining = total_tasks - collected - len(failed_rows)
                eta_min = remaining / avg_rate if avg_rate > 0 else 0

                if debug:
                    print(
                        f"  [{int(elapsed)}s] Collected {collected:,}/{total_tasks:,}, "
                        f"queue: {task_queue.qsize():,}, "
                        f"avg: {avg_rate:.0f}/min, ETA: {eta_min:.1f}min, "
                        f"GETs: {get_requests:,}, not_ready: {not_ready:,}, "
                        f"failed: {len(failed_rows)}"
                    )

                with stats_lock:
                    if stats["collected"] + len(failed_rows) >= total_tasks:
                        break

                if time.time() - stats["start_time"] >= max_wait:
                    break

        # =====================================================================
        # STEP 4: Start all threads
        # =====================================================================

        worker_threads = [Thread(target=worker, args=(i,), daemon=True) for i in range(num_workers)]
        monitor_thread = Thread(target=monitor, daemon=True)

        for t in worker_threads:
            t.start()
        monitor_thread.start()

        # Wait for completion or timeout
        start = time.time()
        while time.time() - start < max_wait:
            with stats_lock:
                if stats["collected"] + len(failed_rows) >= total_tasks:
                    break
            time.sleep(1)

        # Signal stop
        stats["stop"] = True

        # Wait for threads to finish
        for t in worker_threads:
            t.join(timeout=5)

        # =====================================================================
        # STEP 5: Finalize
        # =====================================================================

        # Add any remaining in queue as timeouts
        remaining_in_queue = 0
        while not task_queue.empty():
            try:
                task = task_queue.get_nowait()
                failed_rows.append({
                    "keyword": task["keyword"],
                    "task_id": task["task_id"],
                    "reason": "timeout",
                })
                remaining_in_queue += 1
            except Empty:
                break

        if remaining_in_queue > 0:
            print(f"WARNING: {remaining_in_queue:,} tasks timed out")

        failed_df = pd.DataFrame(failed_rows, columns=["keyword", "task_id", "reason"])

        if not failed_df.empty:
            print(f"WARNING: {len(failed_df):,} keywords failed")

        if results:
            results_df = pd.concat(results, ignore_index=True)
            elapsed = (time.time() - stats["start_time"]) / 60
            if debug:
                print(f"Done. {len(results_df):,} rows for {stats['collected']:,} keywords in {elapsed:.1f}min")
                print(f"Total GET requests: {stats['get_requests']:,}")
                print(f"Not-ready cycles: {stats['not_ready_cycles']:,}")
                if elapsed > 0:
                    print(f"Effective rate: {stats['collected'] / elapsed:.0f}/min")
            return results_df, failed_df

        return pd.DataFrame(columns=self._get_schema()), failed_df

    def extract_paa(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Extract People Also Ask questions from SERP results.

        Args:
            df: DataFrame from live() or post_all() with 'item_type' and 'item' columns

        Returns:
            DataFrame with columns: keyword, question, answer, url, title
        """
        if df.empty or "item_type" not in df.columns:
            return pd.DataFrame(columns=["keyword", "question", "answer", "url", "title"])

        paa_rows = df[df["item_type"] == "people_also_ask"]
        if paa_rows.empty:
            return pd.DataFrame(columns=["keyword", "question", "answer", "url", "title"])

        results = []
        for _, row in paa_rows.iterrows():
            keyword = row.get("keyword", "")
            item = row.get("item", {})
            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except json.JSONDecodeError:
                    continue

            items = item.get("items", []) or []
            for paa_item in items:
                results.append({
                    "keyword": keyword,
                    "question": paa_item.get("title", ""),
                    "answer": paa_item.get("expanded_element", [{}])[0].get("description", "") if paa_item.get("expanded_element") else "",
                    "url": paa_item.get("url", ""),
                    "title": paa_item.get("title", ""),
                })

        return pd.DataFrame(results)


class DataforseoLabsGoogleKeywordSuggestions(BaseEndpoint):
    """
    Endpoint: v3/dataforseo_labs/google/keyword_suggestions/live

    Fetches keyword suggestions for a seed keyword.
    """

    LIVE_URL = "dataforseo_labs/google/keyword_suggestions/live"
    TABLE_NAME = "google_keyword-suggestions_live"

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
                return pd.DataFrame(columns=self._get_schema())

            task = tasks[0]
            results = task.get("result", []) or []
            if not results:
                return pd.DataFrame(columns=self._get_schema())

            suggestions_block = next((r for r in results if "items" in r), None)
            if not suggestions_block:
                return pd.DataFrame(columns=self._get_schema())

            items = suggestions_block.get("items", []) or []
            if not items:
                return pd.DataFrame(columns=self._get_schema())

        except Exception:
            return pd.DataFrame(columns=self._get_schema())

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
                "categories": keyword_info.get("categories") or [],
                "keyword_difficulty": keyword_properties.get("keyword_difficulty"),
                "detected_language": keyword_properties.get("detected_language"),
                "is_another_language": keyword_properties.get("is_another_language"),
                "words_count": keyword_properties.get("words_count"),
                "main_intent": search_intent_info.get("main_intent"),
                "foreign_intent": search_intent_info.get("foreign_intent"),
                "avg_backlinks": avg_backlinks_info.get("backlinks"),
                "avg_dofollow": avg_backlinks_info.get("dofollow"),
                "avg_referring_pages": avg_backlinks_info.get("referring_pages"),
                "avg_referring_domains": avg_backlinks_info.get("referring_domains"),
                "avg_referring_main_domains": avg_backlinks_info.get("referring_main_domains"),
                "avg_rank": avg_backlinks_info.get("rank"),
                "avg_main_domain_rank": avg_backlinks_info.get("main_domain_rank"),
            })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return [
            "se_type", "seed_keyword", "keyword", "location_code", "language_code",
            "search_volume", "competition", "competition_level", "cpc",
            "low_top_of_page_bid", "high_top_of_page_bid", "categories",
            "keyword_difficulty", "detected_language", "is_another_language", "words_count",
            "main_intent", "foreign_intent",
            "avg_backlinks", "avg_dofollow", "avg_referring_pages",
            "avg_referring_domains", "avg_referring_main_domains",
            "avg_rank", "avg_main_domain_rank",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["seed_keyword", "keyword", "location_code", "language_code"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = ["location_code", "search_volume", "keyword_difficulty", "words_count"]
        float_cols = [
            "competition", "cpc", "low_top_of_page_bid", "high_top_of_page_bid",
            "avg_backlinks", "avg_dofollow", "avg_referring_pages",
            "avg_referring_domains", "avg_referring_main_domains",
            "avg_rank", "avg_main_domain_rank",
        ]

        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

        for col in float_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Normalize foreign_intent to STRING
        if "foreign_intent" in df.columns:
            def _normalize(v):
                if isinstance(v, list):
                    return ",".join(str(x) for x in v if x is not None)
                return v
            df["foreign_intent"] = df["foreign_intent"].apply(_normalize).astype("string")

        return df


class DataforseoLabsGoogleRelatedKeywords(BaseEndpoint):
    """
    Endpoint: v3/dataforseo_labs/google/related_keywords/live

    Fetches related keywords for a seed keyword.
    """

    LIVE_URL = "dataforseo_labs/google/related_keywords/live"
    TABLE_NAME = "google_related-keywords_live"

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

            related_keyword = kd.get("keyword") or item.get("keyword")
            depth = item.get("depth")

            serp_item_types_list = serp_info.get("serp_item_types") or []
            serp_item_types_str = ",".join(serp_item_types_list) if isinstance(serp_item_types_list, list) else None

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
                "keyword_difficulty": kw_props.get("keyword_difficulty"),
                "detected_language": kw_props.get("detected_language"),
                "is_other_language": kw_props.get("is_another_language"),
                "serp_item_types": serp_item_types_str,
                "se_results_count": serp_info.get("se_results_count"),
                "serp_last_updated_time": serp_info.get("last_updated_time"),
                "backlinks": backlinks_info.get("backlinks"),
                "dofollow": backlinks_info.get("dofollow"),
                "referring_pages": backlinks_info.get("referring_pages"),
                "referring_domains": backlinks_info.get("referring_domains"),
                "referring_main_domains": backlinks_info.get("referring_main_domains"),
                "main_domain_rank": backlinks_info.get("main_domain_rank"),
                "search_intent_main": intent_info.get("main_intent"),
            })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return [
            "task_id", "seed_keyword", "related_keyword", "depth",
            "location_code", "language_code",
            "search_volume", "cpc", "competition", "competition_level",
            "low_top_of_page_bid", "high_top_of_page_bid", "keyword_difficulty",
            "detected_language", "is_other_language",
            "serp_item_types", "se_results_count", "serp_last_updated_time",
            "backlinks", "dofollow", "referring_pages",
            "referring_domains", "referring_main_domains", "main_domain_rank",
            "search_intent_main",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["task_id", "related_keyword", "depth"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = ["depth", "location_code", "search_volume", "se_results_count", "keyword_difficulty"]
        float_cols = [
            "cpc", "competition", "low_top_of_page_bid", "high_top_of_page_bid",
            "backlinks", "dofollow", "referring_pages",
            "referring_domains", "referring_main_domains", "main_domain_rank",
        ]

        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

        for col in float_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "serp_last_updated_time" in df.columns:
            df["serp_last_updated_time"] = pd.to_datetime(
                df["serp_last_updated_time"], errors="coerce", utc=True
            )

        return df


class DataforseoLabsGoogleRankedKeywords(BaseEndpoint):
    """
    Endpoint: v3/dataforseo_labs/google/ranked_keywords/live

    Fetches ranked keywords for a domain.

    TODO: The original client had a `get_dataforseo_labs_google_ranked_keywords_all`
    method that took (company, domain, num_kw_seo) params and added company/domain/filters
    columns to the output. This was used by an older pipeline. The new `live_all` method
    processes multiple domains but uses a different pattern. Need to review if the
    company/domain metadata columns are still required by downstream consumers.
    """

    LIVE_URL = "dataforseo_labs/google/ranked_keywords/live"
    TABLE_NAME = "dataforseo_labs-google-ranked_keywords"

    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        return [{
            "target": target,
            "language_name": "English",
            "location_code": kwargs.get("location_code", self.config.location_code),
            "limit": kwargs.get("limit", 1000),
            "offset": kwargs.get("offset", 0),
            "load_rank_absolute": True,
            "order_by": ["ranked_serp_element.serp_item.etv,desc"],
            "filters": kwargs.get("filters"),
        }]

    def _parse_response(self, response: dict, target: str) -> pd.DataFrame:
        try:
            items = response["tasks"][0]["result"][0]["items"]
            if not items:
                return pd.DataFrame(columns=self._get_schema())
        except (KeyError, IndexError, TypeError):
            return pd.DataFrame(columns=self._get_schema())

        rows = []
        for item in items:
            keyword_data = item.get("keyword_data", {}) or {}
            keyword_info = keyword_data.get("keyword_info", {}) or {}
            keyword_props = keyword_data.get("keyword_properties", {}) or {}
            serp_info = keyword_data.get("serp_info", {}) or {}
            avg_backlinks_info = keyword_data.get("avg_backlinks_info", {}) or {}
            intent_info = keyword_data.get("search_intent_info", {}) or {}

            ranked_serp_element = item.get("ranked_serp_element", {}) or {}
            serp_item = ranked_serp_element.get("serp_item", {}) or {}

            keyword = keyword_data.get("keyword")
            ranked_url = serp_item.get("url")
            rank = serp_item.get("rank_group")
            keyword_difficulty = keyword_props.get("keyword_difficulty")
            search_volume = keyword_info.get("search_volume")

            if not all([keyword, rank is not None, ranked_url, search_volume is not None, keyword_difficulty is not None]):
                continue

            search_volume_trend = keyword_info.get("search_volume_trend") or {}
            monthly_searches = keyword_info.get("monthly_searches")

            rows.append({
                "keyword": keyword,
                "rank": rank,
                "url": ranked_url,
                "search_volume": search_volume,
                "keyword_difficulty": keyword_difficulty,
                "national_location_code": self.config.location_code,
                "traffic_volume": serp_item.get("etv"),
                "cost_per_click": keyword_info.get("low_top_of_page_bid") or keyword_info.get("cpc"),
                "keyword_location_code": keyword_data.get("location_code"),
                "language_code": keyword_data.get("language_code"),
                "main_domain": serp_item.get("main_domain"),
                "cpc_raw": keyword_info.get("cpc"),
                "low_top_of_page_bid": keyword_info.get("low_top_of_page_bid"),
                "high_top_of_page_bid": keyword_info.get("high_top_of_page_bid"),
                "competition": keyword_info.get("competition"),
                "competition_level": keyword_info.get("competition_level"),
                "categories": keyword_info.get("categories"),
                "monthly_searches": json.dumps(monthly_searches) if monthly_searches else None,
                "search_volume_trend_monthly": search_volume_trend.get("monthly"),
                "search_volume_trend_quarterly": search_volume_trend.get("quarterly"),
                "search_volume_trend_yearly": search_volume_trend.get("yearly"),
                "rank_absolute": serp_item.get("rank_absolute"),
                "position": serp_item.get("position"),
                "serp_keyword_difficulty": ranked_serp_element.get("keyword_difficulty"),
                "serp_item_types": serp_info.get("serp_item_types"),
                "se_results_count": serp_info.get("se_results_count"),
                "main_intent": intent_info.get("main_intent"),
                "foreign_intent": intent_info.get("foreign_intent"),
                "avg_backlinks": avg_backlinks_info.get("backlinks"),
                "avg_referring_domains": avg_backlinks_info.get("referring_domains"),
                "avg_referring_main_domains": avg_backlinks_info.get("referring_main_domains"),
                "avg_rank": avg_backlinks_info.get("rank"),
                "avg_main_domain_rank": avg_backlinks_info.get("main_domain_rank"),
            })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return [
            "keyword", "rank", "url", "search_volume", "keyword_difficulty",
            "national_location_code", "traffic_volume", "cost_per_click",
            "keyword_location_code", "language_code", "main_domain",
            "cpc_raw", "low_top_of_page_bid", "high_top_of_page_bid",
            "competition", "competition_level", "categories", "monthly_searches",
            "search_volume_trend_monthly", "search_volume_trend_quarterly", "search_volume_trend_yearly",
            "rank_absolute", "position", "serp_keyword_difficulty",
            "serp_item_types", "se_results_count", "main_intent", "foreign_intent",
            "avg_backlinks", "avg_referring_domains", "avg_referring_main_domains",
            "avg_rank", "avg_main_domain_rank",
            # Metadata columns added by live_all()
            "company_id", "domain", "filters",
        ]

    def _get_dedupe_keys(self) -> list[str]:
        return ["keyword", "job_id", "upload_id", "domain", "national_location_code"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = [
            "rank", "search_volume", "keyword_difficulty", "national_location_code",
            "keyword_location_code", "search_volume_trend_monthly",
            "search_volume_trend_quarterly", "search_volume_trend_yearly",
            "rank_absolute", "se_results_count",
        ]
        float_cols = [
            "traffic_volume", "cost_per_click", "cpc_raw",
            "low_top_of_page_bid", "high_top_of_page_bid", "competition",
            "avg_backlinks", "avg_referring_domains", "avg_referring_main_domains",
            "avg_rank", "avg_main_domain_rank",
        ]

        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

        for col in float_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    async def live_all(
        self,
        targets: list[str],
        *,
        company_id: str | None = None,
        limit_per_domain: int = 10000,
        filters: list | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Fetch ranked keywords for multiple domains.

        This endpoint handles pagination internally since each domain can have
        many keywords.

        Args:
            targets: List of domains to query
            company_id: Optional company identifier to add to all rows (links to Meta.companies)
            limit_per_domain: Max keywords per domain
            filters: Optional API filters (also stored in output for tracking)
            **kwargs: Additional parameters

        Note:
            The original `get_dataforseo_labs_google_ranked_keywords_all` took a single
            (company, domain) pair. This method processes multiple domains. If you need
            the single-domain pattern with company metadata, pass a single-item list
            with the company_id parameter.
        """
        all_dfs = []

        for domain in targets:
            if self.config.debug:
                print(f"Fetching ranked keywords for {domain}...")

            df = await self._fetch_domain_keywords(domain, limit_per_domain, filters=filters, **kwargs)
            if not df.empty:
                df["domain"] = domain
                df["company_id"] = company_id
                df["filters"] = str(filters) if filters else None
                all_dfs.append(df)

        if all_dfs:
            return pd.concat(all_dfs, ignore_index=True)
        return pd.DataFrame(columns=self._get_schema())

    async def _fetch_domain_keywords(
        self,
        domain: str,
        limit: int,
        **kwargs,
    ) -> pd.DataFrame:
        """Fetch all ranked keywords for a single domain with pagination."""
        page_size = 1000
        max_concurrent = kwargs.get("max_concurrent", 30)
        semaphore = asyncio.Semaphore(max_concurrent)

        results: list[pd.DataFrame] = []
        offset = 0
        consecutive_empty = 0

        while offset < limit and consecutive_empty < 2:
            remaining = min(page_size, limit - offset)

            async with semaphore:
                df = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda o=offset, r=remaining: self.live(
                        domain,
                        limit=r,
                        offset=o,
                        **kwargs,
                    )
                )

            if df.empty:
                consecutive_empty += 1
            else:
                consecutive_empty = 0
                results.append(df)

            offset += page_size

        if results:
            return pd.concat(results, ignore_index=True)
        return pd.DataFrame(columns=self._get_schema())


class KeywordsDataGoogleAdsSearchVolume(BaseEndpoint):
    """
    Endpoint: v3/keywords_data/google_ads/search_volume/live

    Fetches search volume data for keywords. Supports both Live and POST/GET workflows.

    Note: Live endpoints are limited to 12 requests/minute. For high-volume use,
    prefer the POST/GET workflow via post_all().
    """

    LIVE_URL = "keywords_data/google_ads/search_volume/live"
    POST_URL = "keywords_data/google_ads/search_volume/task_post"
    READY_URL = "keywords_data/google_ads/search_volume/tasks_ready"
    GET_URL = "keywords_data/google_ads/search_volume/task_get"
    TABLE_NAME = "keywords_data_google_ads_search_volume"

    def _build_payload(self, target: str | list[str], **kwargs) -> list[dict]:
        keywords = [target] if isinstance(target, str) else target
        return [{
            "language_name": "English",
            "location_code": kwargs.get("location_code", self.config.location_code),
            "keywords": keywords,
        }]

    def _parse_response(self, response: dict, target: str | list[str]) -> pd.DataFrame:
        try:
            items = response["tasks"][0]["result"]
            if not items:
                return pd.DataFrame(columns=self._get_schema())
        except (KeyError, IndexError, TypeError):
            return pd.DataFrame(columns=self._get_schema())

        rows = []
        for item in items:
            keyword = item.get("keyword")
            search_volume = item.get("search_volume")
            if keyword and search_volume is not None:
                rows.append({
                    "keyword": keyword,
                    "local_search_volume": search_volume,
                    "local_location_code": self.config.location_code,
                })

        return pd.DataFrame(rows)

    def _get_schema(self) -> list[str]:
        return ["keyword", "local_search_volume", "local_location_code"]

    def _get_dedupe_keys(self) -> list[str]:
        return ["keyword", "local_location_code"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        if "local_search_volume" in df.columns:
            df["local_search_volume"] = pd.to_numeric(df["local_search_volume"], errors="coerce").astype("Int64")
        if "local_location_code" in df.columns:
            df["local_location_code"] = pd.to_numeric(df["local_location_code"], errors="coerce").astype("Int64")
        return df

    def live(
        self,
        target: str | list[str],
        **kwargs,
    ) -> pd.DataFrame:
        """
        Fetch search volume for keywords.

        Args:
            target: Single keyword or list of keywords (max 1000)
            **kwargs: Additional parameters

        Returns:
            DataFrame with keyword search volumes
        """
        keywords = [target] if isinstance(target, str) else target

        if len(keywords) > 1000:
            raise ValueError("Maximum 1000 keywords per request")

        url = f"{self._client.BASE_URL}/{self.LIVE_URL}"
        payload = self._build_payload(keywords, **kwargs)

        max_retries = kwargs.get("max_retries") or self.config.max_retries
        retry_delays = [3, 5, 15, 30]

        for attempt in range(max_retries):
            delay = retry_delays[attempt] if attempt < len(retry_delays) else retry_delays[-1]
            if attempt > 0:
                time.sleep(delay)

            resp = self._client._post(url, payload, max_retries=1, retry_delay=0)
            if not resp:
                continue

            try:
                status_code = resp["tasks"][0].get("status_code")
                if status_code == 20000:
                    return self._parse_response(resp, keywords)
                if self.config.debug:
                    print(f"Attempt {attempt + 1}: status_code {status_code}")
            except (KeyError, IndexError):
                continue

        return pd.DataFrame(columns=self._get_schema())

    async def live_all(
        self,
        targets: list[str],
        *,
        batch_size: int = 1000,
        batch_delay: float = 2.0,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Fetch search volume for many keywords in batches.

        Args:
            targets: List of keywords
            batch_size: Keywords per request (max 1000)
            batch_delay: Delay between batches to respect rate limits
            **kwargs: Additional parameters

        Returns:
            Combined DataFrame with all search volumes
        """
        batch_size = min(batch_size, 1000)  # API max
        batches = list(self._client._chunked(targets, batch_size))
        total_batches = len(batches)

        if self.config.debug:
            print(f"Fetching search volume for {len(targets)} keywords in {total_batches} batches...")

        results: list[pd.DataFrame] = []
        start_time = time.monotonic()

        for idx, batch in enumerate(batches, 1):
            df = self.live(batch, **kwargs)
            if not df.empty:
                results.append(df)

            if self.config.debug:
                elapsed = time.monotonic() - start_time
                print(f"Progress: {idx}/{total_batches} batches completed. Time: {elapsed:.1f}s")

            if idx < total_batches:
                await asyncio.sleep(batch_delay)

        if results:
            return pd.concat(results, ignore_index=True)
        return pd.DataFrame(columns=self._get_schema())

    # -------------------------------------------------------------------------
    # POST/GET workflow methods (to be implemented)
    # -------------------------------------------------------------------------

    def _task_post(self, keywords: list[str], location_code: int, debug: bool = False) -> list[dict]:
        """POST keywords to task_post endpoint. TODO: Implement."""
        raise NotImplementedError("POST/GET workflow not yet implemented")

    def _tasks_ready(self, debug: bool = False) -> list[str]:
        """Check which tasks are ready. TODO: Implement."""
        raise NotImplementedError("POST/GET workflow not yet implemented")

    def _task_get(self, task_id: str, debug: bool = False) -> pd.DataFrame:
        """GET results for a completed task. TODO: Implement."""
        raise NotImplementedError("POST/GET workflow not yet implemented")

    def post(self, target: str | list[str], **kwargs) -> pd.DataFrame:
        """Submit keywords using POST/GET workflow. TODO: Implement."""
        raise NotImplementedError("POST/GET workflow not yet implemented")

    async def post_all(self, targets: list[str], **kwargs) -> pd.DataFrame:
        """Submit keywords using POST/GET workflow. TODO: Implement."""
        raise NotImplementedError("POST/GET workflow not yet implemented")


class DataforseoLabsGoogleKeywordOverview(BaseEndpoint):
    """
    Endpoint: v3/dataforseo_labs/google/keyword_overview/live

    Fetches keyword overview data for up to 700 keywords per request.
    Includes search volume, CPC, competition, keyword difficulty,
    search intent, and impressions data.
    """

    LIVE_URL = "dataforseo_labs/google/keyword_overview/live"
    POST_URL = None
    TABLE_NAME = "dataforseo_labs_google_keyword_overview"
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
                return pd.DataFrame(columns=self._get_schema())

            task = tasks[0]
            results = task.get("result", []) or []
            if not results:
                return pd.DataFrame(columns=self._get_schema())

            items = results[0].get("items", []) or [] if results[0] else []
            if not items:
                return pd.DataFrame(columns=self._get_schema())

        except Exception:
            return pd.DataFrame(columns=self._get_schema())

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

    def live(
        self,
        keywords: str | list[str],
        *,
        location_code: int | None = None,
        language_code: str | None = None,
        max_retries: int | None = None,
        retry_delay: int | None = None,
        debug: bool | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Fetch keyword overview data for up to 700 keywords in a single request.

        Args:
            keywords: A keyword string or list of keywords (max 700)
            location_code: DataForSEO location code (default from config)
            language_code: DataForSEO language code (default from config)
            max_retries: Override default retry count
            retry_delay: Override default retry delay
            debug: Override default debug setting

        Returns:
            DataFrame with keyword overview data
        """
        if isinstance(keywords, str):
            keywords = [keywords]

        location_code = location_code or self.config.location_code
        language_code = language_code or self.config.language_code
        max_retries = max_retries or self.config.max_retries
        retry_delay = retry_delay or self.config.retry_delay
        debug = debug if debug is not None else self.config.debug

        url = f"{self._client.BASE_URL}/{self.LIVE_URL}"
        payload = self._build_payload(
            keywords,
            location_code=location_code,
            language_code=language_code,
        )

        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                time.sleep(retry_delay)

            resp = self._client._post(url, payload, max_retries=1, retry_delay=0)

            if not resp:
                if debug:
                    print(f"[keyword_overview] Invalid response. Attempt {attempt}/{max_retries}")
                continue

            try:
                df = self._parse_response(resp, keywords)
                if not df.empty:
                    return df
                if debug:
                    print(f"[keyword_overview] Empty result. Attempt {attempt}/{max_retries}")
            except Exception as e:
                if debug:
                    print(f"[keyword_overview] Parse error: {e}. Attempt {attempt}/{max_retries}")
                continue

        return pd.DataFrame(columns=self._get_schema())

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

        Chunks the keyword list into batches of up to 700 and calls live()
        for each batch sequentially.

        Args:
            keywords: Full list of keywords
            batch_size: Keywords per API call (max 700)
            batch_delay: Delay in seconds between batches
            **kwargs: Passed to live()

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

            df = self.live(chunk, **kwargs)
            if not df.empty:
                df_list.append(df)

            if idx < total_batches:
                time.sleep(batch_delay)

        if df_list:
            return pd.concat(df_list, ignore_index=True)
        return pd.DataFrame(columns=self._get_schema())


class DataforseoLabsGoogleSearchIntent(BaseEndpoint):
    """
    Endpoint: v3/dataforseo_labs/google/search_intent/live

    Fetches search intent classification for up to 1000 keywords per request.
    Returns intent label, probability, and secondary intents.
    """

    LIVE_URL = "dataforseo_labs/google/search_intent/live"
    POST_URL = None
    TABLE_NAME = "dataforseo_labs_google_search_intent"
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
                return pd.DataFrame(columns=self._get_schema())

            task = tasks[0]
            results = task.get("result", []) or []
            if not results:
                return pd.DataFrame(columns=self._get_schema())

            items = results[0].get("items", []) or [] if results[0] else []
            if not items:
                return pd.DataFrame(columns=self._get_schema())

        except Exception:
            return pd.DataFrame(columns=self._get_schema())

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

    def live(
        self,
        keywords: str | list[str],
        *,
        language_code: str | None = None,
        max_retries: int | None = None,
        retry_delay: int | None = None,
        debug: bool | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Fetch search intent data for up to 1000 keywords in a single request.

        Args:
            keywords: A keyword string or list of keywords (max 1000)
            language_code: DataForSEO language code (default from config)
            max_retries: Override default retry count
            retry_delay: Override default retry delay
            debug: Override default debug setting

        Returns:
            DataFrame with search intent data
        """
        if isinstance(keywords, str):
            keywords = [keywords]

        language_code = language_code or self.config.language_code
        max_retries = max_retries or self.config.max_retries
        retry_delay = retry_delay or self.config.retry_delay
        debug = debug if debug is not None else self.config.debug

        url = f"{self._client.BASE_URL}/{self.LIVE_URL}"
        payload = self._build_payload(
            keywords,
            language_code=language_code,
        )

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

        return pd.DataFrame(columns=self._get_schema())

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

        Chunks the keyword list into batches of up to 1000 and calls live()
        for each batch sequentially.

        Args:
            keywords: Full list of keywords
            batch_size: Keywords per API call (max 1000)
            batch_delay: Delay in seconds between batches
            **kwargs: Passed to live()

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

            df = self.live(chunk, **kwargs)
            if not df.empty:
                df_list.append(df)

            if idx < total_batches:
                time.sleep(batch_delay)

        if df_list:
            return pd.concat(df_list, ignore_index=True)
        return pd.DataFrame(columns=self._get_schema())


class DataforseoLabsGoogleDomainRankOverview(BaseEndpoint):
    """
    Endpoint: v3/dataforseo_labs/google/domain_rank_overview/live

    Fetches domain ranking overview metrics including total keyword counts,
    estimated traffic, and ranking distribution.
    """

    LIVE_URL = "dataforseo_labs/google/domain_rank_overview/live"
    TABLE_NAME = "dataforseo_labs_google_domain_rank_overview"  # TODO: Create BQ table

    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        return [{
            "target": target,
            "location_code": kwargs.get("location_code", self.config.location_code),
            "language_code": kwargs.get("language_code", self.config.language_code),
        }]

    def _parse_response(self, response: dict, target: str) -> pd.DataFrame:
        try:
            task = response["tasks"][0]
            result = task["result"][0]
            items = result.get("items") or []
        except (KeyError, IndexError):
            return pd.DataFrame(columns=self._get_schema())

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

    def upload(
        self,
        bq_client: "BigQueryClient",
        df: pd.DataFrame,
        job_id: str,
    ) -> None:
        """Upload DataFrame to BigQuery. TODO: Create BQ table first."""
        raise NotImplementedError(
            "BigQuery table 'dataforseo_labs_google_domain_rank_overview' does not exist yet. "
            "Create the table before calling upload()."
        )

    def get_keyword_count(self, domain: str, location_code: int | None = None) -> int:
        """
        Get the total number of organic ranked keywords for a domain.

        This is a convenience method that extracts just the keyword count.

        Args:
            domain: The domain to analyze (e.g., "example.com")
            location_code: Location code for targeting (default from config)

        Returns:
            Total number of ranked keywords, or -1 on failure
        """
        df = self.live(domain, location_code=location_code)
        if df.empty:
            return -1
        try:
            return int(df["organic_count"].iloc[0])
        except (KeyError, IndexError, TypeError, ValueError):
            return -1


# =============================================================================
# Backlinks – Bulk Pages Summary
# =============================================================================


class BacklinksBulkPagesSummary(BaseEndpoint):
    """
    Endpoint: v3/backlinks/bulk_pages_summary/live

    Fetches backlink summary data (total backlinks, referring domains, spam score)
    for up to 1000 target pages per API call.

    Unlike other endpoints, this accepts multiple targets in a single request.
    The live() method handles a single target; live_all() batches targets into
    groups of up to 1000 per API call.
    """

    LIVE_URL = "backlinks/bulk_pages_summary/live"
    TABLE_NAME = "backlinks_bulk_pages_summary_live"

    def _build_payload(self, target: str, **kwargs) -> list[dict]:
        """Build payload for a single target."""
        return [{"targets": [target]}]

    def _build_bulk_payload(self, targets: list[str]) -> list[dict]:
        """Build payload for multiple targets (up to 1000)."""
        return [{"targets": targets}]

    def _parse_response(self, response: dict, target: str) -> pd.DataFrame:
        """Parse API response into a DataFrame. Works for single and bulk responses.

        Response structure: tasks[0].result[0].items[{url, backlinks, ...}]
        """
        try:
            result = response["tasks"][0]["result"][0]
            items = result.get("items") or []
            if not items:
                return pd.DataFrame(columns=self._get_schema())
        except (KeyError, IndexError, TypeError):
            return pd.DataFrame(columns=self._get_schema())

        rows = []
        for item in items:
            if not item:
                continue
            rows.append({
                "url": item.get("url"),
                "backlinks": item.get("backlinks"),
                "referring_domains": item.get("referring_domains"),
                "referring_main_domains": item.get("referring_main_domains"),
                "rank": item.get("rank"),
                "main_domain_rank": item.get("main_domain_rank"),
                "spam_score": item.get("backlinks_spam_score"),
                "referring_ips": item.get("referring_ips"),
                "referring_subnets": item.get("referring_subnets"),
                "referring_pages": item.get("referring_pages"),
                "dofollow": (item.get("backlinks") or 0) - (
                    (item.get("referring_links_attributes") or {}).get("nofollow", 0)
                ),
                "nofollow": (item.get("referring_links_attributes") or {}).get("nofollow", 0),
                "broken_backlinks": item.get("broken_backlinks"),
                "broken_pages": item.get("broken_pages"),
            })

        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=self._get_schema())

    def _get_schema(self) -> list[str]:
        return [
            "url", "backlinks", "referring_domains", "referring_main_domains",
            "rank", "main_domain_rank", "spam_score",
            "referring_ips", "referring_subnets", "referring_pages",
            "dofollow", "nofollow", "broken_backlinks", "broken_pages",
        ]

    async def _fetch_batch_with_fallback(
        self,
        batch: list[str],
        max_retries: int,
        retry_delay: int,
        debug: bool,
        _depth: int = 0,
    ) -> pd.DataFrame | None:
        """
        Fetch a batch with error handling:
        - 40501 (Invalid Field): remove bad URL and retry
        - Timeout/500: after 2 consecutive failures, split batch in half
        - Max 5 recursive splits to prevent infinite recursion
        """
        MAX_SPLIT_DEPTH = 5

        if _depth > MAX_SPLIT_DEPTH:
            if debug:
                print(f"  {'  ' * _depth}Max split depth reached. Skipping {len(batch)} URLs.")
            return pd.DataFrame()

        indent = "  " * (_depth + 1)
        url = f"{self._client.BASE_URL}/{self.LIVE_URL}"
        current_batch = list(batch)
        consecutive_failures = 0

        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                time.sleep(retry_delay)

            payload = self._build_bulk_payload(current_batch)
            resp = self._client._post(url, payload, max_retries=1, retry_delay=0)

            if not resp:
                consecutive_failures += 1
                if debug:
                    print(f"{indent}No response. Attempt {attempt}/{max_retries} (failures: {consecutive_failures})")

                if consecutive_failures >= 2 and len(current_batch) > 1:
                    if debug:
                        print(f"{indent}2 consecutive failures. Splitting batch of {len(current_batch)} in half (depth {_depth + 1})...")
                    mid = len(current_batch) // 2
                    left = await self._fetch_batch_with_fallback(
                        current_batch[:mid], max_retries, retry_delay, debug, _depth + 1
                    )
                    right = await self._fetch_batch_with_fallback(
                        current_batch[mid:], max_retries, retry_delay, debug, _depth + 1
                    )
                    parts = [df for df in [left, right] if df is not None and not df.empty]
                    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
                continue

            # Check for API-level errors
            try:
                task = resp.get("tasks", [{}])[0]
                task_status = task.get("status_code", 0)

                # 40501: Invalid target — remove and retry
                if task_status == 40501:
                    msg = task.get("status_message", "")
                    invalid_url = self._extract_invalid_target(msg)
                    if invalid_url and invalid_url in current_batch:
                        current_batch.remove(invalid_url)
                        if debug:
                            print(f"{indent}Removed invalid target: {invalid_url} ({len(current_batch)} remaining)")
                        if not current_batch:
                            return pd.DataFrame()
                        continue
                    if debug:
                        print(f"{indent}API error 40501: {msg}")
                    continue

                # 500xx: Server error — split immediately (deterministic, retrying won't help)
                if task_status >= 50000:
                    if len(current_batch) > 1:
                        if debug:
                            print(f"{indent}Server error {task_status}. Splitting batch of {len(current_batch)} in half (depth {_depth + 1})...")
                        mid = len(current_batch) // 2
                        left = await self._fetch_batch_with_fallback(
                            current_batch[:mid], max_retries, retry_delay, debug, _depth + 1
                        )
                        right = await self._fetch_batch_with_fallback(
                            current_batch[mid:], max_retries, retry_delay, debug, _depth + 1
                        )
                        parts = [df for df in [left, right] if df is not None and not df.empty]
                        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
                    if debug:
                        print(f"{indent}Server error {task_status} on single URL. Skipping: {current_batch[0][:80]}")
                    return pd.DataFrame()
            except (KeyError, IndexError):
                pass

            # Try to parse the response
            try:
                df = self._parse_response(resp, target="bulk")
                if not df.empty:
                    return df
                if debug:
                    print(f"{indent}Empty result. Attempt {attempt}/{max_retries}")
            except Exception as e:
                if debug:
                    print(f"{indent}Parse error: {e}. Attempt {attempt}/{max_retries}")
                continue

        return pd.DataFrame()

    @staticmethod
    def _extract_invalid_target(error_message: str) -> str | None:
        """Extract the invalid URL from a DataForSEO 40501 error message.

        Example message: "Invalid Field: 'target' (https://example.com(not set)."
        Returns: "https://example.com(not set)"
        """
        import re
        match = re.search(r"'target' \((.+)\)\.$", error_message)
        if match:
            return match.group(1)
        # Fallback: try without the trailing ).
        match = re.search(r"'target' \((.+)\)", error_message)
        if match:
            return match.group(1)
        return None

    def _get_dedupe_keys(self) -> list[str]:
        return ["url"]

    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame:
        int_cols = [
            "backlinks", "referring_domains", "referring_main_domains",
            "rank", "main_domain_rank", "referring_ips", "referring_subnets",
            "referring_pages", "dofollow", "nofollow",
            "broken_backlinks", "broken_pages",
        ]
        float_cols = ["spam_score"]
        bool_cols: list[str] = []

        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        for col in float_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        for col in bool_cols:
            if col in df.columns:
                df[col] = df[col].astype("boolean")

        return df

    async def live_all(
        self,
        targets: list[str],
        *,
        batch_size: int = 1000,
        batch_delay: float | None = None,
        domain: str | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Fetch bulk pages summary for multiple targets.

        Unlike the base live_all() which makes one API call per target,
        this batches up to 1000 targets per API call (the API limit).

        Args:
            targets: List of URLs to query
            batch_size: Targets per API call (max 1000, default 1000)
            batch_delay: Delay between batches in seconds
            domain: Optional domain name to tag results with
            **kwargs: Additional parameters

        Returns:
            Combined DataFrame of all results
        """
        batch_size = min(batch_size, 1000)  # API max is 1000
        batch_delay = batch_delay if batch_delay is not None else self.config.batch_delay
        max_retries = kwargs.pop("max_retries", None) or self.config.max_retries
        retry_delay = kwargs.pop("retry_delay", None) or self.config.retry_delay
        debug = kwargs.pop("debug", None)
        if debug is None:
            debug = self.config.debug

        total = len(targets)
        total_batches = math.ceil(total / batch_size)

        total_batches = math.ceil(total / batch_size)

        if debug:
            print(f"Starting BacklinksBulkPagesSummary for {total} targets in {total_batches} batch(es) of up to {batch_size}...")

        df_list: list[pd.DataFrame] = []

        for idx, batch_targets in enumerate(self._client._chunked(targets, batch_size), start=1):
            current_batch = list(batch_targets)
            if debug:
                print(f"Batch {idx}/{total_batches} ({len(current_batch)} targets)")

            result_df = await self._fetch_batch_with_fallback(
                current_batch, max_retries, retry_delay, debug
            )
            if result_df is not None and not result_df.empty:
                df_list.append(result_df)

            if idx < total_batches:
                await asyncio.sleep(batch_delay)

        if df_list:
            result = pd.concat(df_list, ignore_index=True)
            if domain:
                result["domain"] = domain
            return result
        return pd.DataFrame(columns=self._get_schema())
