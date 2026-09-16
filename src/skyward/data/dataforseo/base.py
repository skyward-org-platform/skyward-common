"""BaseEndpoint — the new abstract base for all DataForSEO endpoints.

Owns the orchestration layer: UUID validation, domain resolution via MetaClient,
the fetch → empty-guard → metadata-stamping → auto-upload pipeline.

Subclasses in `endpoints/<name>.py` provide endpoint-specific behavior by
implementing the abstract methods.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

import pandas as pd

from skyward.data.dataforseo.debug_log import build_attempt_record
from skyward.data.dataforseo.estimates import CostEstimate, RunPlan
from skyward.data.dataforseo.exceptions import InsufficientBalanceError, InvalidLocationError
from skyward.data.dataforseo.run import (
    DEFAULT_BALANCE_BUFFER, RunContext, check_balance, round_money, target_list,
    write_job_run_row,
)
from skyward.functions import _validate_job_id, generate_upload_id

if TYPE_CHECKING:
    from skyward.data.bigquery import BigQueryClient
    from skyward.data.dataforseo.client import ClientConfig, DataForSEOClient

logger = logging.getLogger(__name__)


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

    ENDPOINT_KEY: str = ""

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if "ENDPOINT_KEY" not in cls.__dict__:
            cls.ENDPOINT_KEY = cls.__module__.rsplit(".", 1)[-1]

    def __init__(self, client: "DataForSEOClient") -> None:
        self._client = client

    @property
    def config(self) -> "ClientConfig":
        return self._client.config

    @property
    def location_flag(self) -> str | None:
        """Which DataForSEO.locations flag this endpoint's location_code must have."""
        key = self.ENDPOINT_KEY
        if key == "dataforseo_labs_google_search_intent":
            return None
        if key.startswith("dataforseo_labs_"):
            return "in_labs"
        if key.startswith("serp_"):
            return "in_serp"
        if key.startswith("keywords_data_google_ads_"):
            return "in_google_ads"
        return None

    # ----- Cost planning -----

    def plan(self, targets: list[str], *, endpoint_mode: str = "live", **kwargs) -> RunPlan:
        """Worst-case requests, billable items and rows for a run. Endpoints override."""
        n = len(targets)
        return self._make_plan(targets, endpoint_mode, requests=n, items=n, max_rows=n)

    def _make_plan(self, targets, endpoint_mode: str, *, requests: int, items: int,
                   max_rows: int, **price_inputs) -> RunPlan:
        return RunPlan(
            endpoint=self.ENDPOINT_KEY,
            endpoint_mode=endpoint_mode,
            planned_requests=int(requests),
            planned_items=int(items),
            planned_max_rows=int(max_rows),
            targets=tuple(str(t) for t in targets),
            price_inputs={k: v for k, v in price_inputs.items() if v is not None},
        )

    def estimate_cost(self, targets, *, endpoint_mode: str = "live", **kwargs) -> CostEstimate:
        """Max (list price + buffer) and average cost for a run with these inputs. Spends nothing."""
        return self._client.cost_estimator.estimate(
            self.plan(target_list(targets), endpoint_mode=endpoint_mode, **kwargs))

    @staticmethod
    def _in_unit(run: RunContext | None, target, fn: Callable, *, mark_complete: bool = True):
        return fn() if run is None else run.run_unit(target, fn, mark_complete=mark_complete)

    def _check_location(self, plan_kwargs: dict, ignore: bool) -> None:
        flag = self.location_flag
        if flag is None or ignore:
            return
        code = plan_kwargs.get("location_code") or self.config.location_code
        if code is None:
            return
        if self._client.locations.is_supported(int(code), flag) is False:
            raise InvalidLocationError(
                f"[{self.ENDPOINT_KEY}] location_code {code} is not supported by this endpoint "
                f"({flag} is false in DataForSEO.locations). Pass ignore_location_check=True "
                f"to send it anyway.",
                location_code=int(code), endpoint=self.ENDPOINT_KEY, supported_by=flag,
            )

    def _write_rejection(self, job_id: str, plan: RunPlan, estimate: CostEstimate,
                         endpoint_mode: str, status: str, error: BaseException) -> None:
        write_job_run_row(self._client.bq_client, {
            "job_id": job_id, "endpoint": self.ENDPOINT_KEY, "endpoint_mode": endpoint_mode,
            "event": "end", "status": status,
            "planned_requests": plan.planned_requests, "planned_items": plan.planned_items,
            "planned_max_rows": plan.planned_max_rows,
            "estimate_max_usd": round_money(estimate.max_usd),
            "estimate_avg_usd": round_money(estimate.avg_usd),
            "balance_at_start": round_money(getattr(error, "balance", None)),
            "balance_override": False,
            "error": repr(error)[:1000], "client_id": None,
            "ingest_timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _start_run(
        self,
        targets: list[str],
        *,
        job_id: str,
        resolved: dict | None,
        endpoint_mode: str,
        upload: bool,
        balance_buffer: float,
        ignore_balance_check: bool,
        ignore_location_check: bool,
        upload_batch_rows: int | None,
        plan_kwargs: dict,
        empty_columns: list[str],
        tag_cost_with_upload: bool = True,
    ) -> RunContext:
        plan = self.plan(targets, endpoint_mode=endpoint_mode, **plan_kwargs)
        estimate = self._client.cost_estimator.estimate(plan)
        try:
            self._check_location(plan_kwargs, ignore_location_check)
        except InvalidLocationError as e:
            self._write_rejection(job_id, plan, estimate, endpoint_mode, "rejected_location", e)
            raise
        try:
            balance = check_balance(
                self._client, required_usd=estimate.max_usd, balance_buffer=balance_buffer,
                ignore=ignore_balance_check, job_id=job_id, endpoint=self.ENDPOINT_KEY,
                remaining_targets=plan.targets,
            )
        except InsufficientBalanceError as e:
            self._write_rejection(job_id, plan, estimate, endpoint_mode, "rejected_low_balance", e)
            raise
        client = self._client

        def _write(df: pd.DataFrame, uid: str) -> None:
            # upload() never raises (it prints and returns False on failure) so a save
            # that didn't reach BigQuery must be reported here, not inferred from an
            # exception — otherwise the run's job_runs row and cost_log rows go on to
            # claim a save that never happened.
            if not self.upload(client.bq_client, df, job_id=job_id, upload_id=uid):
                run.note_save_failure(uid)

        run = RunContext(
            client=client, endpoint_key=self.ENDPOINT_KEY, job_id=job_id, plan=plan,
            estimate=estimate, endpoint_mode=endpoint_mode, upload=upload,
            write=_write,
            stamp=lambda df: self._stamp_fetch_metadata(df, resolved, endpoint_mode=endpoint_mode),
            empty_columns=empty_columns, upload_batch_rows=upload_batch_rows,
            balance_buffer=balance_buffer, ignore_balance_check=ignore_balance_check,
            tag_cost_with_upload=tag_cost_with_upload,
        )
        run.start(balance)
        return run

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
        include_debug_logs: bool = False,
        balance_buffer: float = DEFAULT_BALANCE_BUFFER,
        ignore_balance_check: bool = False,
        ignore_location_check: bool = False,
        upload_batch_rows: int | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        _validate_job_id(job_id)
        resolved = self._resolve_domain(domain, domain_id, interactive)
        run = self._start_run(
            target_list(target), job_id=job_id, resolved=resolved, endpoint_mode="live",
            upload=upload, balance_buffer=balance_buffer,
            ignore_balance_check=ignore_balance_check,
            ignore_location_check=ignore_location_check, upload_batch_rows=upload_batch_rows,
            plan_kwargs={**kwargs, "_single_call": True},
            empty_columns=self._get_schema() + ["domain_id", "domain", "endpoint_mode"],
        )
        collector = self._make_debug_collector(job_id, include_debug_logs)
        try:
            run.run_unit(target, lambda: self._fetch_live(target, _debug_collector=collector, **kwargs))
        except BaseException as exc:
            # close() no longer raises when handed a cause, but never let a failure in
            # there cost us the original exception: the bare `raise` below is what the
            # caller is waiting for, and it only runs if close() returns.
            try:
                run.close(error=exc)
            except Exception as close_exc:  # noqa: BLE001
                logger.error("[%s] job %s: close() failed while handling %r: %r",
                             self.TABLE_NAME, job_id, exc, close_exc)
            raise
        finally:
            if collector is not None:
                collector.flush()
        return run.close()

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
        include_debug_logs: bool = False,
        balance_buffer: float = DEFAULT_BALANCE_BUFFER,
        ignore_balance_check: bool = False,
        ignore_location_check: bool = False,
        upload_batch_rows: int | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        _validate_job_id(job_id)
        resolved = self._resolve_domain(domain, domain_id, interactive)

        batch_size = batch_size or self.config.batch_size
        batch_delay = batch_delay if batch_delay is not None else self.config.batch_delay

        run = self._start_run(
            list(targets), job_id=job_id, resolved=resolved, endpoint_mode="live",
            upload=upload, balance_buffer=balance_buffer,
            ignore_balance_check=ignore_balance_check,
            ignore_location_check=ignore_location_check, upload_batch_rows=upload_batch_rows,
            plan_kwargs={**kwargs, "batch_size": batch_size},
            empty_columns=self._get_schema() + ["domain_id", "domain", "endpoint_mode"],
        )
        collector = self._make_debug_collector(job_id, include_debug_logs)
        loop = asyncio.get_running_loop()

        try:
            with ThreadPoolExecutor(max_workers=batch_size) as executor:
                total = len(targets)
                total_batches = math.ceil(total / batch_size) if batch_size else 1
                batches = [targets[i : i + batch_size] for i in range(0, total, batch_size)]
                for idx, batch in enumerate(batches, start=1):
                    if self.config.debug:
                        print(f"Processing batch {idx}/{total_batches} ({len(batch)} targets)")
                    tasks = [
                        loop.run_in_executor(
                            executor,
                            lambda t=t: run.run_unit(
                                t, lambda: self._fetch_live(t, _debug_collector=collector, **kwargs)),
                        )
                        for t in batch
                    ]
                    await asyncio.gather(*tasks)
                    if idx < total_batches:
                        await asyncio.sleep(batch_delay)
        except BaseException as exc:
            run.close(error=exc)
            raise
        finally:
            if collector is not None:
                collector.flush()

        return run.close()

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
        upload_id: str | None = None,
    ) -> bool:
        """Append rows to the endpoint's BQ table. Stamps job_id/upload_id/ingest_timestamp.

        Returns True when the rows were loaded (or there was nothing to load), and False
        when the save did not happen — the table doesn't exist, or the load/log step
        raised. Callers that must know whether data actually reached BigQuery (rather than
        merely having attempted it) need this return value: the failure paths only print,
        which is invisible to a caller and to RunContext's job_runs bookkeeping.
        """
        from google.cloud import bigquery

        _validate_job_id(job_id)

        if df is None or df.empty:
            print("Skipping upload - DataFrame is empty.")
            return True

        df = df.copy()

        df["ingest_timestamp"] = pd.Timestamp.now("UTC")
        df["ingest_timestamp"] = pd.to_datetime(df["ingest_timestamp"], utc=True)
        upload_id = upload_id or generate_upload_id()
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
                return False

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
            return True

        except Exception as e:
            print(f"Upload failed: {e}")
            return False

    # ----- Helpers -----

    def _make_debug_collector(self, job_id: str, include_debug_logs: bool):
        """Allocate a run-scoped DebugLogCollector, or None when logging is off.

        Returns None (logging disabled) when the flag is off or no bq_client is
        wired, so the retry loop's `_debug_collector` is a simple truthiness check.
        """
        if not include_debug_logs:
            return None
        bq = self._client.bq_client
        if bq is None:
            print("include_debug_logs=True but no bq_client; debug logging disabled.")
            return None
        from skyward.data.dataforseo.debug_log import DebugLogCollector
        return DebugLogCollector(bq, job_id=job_id)

    def _run_live_loop(
        self,
        *,
        target,
        parse_target,
        payload: list[dict],
        max_retries: int,
        retry_delay: float,
        debug: bool,
        collector,
        empty_columns: list[str],
    ) -> pd.DataFrame:
        """Shared live retry loop with optional per-attempt debug capture.

        Posts `payload`, parses with `parse_target`, and returns the first
        non-empty df. When `collector` is set, records one debug row per attempt
        (timing, transport status via the `_post` status_sink, DFS task fields).
        `endpoint` on each row is derived from `LIVE_URL` (e.g. `keyword_suggestions`).
        """
        url = f"{self._client.BASE_URL}/{self.LIVE_URL}"
        endpoint_name = self.LIVE_URL.split("/")[-2]

        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                time.sleep(retry_delay)

            sink: dict | None = {} if collector is not None else None
            t0 = time.perf_counter()
            started_at = datetime.now(timezone.utc).isoformat()
            resp = self._client._post(
                url, payload, max_retries=1, retry_delay=0, status_sink=sink
            )
            elapsed_ms = int((time.perf_counter() - t0) * 1000)

            df = None
            n_items = 0
            if resp:
                try:
                    df = self._parse_response(resp, parse_target)
                    n_items = 0 if (df is None or df.empty) else len(df)
                except Exception as e:
                    df = None
                    if debug:
                        print(f"[{endpoint_name}:{target}] Parse error: {e}. "
                              f"Attempt {attempt}/{max_retries}")
            elif debug:
                print(f"[{endpoint_name}:{target}] Invalid response. "
                      f"Attempt {attempt}/{max_retries}")

            is_terminal = (n_items > 0) or (attempt == max_retries)
            if collector is not None:
                collector.record(build_attempt_record(
                    endpoint=endpoint_name,
                    target=target,
                    attempt=attempt,
                    is_terminal=is_terminal,
                    started_at=started_at,
                    duration_ms=elapsed_ms,
                    status_sink=sink,
                    payload=payload,
                    resp=resp,
                    n_items=n_items,
                ))

            if df is not None and not df.empty:
                return df

        return pd.DataFrame(columns=empty_columns)

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
                "Domain resolution requires SUPABASE_DB_URL (Meta lives in Supabase as "
                "of v1.5.0). Set it, or pass `domain=None` to opt out of domain tagging."
            )

        if provided_id:
            try:
                domain_id_int = int(domain_id)
            except (TypeError, ValueError):
                raise ValueError(f"domain_id must be an integer (got {domain_id!r})")
            found = meta.get_domain_by_id(domain_id_int)
            if found is None:
                raise ValueError(
                    f"Domain ID {domain_id_int} not found in meta.domains. "
                    f"Provide a valid domain_id, or pass domain=<string> to auto-create by name."
                )
            return {"domain_id": found["domain_id"], "domain": found["domain"]}

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
                f"\nDomain {cleaned!r} not found in meta.domains.\n"
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
