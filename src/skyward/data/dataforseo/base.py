"""BaseEndpoint — the new abstract base for all DataForSEO endpoints.

Owns the orchestration layer: UUID validation, domain resolution via MetaClient,
the fetch → empty-guard → metadata-stamping → auto-upload pipeline.

Subclasses in `endpoints/<name>.py` provide endpoint-specific behavior by
implementing the abstract methods.
"""

from __future__ import annotations

import asyncio
import math
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import pandas as pd

from skyward.functions import _validate_job_id, generate_upload_id

if TYPE_CHECKING:
    from skyward.data.bigquery import BigQueryClient
    from skyward.data.dataforseo.client import ClientConfig, DataForSEOClient


# Sentinel for "arg not provided" to distinguish from explicit None
_UNSET: Any = object()


class BaseEndpoint(ABC):
    """Abstract base for DataForSEO endpoint wrappers."""

    LIVE_URL: str
    POST_URL: str | None = None
    READY_URL: str | None = None
    GET_URL: str | None = None
    FIXED_URL: str | None = None
    TABLE_NAME: str
    DATASET: str = "DataForSEO"

    def __init__(self, client: "DataForSEOClient") -> None:
        self._client = client

    @property
    def config(self) -> "ClientConfig":
        return self._client.config

    # ----- Abstract methods -----

    @abstractmethod
    def _build_payload(self, target: str, **kwargs) -> list[dict]: ...

    @abstractmethod
    def _parse_response(self, response: dict, target) -> pd.DataFrame:
        """Must return a df with a `task_id` column stamped per row."""

    @abstractmethod
    def _get_schema(self) -> list[str]: ...

    @abstractmethod
    def _get_dedupe_keys(self) -> list[str]: ...

    @abstractmethod
    def _cast_types(self, df: pd.DataFrame) -> pd.DataFrame: ...

    @abstractmethod
    def _fetch_live(self, target: str, **kwargs) -> pd.DataFrame:
        """Execute the HTTP live call and return a parsed df."""

    # ----- Public: live mode -----

    def live(
        self,
        target: str,
        *,
        domain: Any = _UNSET,
        domain_id: Any = _UNSET,
        job_id: str,
        interactive: bool = False,
        upload: bool = True,
        **kwargs,
    ) -> pd.DataFrame:
        _validate_job_id(job_id)
        resolved = self._resolve_domain(domain, domain_id, interactive)

        df = self._fetch_live(target, **kwargs)

        if df is None or df.empty:
            print("No rows returned. Skipping upload.")
            return pd.DataFrame(columns=self._get_schema() + ["domain_id", "domain", "endpoint_mode"])

        df = self._stamp_fetch_metadata(df, resolved, endpoint_mode="live")

        if upload:
            self.upload(self._client.bq_client, df, job_id=job_id)

        return df

    async def live_all(
        self,
        targets: list[str],
        *,
        domain: Any = _UNSET,
        domain_id: Any = _UNSET,
        job_id: str,
        interactive: bool = False,
        upload: bool = True,
        batch_size: int | None = None,
        batch_delay: float | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        _validate_job_id(job_id)
        resolved = self._resolve_domain(domain, domain_id, interactive)

        batch_size = batch_size or self.config.batch_size
        batch_delay = batch_delay if batch_delay is not None else self.config.batch_delay

        loop = asyncio.get_running_loop()
        df_list: list[pd.DataFrame] = []

        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            total = len(targets)
            total_batches = math.ceil(total / batch_size) if batch_size else 1
            batches = [targets[i : i + batch_size] for i in range(0, total, batch_size)]
            for idx, batch in enumerate(batches, start=1):
                if self.config.debug:
                    print(f"Processing batch {idx}/{total_batches} ({len(batch)} targets)")
                tasks = [
                    loop.run_in_executor(executor, lambda t=t: self._fetch_live(t, **kwargs))
                    for t in batch
                ]
                batch_results = await asyncio.gather(*tasks)
                for df in batch_results:
                    if isinstance(df, pd.DataFrame) and not df.empty:
                        df_list.append(df)
                if idx < total_batches:
                    await asyncio.sleep(batch_delay)

        if not df_list:
            print("No rows returned. Skipping upload.")
            return pd.DataFrame(columns=self._get_schema() + ["domain_id", "domain", "endpoint_mode"])

        combined = pd.concat(df_list, ignore_index=True)
        combined = self._stamp_fetch_metadata(combined, resolved, endpoint_mode="live")

        if upload:
            self.upload(self._client.bq_client, combined, job_id=job_id)

        return combined

    # ----- Public: POST/GET (standard) mode (default: unsupported) -----

    def post(self, target: str, **kwargs) -> pd.DataFrame:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support POST/GET workflow."
        )

    async def post_all(self, targets: list[str], **kwargs) -> pd.DataFrame:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support POST/GET workflow."
        )

    # ----- Upload -----

    def upload(
        self,
        bq_client: "BigQueryClient",
        df: pd.DataFrame,
        *,
        job_id: str,
        client_id: str | None = None,
    ) -> None:
        """Append rows to the endpoint's BQ table. Stamps job_id/upload_id/ingest_timestamp."""
        from google.cloud import bigquery

        _validate_job_id(job_id)

        if df is None or df.empty:
            print("Skipping upload - DataFrame is empty.")
            return

        df = df.copy()

        df["ingest_timestamp"] = pd.Timestamp.utcnow()
        df["ingest_timestamp"] = pd.to_datetime(df["ingest_timestamp"], utc=True)
        upload_id = generate_upload_id()
        df["upload_id"] = upload_id
        df["job_id"] = job_id

        df = self._cast_types(df)

        full_table_id = f"{bq_client.client.project}.{self.DATASET}.{self.TABLE_NAME}"
        row_count = len(df)
        timestamp = df["ingest_timestamp"].iloc[0]

        try:
            try:
                bq_client.client.get_table(full_table_id)
            except Exception:
                print(f"Table {full_table_id} does not exist. Create it before uploading.")
                return

            job_config = bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND
            )
            load_job = bq_client.client.load_table_from_dataframe(
                df, full_table_id, job_config=job_config
            )
            load_job.result()

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

    # ----- Helpers -----

    def _resolve_domain(self, domain: Any, domain_id: Any, interactive: bool) -> dict | None:
        """Resolve domain args to {"domain_id": int, "domain": str} or None."""
        provided_domain = domain is not _UNSET
        provided_id = domain_id is not _UNSET
        if provided_domain + provided_id != 1:
            raise ValueError(
                "Must pass exactly one of `domain=` (string or None) or `domain_id=` (int). "
                "Pass `domain=None` to explicitly opt out of domain tagging."
            )

        if provided_domain and domain is None:
            return None

        meta = self._client.meta_client
        if meta is None:
            raise RuntimeError(
                "Domain resolution requires a bq_client. Construct DataForSEOClient "
                "with `bq_client=` to enable Meta.domains lookup, or pass `domain=None` "
                "to opt out."
            )

        if provided_id:
            try:
                domain_id_int = int(domain_id)
            except (TypeError, ValueError):
                raise ValueError(f"domain_id must be an integer (got {domain_id!r})")
            from google.cloud import bigquery
            query = f"""
                SELECT domain_id, domain
                FROM `{meta._project_id}.Meta.domains`
                WHERE domain_id = @domain_id
                LIMIT 1
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("domain_id", "INT64", domain_id_int)
                ]
            )
            rows = meta.bq.client.query(query, job_config=job_config).result().to_dataframe()
            if rows.empty:
                raise ValueError(
                    f"Domain ID {domain_id_int} not found in Meta.domains. "
                    f"Provide a valid domain_id, or pass domain=<string> to auto-create by name."
                )
            row = rows.iloc[0]
            return {"domain_id": int(row["domain_id"]), "domain": row["domain"]}

        # domain string path
        existing = meta.get_domain(domain)
        if existing is not None:
            return {"domain_id": existing["domain_id"], "domain": existing["domain"]}

        cleaned = meta._clean_domain(domain, preserve_path=True)

        if interactive:
            return self._prompt_unknown_domain(cleaned)

        # Auto-create (bypass mode)
        added = meta.add_domains([cleaned])
        if not added:
            raise RuntimeError(f"Auto-create failed for domain {cleaned!r}")
        first = added[0]
        print(f"[auto-added domain {cleaned!r} as id {first['domain_id']}]")
        return {"domain_id": int(first["domain_id"]), "domain": first["domain"]}

    def _prompt_unknown_domain(self, cleaned: str) -> dict:
        meta = self._client.meta_client
        while True:
            choice = input(
                f"\nDomain {cleaned!r} not found in Meta.domains.\n"
                f"  [r] retype\n"
                f"  [a] add this domain to the system and continue\n"
                f"  [x] abort\n"
                f"> "
            ).strip().lower()
            if choice == "x":
                raise RuntimeError(f"User aborted for unknown domain {cleaned!r}")
            if choice == "r":
                retyped = input("Enter the domain: ").strip()
                if not retyped:
                    continue
                cleaned = meta._clean_domain(retyped, preserve_path=True)
                existing = meta.get_domain(cleaned)
                if existing is not None:
                    return {"domain_id": existing["domain_id"], "domain": existing["domain"]}
                continue
            if choice == "a":
                added = meta.add_domains([cleaned])
                first = added[0]
                print(f"[added domain {cleaned!r} as id {first['domain_id']}]")
                return {"domain_id": int(first["domain_id"]), "domain": first["domain"]}

    def _stamp_fetch_metadata(
        self, df: pd.DataFrame, resolved: dict | None, endpoint_mode: str
    ) -> pd.DataFrame:
        """Stamp `domain_id`, `domain`, `endpoint_mode` on every row."""
        df = df.copy()
        if resolved is None:
            df["domain_id"] = pd.Series([pd.NA] * len(df), dtype="Int64")
            df["domain"] = pd.Series([pd.NA] * len(df), dtype="string")
        else:
            df["domain_id"] = pd.Series([resolved["domain_id"]] * len(df), dtype="Int64")
            df["domain"] = pd.Series([resolved["domain"]] * len(df), dtype="string")
        df["endpoint_mode"] = endpoint_mode
        return df
