from __future__ import annotations

import pandas as pd
from datetime import datetime
from typing import Optional, List
from google.cloud import bigquery

from skyward.data.meta import MetaClient


class DataHub(MetaClient):
    """Central interface for querying data via Meta tables."""

    # Tables where data can be looked up by domain (via Meta.company_domains)
    # All other tables use job_id lookup (via Logs.upload_events)
    DOMAIN_TABLES = {
        "dataforseo_labs-google-ranked_keywords",
        "backlinks_backlinks_live",
        "serp_google_organic_live_advanced",
        "backlinks_bulk_pages_summary_live",
        "backlinks_summary_live",
    }

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

        Args:
            client_id: Filter by client
            project_id: Filter by project
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
        params = []
        conditions = []

        if dataset is not None:
            conditions.append("dataset = @dataset")
            params.append(bigquery.ScalarQueryParameter("dataset", "STRING", dataset))

        if active_only:
            conditions.append("is_active = TRUE")

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"""
            SELECT dataset, table_name, row_count, size_bytes,
                   is_active, status_changed_at, notes, last_indexed_at
            FROM `{self._project_id}.Meta.table_catalog`
            {where_clause}
            ORDER BY dataset, table_name
        """

        job_config = bigquery.QueryJobConfig(query_parameters=params) if params else None
        return self.bq.client.query(query, job_config=job_config).result().to_dataframe()

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

        # 1. Read current catalog state for computing the diff summary
        catalog_query = f"""
            SELECT table_name, is_active
            FROM `{project}.Meta.table_catalog`
            WHERE dataset = @dataset
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("dataset", "STRING", dataset)
            ]
        )
        catalog = self.bq.client.query(catalog_query, job_config=job_config).result().to_dataframe()
        catalog_table_names = set(catalog["table_name"]) if not catalog.empty else set()
        catalog_active = set(catalog[catalog["is_active"] == True]["table_name"]) if not catalog.empty else set()
        catalog_inactive = set(catalog[catalog["is_active"] == False]["table_name"]) if not catalog.empty else set()

        # 2. Get current BQ tables for the diff summary
        bq_names_query = f"""
            SELECT table_name
            FROM `{project}.{dataset}.INFORMATION_SCHEMA.TABLES`
            WHERE table_type = 'BASE TABLE'
              AND NOT STARTS_WITH(table_name, 'temp_')
              AND NOT STARTS_WITH(table_name, '_temp_')
        """
        bq_names_df = self.bq.client.query(bq_names_query).result().to_dataframe()
        bq_table_names = set(bq_names_df["table_name"])

        # 3. Compute diff for summary
        new_tables = bq_table_names - catalog_table_names
        missing_tables = catalog_active - bq_table_names
        reappearing_tables = catalog_inactive & bq_table_names
        existing_tables = catalog_active & bq_table_names

        # 4. Single MERGE handles all inserts, updates, and deactivations.
        merge_query = f"""
            MERGE `{project}.Meta.table_catalog` T
            USING (
                SELECT
                    '{dataset}' AS dataset,
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
            ) S
            ON T.dataset = S.dataset AND T.table_name = S.table_name
            WHEN MATCHED THEN UPDATE SET
                T.row_count = S.row_count,
                T.size_bytes = S.size_bytes,
                T.is_active = TRUE,
                T.status_changed_at = CASE
                    WHEN T.is_active = FALSE THEN CURRENT_TIMESTAMP()
                    ELSE T.status_changed_at
                END,
                T.last_indexed_at = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED BY TARGET THEN INSERT
                (dataset, table_name, row_count, size_bytes, is_active,
                 status_changed_at, notes, last_indexed_at)
                VALUES (S.dataset, S.table_name, S.row_count, S.size_bytes,
                        TRUE, NULL, NULL, CURRENT_TIMESTAMP())
            WHEN NOT MATCHED BY SOURCE AND T.dataset = '{dataset}' THEN UPDATE SET
                T.is_active = FALSE,
                T.status_changed_at = CASE
                    WHEN T.is_active = TRUE THEN CURRENT_TIMESTAMP()
                    ELSE T.status_changed_at
                END,
                T.last_indexed_at = CURRENT_TIMESTAMP()
        """
        self.bq.client.query(merge_query).result()

        return {
            "dataset": dataset,
            "new_tables": sorted(new_tables),
            "deactivated_tables": sorted(missing_tables),
            "reactivated_tables": sorted(reappearing_tables),
            "updated_tables": sorted(existing_tables),
            "total_active": len(bq_table_names),
        }

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
        params = [
            bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
            bigquery.ScalarQueryParameter("table_name", "STRING", table),
            bigquery.ScalarQueryParameter("limit_val", "INT64", limit),
        ]

        # Check if we should use domain lookup
        if use_domain_lookup and table in self.DOMAIN_TABLES:
            # Domain-based lookup through Meta tables
            query = f"""
                SELECT d.*
                FROM `{self._project_id}.{dataset}.{table}` d
                WHERE d.domain IN (
                    SELECT d2.domain FROM `{self._project_id}.Meta.domains` d2
                    JOIN `{self._project_id}.Meta.client_domains` cd ON d2.domain_id = cd.domain_id
                    WHERE cd.client_id = @client_id AND cd.is_competitor = FALSE
                )
                LIMIT @limit_val
            """
        else:
            # Job_id-based lookup through upload log (default)
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
        role: Optional[str] = None,
        limit: int = 1000,
        use_domain_lookup: bool = False,
    ) -> pd.DataFrame:
        """
        Pull data for a project from a specific table.

        By default, uses job_id lookup through the upload log.

        Args:
            project_id: The project identifier
            table: Table name
            dataset: Dataset name (default 'DataForSEO')
            role: Optional filter ('client' or 'competitor')
            limit: Maximum rows to return
            use_domain_lookup: If True and table is domain-based, lookup by domain

        Returns:
            DataFrame with data for the project
        """
        params = [
            bigquery.ScalarQueryParameter("project_id", "STRING", project_id),
            bigquery.ScalarQueryParameter("table_name", "STRING", table),
            bigquery.ScalarQueryParameter("limit_val", "INT64", limit),
        ]

        role_filter = ""
        if role is not None:
            role_filter = "AND pc.role = @role"
            params.append(bigquery.ScalarQueryParameter("role", "STRING", role))

        if use_domain_lookup and table in self.DOMAIN_TABLES:
            # Domain-based lookup
            query = f"""
                SELECT d.*, pc.role
                FROM `{self._project_id}.{dataset}.{table}` d
                JOIN `{self._project_id}.Meta.company_domains` cd ON d.domain = cd.domain
                JOIN `{self._project_id}.Meta.project_companies` pc ON cd.company_id = pc.company_id
                WHERE pc.project_id = @project_id
                {role_filter}
                LIMIT @limit_val
            """
        else:
            # Job_id-based lookup (default)
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

        Args:
            client_id: Optional filter by client

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

        Args:
            dataset: Optional filter by dataset
            client_id: Optional filter by client

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
