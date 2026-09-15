"""Cached DataForSEO location catalog in BigQuery (DataForSEO.locations).

One table holds every code from the three DFS lists (SERP, Google Ads, Labs) with a flag
per list. Codes are one shared system; the lists only differ in which codes each endpoint
accepts. All three list endpoints are free. `refresh()` is the only code that calls them
for the catalog.
"""

from __future__ import annotations

import json
import logging
import threading

import pandas as pd

from skyward.functions import generate_job_id, generate_upload_id

logger = logging.getLogger(__name__)

DATASET = "DataForSEO"
LOCATIONS_TABLE = "locations"
SOURCES: tuple[tuple[str, str], ...] = (
    ("in_serp", "serp/google/locations"),
    ("in_google_ads", "keywords_data/google_ads/locations"),
    ("in_labs", "dataforseo_labs/locations_and_languages"),
)
FLAGS = tuple(flag for flag, _ in SOURCES)
LOCATION_COLUMNS = [
    "location_code", "location_name", "location_code_parent", "country_iso_code",
    "location_type", "in_serp", "in_google_ads", "in_labs", "available_languages",
    "refreshed_at",
]
_TEXT_FIELDS = ("location_name", "location_code_parent", "country_iso_code", "location_type")


def merge_location_lists(lists: dict[str, list[dict]], refreshed_at) -> pd.DataFrame:
    merged: dict[int, dict] = {}
    for flag in FLAGS:
        for loc in lists.get(flag) or []:
            code = loc.get("location_code")
            if code is None:
                continue
            row = merged.setdefault(int(code), {
                "location_code": int(code), "location_name": None,
                "location_code_parent": None, "country_iso_code": None,
                "location_type": None, "in_serp": False, "in_google_ads": False,
                "in_labs": False, "available_languages": None, "refreshed_at": refreshed_at,
            })
            row[flag] = True
            for key in _TEXT_FIELDS:
                if row[key] is None and loc.get(key) is not None:
                    row[key] = loc[key]
            if flag == "in_labs" and loc.get("available_languages") is not None:
                row["available_languages"] = json.dumps(loc["available_languages"], default=str)
    df = pd.DataFrame(list(merged.values()), columns=LOCATION_COLUMNS)
    df["location_code"] = df["location_code"].astype("Int64")
    df["location_code_parent"] = pd.to_numeric(df["location_code_parent"], errors="coerce").astype("Int64")
    for flag in FLAGS:
        df[flag] = df[flag].astype("boolean")
    return df


