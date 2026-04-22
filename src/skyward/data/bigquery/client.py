from typing import List, Tuple, Dict, Optional, Any
import pandas as pd
from datetime import datetime
from google.cloud import bigquery
from google.oauth2 import service_account
import skyward.functions as skyward_functions

class BigQueryClient:
    """
    Thin wrapper around Google Cloud BigQuery for authentication and basic operations.
    Create once in your app, then pass to any function that needs it.
    """

    def __init__(self, project_id: str = None, credentials_info: dict = None):
        if not project_id:
            raise RuntimeError("Missing BigQuery project_id.")

        self.project_id = project_id

        if credentials_info:
            self.credentials = service_account.Credentials.from_service_account_info(credentials_info)
            self.client = bigquery.Client(credentials=self.credentials, project=self.project_id)
        else:
            # Use Application Default Credentials (ADC)
            try:
                self.client = bigquery.Client(project=self.project_id)
            except Exception as e:
                raise RuntimeError(
                    "No Google Cloud credentials found. To authenticate, run:\n\n"
                    "    gcloud auth application-default login\n\n"
                    "Or set GCP_DATAHUB_CREDENTIALS in your .env to a service account JSON path."
                ) from e



# ======================================================================================== #

    # log an event that got uploaded into the database
    def log_upload_event(
        self,
        job_id,
        upload_id,
        source,
        source_program,
        dataset,
        table,
        row_count,
        timestamp,
        client_id=None,
        project_id=None,
        notes=None,
    ):
        """
        Logs an upload event into the BigQuery 'upload_events' table.

        Args:
            job_id (str): Unique identifier for the processing job.
            upload_id (str): Unique identifier for the upload event.
            source (str): Name or type of the data source.
            source_program (str): Program or tool that generated the data.
            dataset (str): Dataset name within the source.
            table (str): Table name within the source or dataset.
            row_count (int): Number of rows uploaded in the event.
            timestamp (datetime): Datetime object indicating when the data was ingested.
            client_id (str, optional): Client identifier from Meta.clients.
            project_id (str, optional): Project identifier from Meta.projects.
            notes (str, optional): Free-form notes about this upload.

        Returns:
            None
        """

        try:
            # Fully-qualified table ID in BigQuery
            log_table = "data-hub-468216.Logs.upload_events"

            # Prepare the row(s) to insert — must match table schema exactly
            log_entry = [{
                "job_id": job_id,                         # REQUIRED field
                "upload_id": upload_id,                   # REQUIRED field
                "source": source,                         # Optional field
                "source_program": source_program,         # Optional field
                "dataset": dataset,                       # Optional field
                "table": table,                           # Optional field
                "row_count": row_count,                   # Optional field (integer)
                "ingest_timestamp": timestamp.isoformat(), # REQUIRED field — store as RFC3339 string
                "client_id": client_id,                   # Optional field (new)
                "project_id": project_id,                 # Optional field (new)
                "notes": notes,                           # Optional field (new)
            }]

            # Insert rows into BigQuery via streaming API
            errors = self.client.insert_rows_json(log_table, log_entry)

            # If the API returns any errors, raise them to make failures visible
            if errors:
                raise RuntimeError(errors)

        except Exception as e:
            # Catch any exception and print a failure message
            print(f"Log failed: {e}")


    def get_ga4_dataset_hostnames(self):
        datasets = self.client.list_datasets()

        dataset_hostnames = {}

        for ds in datasets:
            dataset_id = ds.dataset_id
            if not dataset_id.startswith("analytics_"):
                continue  # skip non-GA4 datasets

            query = f"""
            SELECT device.web_info.hostname AS hostname
            FROM `{self.client.project}.{dataset_id}.events_*`
            WHERE device.web_info.hostname IS NOT NULL
            LIMIT 1
            """

            try:
                results = list(self.client.query(query).result())
                if results:
                    dataset_hostnames[dataset_id] = results[0].hostname
                else:
                    dataset_hostnames[dataset_id] = None
            except Exception as e:
                dataset_hostnames[dataset_id] = f"Error: {e}"

        return dataset_hostnames

    def get_gsc_dataset_hostnames(self):

        gsc_datasets = []
        gsc_prefixes = ("jepto", "searchconsole")

        all_datasets = self.client.list_datasets()

        for dataset in all_datasets:
            dataset_id = dataset.dataset_id.lower()

            # Check if the dataset starts with any of the GSC prefixes
            if dataset_id.startswith(gsc_prefixes):
                gsc_datasets.append(dataset.dataset_id)

        return gsc_datasets


