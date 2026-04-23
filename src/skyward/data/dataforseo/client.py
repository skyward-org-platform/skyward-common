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

import time
from dataclasses import dataclass
from typing import Generator, List, TYPE_CHECKING

import requests
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3.util.retry import Retry

if TYPE_CHECKING:
    from skyward.data.bigquery import BigQueryClient


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

    def get_balance(self) -> dict:
        """
        Retrieve DataForSEO account balance and lifetime spend.

        Returns:
            Dict with keys:
              balance (float):  current USD balance — negative means overdrawn
              total   (float):  lifetime amount spent in USD
              raw     (dict):   full `money` block from the API response
        """
        url = f"{self.BASE_URL}/appendix/user_data"
        resp = self._get(url)
        try:
            money = resp["tasks"][0]["result"][0].get("money", {}) or {}
        except (TypeError, KeyError, IndexError):
            money = {}
        return {
            "balance": float(money.get("balance", 0.0) or 0.0),
            "total": float(money.get("total", 0.0) or 0.0),
            "raw": money,
        }

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

