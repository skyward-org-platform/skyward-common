from __future__ import annotations

import pandas as pd
from datetime import datetime
from typing import Optional, List
from google.cloud import bigquery

from skyward.data.meta import MetaClient


class DataHub(MetaClient):
    """Central interface for querying data via Meta tables.

    NOTE: Several methods on this class depend on ``client_id`` / ``project_id``
    being populated on ``Logs.upload_events``. Those fields are not reliably set
    today, so the affected methods are soft-deprecated and documented on each
    method. See KNOWN_ISSUES.md in the repo root for the full list.
    """

    # Tables with a ``domain`` column that support domain-based lookup via
    # Meta.domains + Meta.client_domains (see get_client_data).
    # All other tables use job_id lookup (via Logs.upload_events).
    DOMAIN_TABLES = {
        "dataforseo_labs-google-ranked_keywords",
        "backlinks_backlinks_live",
        "serp_google_organic_live_advanced",
        "backlinks_bulk_pages_summary_live",
        "backlinks_summary_live",
    }

    def __init__(self, sb_client, bq_client):
        """Hybrid data hub: entities/catalogs in Supabase, analytics in BigQuery.

        Args:
            sb_client: SupabaseClient for the meta.* schema (entities + catalogs).
            bq_client: BigQueryClient for analytical data, Logs.upload_events,
                       and INFORMATION_SCHEMA scans.
        """
        super().__init__(sb_client)
        self.bq = bq_client
        # BQ project id, used by the f-string BQ queries below.
        self._project_id = bq_client.client.project

    # ══════════════════════════════════════════════════════════════════════════
    # Upload log queries
    # ══════════════════════════════════════════════════════════════════════════

    def search_uploads(
        self,
        client_id: Optional[str] = None,
        project_id: Optional[str] = None,
        job_id: Optional[str] = None,
        upload_id: Optional[str] = None,
        dataset: Optional[str] = None,
        table: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 50,
    ) -> pd.DataFrame:
        """
        Search upload log with flexible filters.

        All parameters are optional. Multiple parameters are ANDed together.

        .. warning::
            The ``client_id`` and ``project_id`` filters are best-effort. Those
            fields are not reliably populated on ``Logs.upload_events`` today, so
            filtering by them will silently miss uploads where the field is null.
            Prefer filtering by ``job_id``, ``upload_id``, ``dataset``, or
            ``table`` when you need complete results. See KNOWN_ISSUES.md.

        Args:
            client_id: Filter by client (unreliable — see warning above)
            project_id: Filter by project (unreliable — see warning above)
            job_id: Filter by job
            upload_id: Filter by specific upload
            dataset: Filter by dataset name
            table: Filter by table name
            since: Filter to uploads after this timestamp
            limit: Maximum rows to return

        Returns:
            DataFrame of matching upload log entries, ordered by ingest_timestamp DESC
        """
        params = []
        conditions = []

        if client_id is not None:
            conditions.append("client_id = @client_id")
            params.append(bigquery.ScalarQueryParameter("client_id", "STRING", client_id))

        if project_id is not None:
            conditions.append("project_id = @project_id")
            params.append(bigquery.ScalarQueryParameter("project_id", "STRING", project_id))

        if job_id is not None:
            conditions.append("job_id = @job_id")
            params.append(bigquery.ScalarQueryParameter("job_id", "STRING", job_id))

        if upload_id is not None:
            conditions.append("upload_id = @upload_id")
            params.append(bigquery.ScalarQueryParameter("upload_id", "STRING", upload_id))

        if dataset is not None:
            conditions.append("dataset = @dataset")
            params.append(bigquery.ScalarQueryParameter("dataset", "STRING", dataset))

        if table is not None:
            conditions.append("`table` = @table")
            params.append(bigquery.ScalarQueryParameter("table", "STRING", table))

        if since is not None:
            conditions.append("ingest_timestamp >= @since")
            params.append(bigquery.ScalarQueryParameter("since", "TIMESTAMP", since))

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(bigquery.ScalarQueryParameter("limit_val", "INT64", limit))

        query = f"""
            SELECT *
            FROM `{self._project_id}.Logs.upload_events`
            {where_clause}
            ORDER BY ingest_timestamp DESC
            LIMIT @limit_val
        """

        job_config = bigquery.QueryJobConfig(query_parameters=params)
        return self.bq.client.query(query, job_config=job_config).result().to_dataframe()

    def get_upload_summary(self, client_id: str) -> pd.DataFrame:
        """
        Get summary of uploads per table for a client.

        .. deprecated::
            ``client_id`` is not reliably populated on ``Logs.upload_events``,
            so this method will miss any uploads where ``client_id`` is null.
            Results are lower-bound only. Do not use for anything that requires
            a complete picture of a client's uploads. See KNOWN_ISSUES.md.

        Args:
            client_id: The client identifier

        Returns:
            DataFrame with: dataset, table, total_rows, upload_count, latest_upload
        """
        query = f"""
            SELECT
                dataset,
                `table`,
                SUM(row_count) AS total_rows,
                COUNT(*) AS upload_count,
                MAX(ingest_timestamp) AS latest_upload
            FROM `{self._project_id}.Logs.upload_events`
            WHERE client_id = @client_id
            GROUP BY dataset, `table`
            ORDER BY latest_upload DESC
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("client_id", "STRING", client_id)
            ]
        )
        return self.bq.client.query(query, job_config=job_config).result().to_dataframe()

    def preview_upload(
        self,
        upload_id: str,
        limit: int = 100,
    ) -> pd.DataFrame:
        """
        Preview rows from a specific upload.

        Looks up the dataset and table from the upload log, then queries
        the actual data table for rows with matching upload_id.

        Args:
            upload_id: The upload identifier
            limit: Maximum rows to return

        Returns:
            DataFrame with preview rows, or empty if upload not found
        """
        # First, find the upload in the log
        log_query = f"""
            SELECT dataset, `table`
            FROM `{self._project_id}.Logs.upload_events`
            WHERE upload_id = @upload_id
            LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("upload_id", "STRING", upload_id)
            ]
        )
        log_result = self.bq.client.query(log_query, job_config=job_config).result()
        log_rows = list(log_result)

        if not log_rows:
            return pd.DataFrame()  # Upload not found

        dataset = log_rows[0].dataset
        table = log_rows[0].table

        # Query the actual table
        data_query = f"""
            SELECT *
            FROM `{self._project_id}.{dataset}.{table}`
            WHERE upload_id = @upload_id
            LIMIT @limit_val
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("upload_id", "STRING", upload_id),
                bigquery.ScalarQueryParameter("limit_val", "INT64", limit),
            ]
        )
        return self.bq.client.query(data_query, job_config=job_config).result().to_dataframe()

    # ══════════════════════════════════════════════════════════════════════════
    # Table catalog
    # ══════════════════════════════════════════════════════════════════════════

    def list_tables(
        self,
        dataset: Optional[str] = None,
        active_only: bool = True,
    ) -> pd.DataFrame:
        """
        List tables from the cached catalog. No INFORMATION_SCHEMA scan.

        Args:
            dataset: Filter to a specific dataset. None returns all.
            active_only: If True, only return is_active = TRUE rows.

        Returns:
            DataFrame with dataset, table_name, row_count, size_bytes,
            is_active, status_changed_at, notes, last_indexed_at
        """
        params = {}
        conditions = []

        if dataset is not None:
            conditions.append("dataset = %(dataset)s")
            params["dataset"] = dataset

        if active_only:
            conditions.append("is_active = TRUE")

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"""
            SELECT dataset, table_name, row_count, size_bytes,
                   is_active, status_changed_at, notes, last_indexed_at
            FROM meta.table_catalog
            {where_clause}
            ORDER BY dataset, table_name
        """
        return self.sb.query(query, params)

    def reindex_catalog(self, dataset: str) -> dict:
        """
        Re-index the table catalog for a specific dataset.

        Scans INFORMATION_SCHEMA.TABLES and INFORMATION_SCHEMA.TABLE_STORAGE
        for the target dataset, diffs against Meta.table_catalog, and applies
        inserts/updates.

        Args:
            dataset: The dataset to re-index (e.g., 'DataForSEO')

        Returns:
            {
                "dataset": "DataForSEO",
                "new_tables": ["table_a", "table_b"],
                "deactivated_tables": ["old_table"],
                "reactivated_tables": [],
                "updated_tables": ["existing_1", "existing_2"],
                "total_active": 14
            }
        """
        project = self._project_id

        # 1. Read current catalog state from Supabase for the diff summary.
        catalog = self.sb.query(
            "select table_name, is_active from meta.table_catalog where dataset = %(dataset)s",
            {"dataset": dataset},
        )
        catalog_table_names = set(catalog["table_name"]) if not catalog.empty else set()
        catalog_active = set(catalog[catalog["is_active"] == True]["table_name"]) if not catalog.empty else set()
        catalog_inactive = set(catalog[catalog["is_active"] == False]["table_name"]) if not catalog.empty else set()

        # 2. Scan BQ INFORMATION_SCHEMA for current tables + storage (one query).
        bq_scan_query = f"""
            SELECT
                t.table_name,
                ts.total_rows AS row_count,
                ts.total_logical_bytes AS size_bytes
            FROM `{project}.{dataset}.INFORMATION_SCHEMA.TABLES` t
            LEFT JOIN `region-us.INFORMATION_SCHEMA.TABLE_STORAGE` ts
                ON ts.table_schema = '{dataset}'
                AND t.table_name = ts.table_name
            WHERE t.table_type = 'BASE TABLE'
              AND NOT STARTS_WITH(t.table_name, 'temp_')
              AND NOT STARTS_WITH(t.table_name, '_temp_')
        """
        bq_df = self.bq.client.query(bq_scan_query).result().to_dataframe()
        bq_table_names = set(bq_df["table_name"]) if not bq_df.empty else set()

        # 3. Compute diff for summary.
        new_tables = bq_table_names - catalog_table_names
        missing_tables = catalog_active - bq_table_names
        reappearing_tables = catalog_inactive & bq_table_names
        existing_tables = catalog_active & bq_table_names

        # 4. Upsert every scanned table into the Supabase catalog. status_changed_at
        #    flips only when an inactive row reactivates (matches the old MERGE).
        for row in bq_df.itertuples(index=False):
            row_count = None if pd.isna(row.row_count) else int(row.row_count)
            size_bytes = None if pd.isna(row.size_bytes) else int(row.size_bytes)
            self.sb.execute(
                """
                INSERT INTO meta.table_catalog
                    (dataset, table_name, row_count, size_bytes, is_active,
                     status_changed_at, notes, last_indexed_at)
                VALUES (%(dataset)s, %(table_name)s, %(row_count)s, %(size_bytes)s,
                        TRUE, NULL, NULL, now())
                ON CONFLICT (dataset, table_name) DO UPDATE SET
                    row_count = excluded.row_count,
                    size_bytes = excluded.size_bytes,
                    is_active = TRUE,
                    status_changed_at = CASE
                        WHEN meta.table_catalog.is_active = FALSE THEN now()
                        ELSE meta.table_catalog.status_changed_at
                    END,
                    last_indexed_at = now()
                """,
                {
                    "dataset": dataset,
                    "table_name": row.table_name,
                    "row_count": row_count,
                    "size_bytes": size_bytes,
                },
            )

        # 5. Deactivate catalog tables that are active but no longer in BQ.
        if missing_tables:
            self.sb.execute(
                """
                UPDATE meta.table_catalog
                SET is_active = FALSE, status_changed_at = now(), last_indexed_at = now()
                WHERE dataset = %(dataset)s
                  AND table_name = ANY(%(names)s)
                  AND is_active = TRUE
                """,
                {"dataset": dataset, "names": sorted(missing_tables)},
            )

        return {
            "dataset": dataset,
            "new_tables": sorted(new_tables),
            "deactivated_tables": sorted(missing_tables),
            "reactivated_tables": sorted(reappearing_tables),
            "updated_tables": sorted(existing_tables),
            "total_active": len(bq_table_names),
        }

    def scan_datasets(self, prefixes: dict = None, full: bool = False) -> dict:
        """Scan BQ datasets and update meta.dataset_catalog with type and hostname.

        Hybrid: dataset discovery + GA4 hostname resolution come from BigQuery
        (self.bq); the catalog rows are written to Supabase (self.sb).

        Args:
            prefixes: Dict mapping type names to list of prefixes.
                      Defaults to MetaClient.DEFAULT_DATASET_PREFIXES.
            full: If True, scan ALL datasets (slow). If False, only scan
                  datasets matching the prefix patterns (fast).

        Returns:
            Dict mapping type names to lists of dataset info dicts.
        """
        if prefixes is None:
            prefixes = self.DEFAULT_DATASET_PREFIXES

        prefix_map = []
        for ds_type, prefix_list in prefixes.items():
            for prefix in prefix_list:
                prefix_map.append((prefix.lower(), ds_type))

        # Get all datasets from BQ
        all_datasets = list(self.bq.client.list_datasets())

        categorized = {}
        unrecognized = []
        for ds in all_datasets:
            dataset_id = ds.dataset_id
            dataset_lower = dataset_id.lower()
            matched_type = None
            for prefix, ds_type in prefix_map:
                if dataset_lower.startswith(prefix):
                    matched_type = ds_type
                    break
            if matched_type:
                categorized.setdefault(matched_type, []).append(dataset_id)
            elif full:
                unrecognized.append(dataset_id)

        ga4_hostnames = {}
        if "ga4" in categorized:
            ga4_hostnames = self.bq.get_ga4_dataset_hostnames()

        discovered = []
        for ds_type, dataset_ids in categorized.items():
            for dataset_id in dataset_ids:
                hostname = None
                if ds_type == "ga4":
                    hostname = ga4_hostnames.get(dataset_id)
                    if hostname and hostname.startswith("Error:"):
                        hostname = None
                elif ds_type == "gsc":
                    dataset_lower = dataset_id.lower()
                    if "_sc_domain_" in dataset_lower:
                        parts = dataset_id.split("_sc_domain_")
                        if len(parts) > 1:
                            hostname = parts[1].replace("_", ".").lower().replace("www.", "")
                    elif dataset_lower.startswith("jepto_gsc_"):
                        hostname = dataset_id[len("jepto_gsc_"):].replace("_", ".").lower()
                    elif dataset_lower.startswith("searchconsole_"):
                        hostname = dataset_id[len("searchconsole_"):].replace("_", ".").lower()
                discovered.append({"dataset": dataset_id, "dataset_type": ds_type, "hostname": hostname})

        if full:
            for dataset_id in unrecognized:
                discovered.append({"dataset": dataset_id, "dataset_type": "other", "hostname": None})

        # Upsert each discovered dataset into the Supabase catalog (hostname COALESCE).
        for ds_info in discovered:
            self.sb.execute(
                """
                INSERT INTO meta.dataset_catalog (dataset, dataset_type, hostname, active, updated_at)
                VALUES (%(dataset)s, %(dataset_type)s, %(hostname)s, TRUE, now())
                ON CONFLICT (dataset) DO UPDATE SET
                    dataset_type = excluded.dataset_type,
                    hostname = COALESCE(excluded.hostname, meta.dataset_catalog.hostname),
                    active = TRUE,
                    updated_at = now()
                """,
                ds_info,
            )

        # Remove catalog entries that no longer exist in BQ.
        all_bq_dataset_names = list({ds.dataset_id for ds in all_datasets})
        if full:
            self.sb.execute(
                "DELETE FROM meta.dataset_catalog WHERE dataset != ALL(%(datasets)s)",
                {"datasets": all_bq_dataset_names},
            )
        else:
            prefix_conditions = " OR ".join(
                [f"LOWER(dataset) LIKE '{p}%%'" for p, _ in prefix_map]
            )
            self.sb.execute(
                f"DELETE FROM meta.dataset_catalog "
                f"WHERE ({prefix_conditions}) AND dataset != ALL(%(datasets)s)",
                {"datasets": all_bq_dataset_names},
            )

        result = {}
        for ds_info in discovered:
            result.setdefault(ds_info["dataset_type"], []).append(ds_info)
        return result

    # ══════════════════════════════════════════════════════════════════════════
    # Data access (read from actual tables)
    # ══════════════════════════════════════════════════════════════════════════

    def get_client_data(
        self,
        client_id: str,
        table: str,
        dataset: str = "DataForSEO",
        limit: int = 1000,
        use_domain_lookup: bool = False,
    ) -> pd.DataFrame:
        """
        Pull data for a client from a specific table.

        By default, uses job_id lookup through the upload log. For domain-based
        tables (ranked_keywords, backlinks), can optionally use domain lookup
        through Meta tables.

        .. warning::
            The default (``use_domain_lookup=False``) path filters
            ``Logs.upload_events`` by ``client_id``, which is not reliably
            populated today — results will miss uploads where ``client_id`` is
            null. For the tables in ``DOMAIN_TABLES``, prefer
            ``use_domain_lookup=True`` (goes through ``Meta.client_domains``,
            which is reliable). For other tables, results are lower-bound only.
            See KNOWN_ISSUES.md.

        Args:
            client_id: The client identifier
            table: Table name (e.g., 'dataforseo_labs-google-ranked_keywords')
            dataset: Dataset name (default 'DataForSEO')
            limit: Maximum rows to return
            use_domain_lookup: If True and table is domain-based, lookup by domain
                               instead of job_id. Default False (use job_id).

        Returns:
            DataFrame with data for the client
        """
        # Check if we should use domain lookup
        if use_domain_lookup and table in self.DOMAIN_TABLES:
            # Resolve the client's (non-competitor) domains from Supabase, then
            # filter the BQ data table by that domain list. Meta no longer lives
            # in BQ, so this can't be a single cross-dataset subquery anymore.
            domains_df = self.get_client_domains(int(client_id), is_competitor=False)
            domain_list = domains_df["domain"].tolist() if not domains_df.empty else []
            params = [
                bigquery.ArrayQueryParameter("domains", "STRING", domain_list),
                bigquery.ScalarQueryParameter("limit_val", "INT64", limit),
            ]
            query = f"""
                SELECT d.*
                FROM `{self._project_id}.{dataset}.{table}` d
                WHERE d.domain IN UNNEST(@domains)
                LIMIT @limit_val
            """
        else:
            # Job_id-based lookup through upload log (default)
            params = [
                bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
                bigquery.ScalarQueryParameter("table_name", "STRING", table),
                bigquery.ScalarQueryParameter("limit_val", "INT64", limit),
            ]
            query = f"""
                SELECT d.*
                FROM `{self._project_id}.{dataset}.{table}` d
                WHERE d.job_id IN (
                    SELECT le.job_id
                    FROM `{self._project_id}.Logs.upload_events` le
                    WHERE le.client_id = @client_id
                    AND le.`table` = @table_name
                )
                LIMIT @limit_val
            """

        job_config = bigquery.QueryJobConfig(query_parameters=params)
        return self.bq.client.query(query, job_config=job_config).result().to_dataframe()

    def get_project_data(
        self,
        project_id: str,
        table: str,
        dataset: str = "DataForSEO",
        limit: int = 1000,
    ) -> pd.DataFrame:
        """
        Pull data for a project from a specific table.

        Uses job_id lookup through ``Logs.upload_events`` filtered by
        ``project_id``.

        .. warning::
            ``project_id`` is not reliably populated on ``Logs.upload_events``
            today — results will miss uploads where ``project_id`` is null.
            Results are lower-bound only. See KNOWN_ISSUES.md.

            The previous ``use_domain_lookup=True`` branch has been removed
            because it referenced ``Meta.company_domains`` and
            ``Meta.project_companies``, neither of which exist in BigQuery.
            The ``role`` filter was also removed with that branch.

        Args:
            project_id: The project identifier
            table: Table name
            dataset: Dataset name (default 'DataForSEO')
            limit: Maximum rows to return

        Returns:
            DataFrame with data for the project
        """
        params = [
            bigquery.ScalarQueryParameter("project_id", "STRING", project_id),
            bigquery.ScalarQueryParameter("table_name", "STRING", table),
            bigquery.ScalarQueryParameter("limit_val", "INT64", limit),
        ]

        query = f"""
            SELECT d.*
            FROM `{self._project_id}.{dataset}.{table}` d
            WHERE d.job_id IN (
                SELECT le.job_id
                FROM `{self._project_id}.Logs.upload_events` le
                WHERE le.project_id = @project_id
                AND le.`table` = @table_name
            )
            LIMIT @limit_val
        """

        job_config = bigquery.QueryJobConfig(query_parameters=params)
        return self.bq.client.query(query, job_config=job_config).result().to_dataframe()

    def get_available_datasets(self, client_id: Optional[str] = None) -> List[str]:
        """
        Get distinct datasets from the upload log.

        .. warning::
            The ``client_id`` filter is best-effort. That field is not reliably
            populated on ``Logs.upload_events``, so filtering will silently miss
            datasets whose uploads have a null ``client_id``. Call without the
            filter for a complete list. See KNOWN_ISSUES.md.

        Args:
            client_id: Optional filter by client (unreliable — see warning above)

        Returns:
            List of dataset names that have uploads
        """
        params = []
        where_clause = ""

        if client_id is not None:
            where_clause = "WHERE client_id = @client_id"
            params.append(bigquery.ScalarQueryParameter("client_id", "STRING", client_id))

        query = f"""
            SELECT DISTINCT dataset
            FROM `{self._project_id}.Logs.upload_events`
            {where_clause}
            ORDER BY dataset
        """

        job_config = bigquery.QueryJobConfig(query_parameters=params) if params else None
        result = self.bq.client.query(query, job_config=job_config).result()
        return [row.dataset for row in result if row.dataset]

    def get_available_tables(
        self,
        dataset: Optional[str] = None,
        client_id: Optional[str] = None,
    ) -> List[str]:
        """
        Get distinct tables from the upload log.

        .. warning::
            The ``client_id`` filter is best-effort. That field is not reliably
            populated on ``Logs.upload_events``, so filtering will silently miss
            tables whose uploads have a null ``client_id``. Call without the
            filter for a complete list. See KNOWN_ISSUES.md.

        Args:
            dataset: Optional filter by dataset
            client_id: Optional filter by client (unreliable — see warning above)

        Returns:
            List of table names that have uploads
        """
        params = []
        conditions = []

        if dataset is not None:
            conditions.append("dataset = @dataset")
            params.append(bigquery.ScalarQueryParameter("dataset", "STRING", dataset))

        if client_id is not None:
            conditions.append("client_id = @client_id")
            params.append(bigquery.ScalarQueryParameter("client_id", "STRING", client_id))

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"""
            SELECT DISTINCT `table`
            FROM `{self._project_id}.Logs.upload_events`
            {where_clause}
            ORDER BY `table`
        """

        job_config = bigquery.QueryJobConfig(query_parameters=params) if params else None
        result = self.bq.client.query(query, job_config=job_config).result()
        return [row.table for row in result if row.table]

    def get_ga4_datasets(self) -> pd.DataFrame:
        """
        Get GA4 datasets with their hostnames.

        Returns:
            DataFrame with columns: dataset_id, hostname
        """
        ga4_dict = self.bq.get_ga4_dataset_hostnames()
        if not ga4_dict:
            return pd.DataFrame(columns=["dataset_id", "hostname"])

        return pd.DataFrame([
            {"dataset_id": k, "hostname": v}
            for k, v in ga4_dict.items()
        ])

    def get_gsc_datasets(self) -> List[str]:
        """
        Get Google Search Console dataset IDs.

        Returns:
            List of GSC dataset names
        """
        return self.bq.get_gsc_dataset_hostnames()