# ======================================================================================== #
# Meta & Upload Logging Helper Methods
# ======================================================================================== #

    def search_uploads(
        self,
        client_id: Optional[str] = None,
        project_id: Optional[str] = None,
        job_id: Optional[str] = None,
        table: Optional[str] = None,
        dataset: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> pd.DataFrame:
        """
        Search upload events with optional filters.

        Args:
            client_id: Filter by client_id
            project_id: Filter by project_id
            job_id: Filter by job_id
            table: Filter by table name
            dataset: Filter by dataset name
            since: Filter events after this timestamp
            limit: Maximum rows to return (default 100)

        Returns:
            DataFrame of matching upload_events ordered by ingest_timestamp DESC
        """
        conditions = []
        params = []

        if client_id is not None:
            conditions.append("client_id = @client_id")
            params.append(bigquery.ScalarQueryParameter("client_id", "STRING", client_id))
        if project_id is not None:
            conditions.append("project_id = @project_id")
            params.append(bigquery.ScalarQueryParameter("project_id", "STRING", project_id))
        if job_id is not None:
            conditions.append("job_id = @job_id")
            params.append(bigquery.ScalarQueryParameter("job_id", "STRING", job_id))
        if table is not None:
            conditions.append("`table` = @table")
            params.append(bigquery.ScalarQueryParameter("table", "STRING", table))
        if dataset is not None:
            conditions.append("dataset = @dataset")
            params.append(bigquery.ScalarQueryParameter("dataset", "STRING", dataset))
        if since is not None:
            conditions.append("ingest_timestamp >= @since")
            params.append(bigquery.ScalarQueryParameter("since", "TIMESTAMP", since))

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(bigquery.ScalarQueryParameter("limit_val", "INT64", limit))

        query = f"""
            SELECT *
            FROM `data-hub-468216.Logs.upload_events`
            {where_clause}
            ORDER BY ingest_timestamp DESC
            LIMIT @limit_val
        """

        job_config = bigquery.QueryJobConfig(query_parameters=params)
        return self.client.query(query, job_config=job_config).result().to_dataframe()

    def get_client_job_ids(
        self,
        client_id: str,
        dataset: Optional[str] = None,
        table: Optional[str] = None,
    ) -> List[str]:
        """
        Get all job_ids for a client with optional dataset/table filters.

        Args:
            client_id: The client identifier
            dataset: Optional dataset filter
            table: Optional table filter

        Returns:
            List of job_id strings
        """
        params = [
            bigquery.ScalarQueryParameter("client_id", "STRING", client_id)
        ]
        conditions = ["client_id = @client_id"]

        if dataset is not None:
            conditions.append("dataset = @dataset")
            params.append(bigquery.ScalarQueryParameter("dataset", "STRING", dataset))
        if table is not None:
            conditions.append("`table` = @table")
            params.append(bigquery.ScalarQueryParameter("table", "STRING", table))

        where_clause = " AND ".join(conditions)

        query = f"""
            SELECT DISTINCT job_id
            FROM `data-hub-468216.Logs.upload_events`
            WHERE {where_clause}
            ORDER BY job_id
        """

        job_config = bigquery.QueryJobConfig(query_parameters=params)
        result = self.client.query(query, job_config=job_config).result()
        return [row.job_id for row in result]