class LocationCatalog:
    def __init__(self, client) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._support: dict[int, dict | None] = {}
        self._queries: dict[tuple, list[dict]] = {}

    def _bq(self):
        return self._client.bq_client

    def _table(self) -> str:
        return f"{self._bq().client.project}.{DATASET}.{LOCATIONS_TABLE}"

    def fetch_lists(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for flag, path in SOURCES:
            resp = self._client._get(f"{self._client.BASE_URL}/{path}")
            try:
                out[flag] = resp["tasks"][0]["result"] or []
            except (TypeError, KeyError, IndexError):
                out[flag] = []
        return out

    def refresh(self) -> int:
        from google.cloud import bigquery

        bq = self._bq()
        if bq is None:
            raise RuntimeError("refresh_locations requires a BigQuery client.")
        lists = self.fetch_lists()
        empty = [flag for flag, rows in lists.items() if not rows]
        if empty:
            raise RuntimeError(
                f"DataForSEO returned no locations for {empty}; refusing to overwrite "
                f"{DATASET}.{LOCATIONS_TABLE}.")
        ts = pd.Timestamp.now("UTC")
        df = merge_location_lists(lists, ts)
        cfg = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)
        bq.client.load_table_from_dataframe(df, self._table(), job_config=cfg).result()
        try:
            bq.log_upload_event(
                job_id=generate_job_id(), upload_id=generate_upload_id(), source="dataforseo",
                source_program="refresh_locations", dataset=DATASET, table=LOCATIONS_TABLE,
                row_count=len(df), timestamp=ts,
            )
        except Exception as e:  # noqa: BLE001
            print(f"Log failed: {e}")
        with self._lock:
            self._support.clear()
            self._queries.clear()
        return len(df)

    def is_supported(self, location_code: int, flag: str) -> bool | None:
        if flag not in FLAGS:
            raise ValueError(f"Unknown location flag {flag!r}; expected one of {FLAGS}")
        code = int(location_code)
        with self._lock:
            if code in self._support:
                flags = self._support[code]
                return None if flags is None else flags[flag]
        flags = self._read_support(code)
        with self._lock:
            self._support[code] = flags
        return None if flags is None else flags[flag]

    def _read_support(self, code: int) -> dict | None:
        bq = self._bq()
        if bq is None:
            return None
        from google.cloud import bigquery

        sql = (f"SELECT (SELECT COUNT(*) FROM `{self._table()}`) AS total_rows, "
               f"LOGICAL_OR(in_serp) AS in_serp, LOGICAL_OR(in_google_ads) AS in_google_ads, "
               f"LOGICAL_OR(in_labs) AS in_labs "
               f"FROM `{self._table()}` WHERE location_code = @code")
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("code", "INT64", code)])
        try:
            df = bq.client.query(sql, job_config=cfg).result().to_dataframe()
        except Exception as e:  # noqa: BLE001 - never block a run on the catalog
            logger.warning("Location catalog read failed: %r", e)
            return None
        if df.empty or not int(df.iloc[0]["total_rows"] or 0):
            return None
        row = df.iloc[0]
        return {f: (False if pd.isna(row[f]) else bool(row[f])) for f in FLAGS}

    def get(self, *, location_type: str | None = None, country_iso_code: str | None = None,
            supported_by: str | None = None, location_code: int | None = None) -> list[dict]:
        if supported_by is not None and supported_by not in FLAGS:
            raise ValueError(f"Unknown location flag {supported_by!r}; expected one of {FLAGS}")
        key = (location_type, country_iso_code, supported_by, location_code)
        with self._lock:
            if key in self._queries:
                return self._queries[key]
        rows = self._read_rows(location_type, country_iso_code, supported_by, location_code)
        if not rows and supported_by is None:
            rows = self._fallback(location_type, country_iso_code, location_code)
        elif not rows and supported_by is not None:
            logger.warning("Location catalog read failed for supported_by=%r; cache unavailable", supported_by)
        with self._lock:
            self._queries[key] = rows
        return rows

    def _read_rows(self, location_type, country_iso_code, supported_by, location_code) -> list[dict]:
        bq = self._bq()
        if bq is None:
            return []
        from google.cloud import bigquery

        where: list[str] = []
        params: list = []
        if location_type is not None:
            where.append("location_type = @location_type")
            params.append(bigquery.ScalarQueryParameter("location_type", "STRING", location_type))
        if country_iso_code is not None:
            where.append("country_iso_code = @country_iso_code")
            params.append(bigquery.ScalarQueryParameter("country_iso_code", "STRING", country_iso_code))
        if supported_by is not None:
            where.append(supported_by)
        if location_code is not None:
            where.append("location_code = @location_code")
            params.append(bigquery.ScalarQueryParameter("location_code", "INT64", int(location_code)))
        sql = f"SELECT * FROM `{self._table()}`"
        if where:
            sql += " WHERE " + " AND ".join(where)
        try:
            df = bq.client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result().to_dataframe()
        except Exception as e:  # noqa: BLE001
            logger.warning("Location catalog read failed: %r", e)
            return []
        return df.to_dict("records")

    def _fallback(self, location_type, country_iso_code, location_code) -> list[dict]:
        """Cache empty or unreadable: filter the live SERP list (free) in Python."""
        rows = self._client.get_serp_locations()
        return [
            r for r in rows
            if (location_type is None or r.get("location_type") == location_type)
            and (country_iso_code is None or r.get("country_iso_code") == country_iso_code)
            and (location_code is None or r.get("location_code") == int(location_code))
        ]
