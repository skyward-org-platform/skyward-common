from typing import List, Tuple, Dict, Optional, Any
import pandas as pd
from datetime import datetime
from google.cloud import bigquery
from google.oauth2 import service_account
import skyward.functions as skyward_functions

DEFAULT_INPUT_PROMPT = "Enter number (or press Enter to abort): "

class BigQueryClient:
    """
    Thin wrapper around Google Cloud BigQuery for authentication and basic operations.
    Create once in your app, then pass to any function that needs it.
    """

    def __init__(self, credentials_info: str = None, project_id: str = None):
        self.project_id = project_id
        self.credentials = service_account.Credentials.from_service_account_info(credentials_info) if credentials_info else None

         # Validate credentials

        if not self.credentials or not self.project_id:
            raise RuntimeError("Missing BigQuery credentials.")

        # Initialize BigQuery client
        self.client: bigquery.Client = bigquery.Client(
            credentials=self.credentials,
            project=self.project_id
        )



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



    def select_dataset(self, filter_string: str = ""):
        # predefined datasets
        static_datasets = [
            "DataForSEO",
            "ScreamingFrog",
            "Logs"
        ]

        ga4_datasets = self.get_ga4_dataset_hostnames()
        ga4_datasets_formatted = []

        gsc_datasets = self.get_gsc_dataset_hostnames()

        for ga4_id, hostname in ga4_datasets.items():
            display_name = f"{ga4_id} ({hostname})"
            ga4_datasets_formatted.append(display_name)

        display_list = static_datasets + ga4_datasets_formatted + gsc_datasets
        display_list = [d for d in display_list if filter_string.lower() in d.lower()]

        # Step 1: List datasets
        print("Select Dataset:")
        print("\n".join([f"{i}: {d}" for i, d in enumerate(display_list)]))
        print()

        # input gathering loop
        while True:
            dataset_i = input(DEFAULT_INPUT_PROMPT).strip()
            if dataset_i == "": # abort if empty input
                print("Aborting.")
                return
            try:
                index = int(dataset_i)
                if 0 <= index < len(display_list):
                    break
                else:
                    raise ValueError
            except (ValueError):
                print("Invalid input.")

        dataset_id = display_list[int(dataset_i)]
        print(f"Selected Dataset: {dataset_id}\n")
        return dataset_id.split(" ")[0]  # return only the dataset ID portion



    def select_table(self, dataset_id: str):

        dataset_ref = self.client.dataset(dataset_id)
        tables = list(self.client.list_tables(dataset_ref))

        print(f"Select Table in the {dataset_id} Dataset:")
        print("\n".join([f"{i}: {t.table_id}" for i, t in enumerate(tables)]))
        print()

        # input gathering
        while True:
            table_i = input(DEFAULT_INPUT_PROMPT).strip()
            if table_i == "": # abort if empty input
                print("Aborting.")
                return
            try:
                index = int(table_i)
                if 0 <= index < len(tables):
                    break
                else:
                    raise ValueError
            except (ValueError):
                print("Invalid input.")


        table_id = tables[int(table_i)].table_id
        print(f"Selected Table: {table_id}\n")
        return table_id




    def select_upload_id(self, dataset_id: str, table_id: str, identifier_cols: List[str] = [], order_by_cols: Dict[str, str] = {}):

        # --- Step 0: Determine which columns to query and display ---

        # Mandatory column for selection
        included_identifiers = ['upload_id', 'job_id']

        if identifier_cols:
            try:
                # Retrieve schema to verify the optional column exists (zero-cost metadata call)
                table = self.client.get_table(f"{self.project_id}.{dataset_id}.{table_id}")
                existing_columns = {field.name for field in table.schema}

                # Iterate through the list of requested identifier columns
                for col_name in identifier_cols:
                    if col_name in existing_columns:
                        # SUCCESS: Column exists, append the string to the list
                        included_identifiers.append(col_name)
                    else:
                        # WARNING: Column does not exist
                        print(f"WARNING: Identifier column '{col_name}' not found in the table schema. Skipping it.")

            except Exception as e:
                print(f"ERROR: Failed to retrieve table schema. Details: {e}")
                return None

        # Create the SQL SELECT clause string
        select_cols_str = ", ".join(included_identifiers)

        # --- Step 1: List unique identifiers in the selected table ---
        # --- NEW: Build the ORDER BY clause ---
        order_by_parts = []

        # Iterate through the dictionary: key=column name, value=direction (ASC/DESC)
        for col, direction in order_by_cols.items():
            # Sanitize the direction input (optional but recommended)
            direction = direction.upper().strip()

            # Check if the column exists in the final selected list to prevent BQ error
            if col in included_identifiers:
                order_by_parts.append(f"{col} {direction}")
            else:
                print(f"WARNING: Order-by column '{col}' skipped as it was not included in the SELECT list.")

        # Fallback: Always order by the primary selector if no valid order_by_cols were provided
        if not order_by_parts:
            order_by_parts.append("upload_id DESC")

        order_by_str = ", ".join(order_by_parts)

        # Use SELECT DISTINCT to get unique combinations of upload_id and the identifier
        upload_id_query = f"""
            SELECT {select_cols_str}, COUNT(*) AS row_count
            FROM `{self.project_id}.{dataset_id}.{table_id}`
            GROUP BY {select_cols_str}
            ORDER BY {order_by_str}
        """

        upload_id_results = self.client.query(upload_id_query).result().to_dataframe(create_bqstorage_client=True)

        if upload_id_results.empty:
            print(f"No upload_ids found in {dataset_id}.{table_id}.")
            return None

        print(f"Select Upload ID in the {dataset_id}.{table_id} Table:")
        skyward_functions.display_scrollable_df(upload_id_results, height_px="300px")
        print()

        # --- input gathering loop ---
        while True:
            upload_i = input(DEFAULT_INPUT_PROMPT).strip()
            if upload_i == "": # abort if empty input
                print("Aborting.")
                return
            try:
                index = int(upload_i)
                # Check bounds against the DataFrame length
                if 0 <= index < len(upload_id_results):
                    break
                else:
                    raise ValueError
            except (ValueError):
                print("Invalid input.")

        # Extract the selected upload_id string using the validated index
        upload_id = upload_id_results.iloc[index]['upload_id']
        print(f"Selected Upload ID: {upload_id}\n")
        return upload_id


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

    def get_client_domains(self, client_id: str) -> List[str]:
        """
        Get all domains associated with a client.

        Args:
            client_id: The client identifier

        Returns:
            List of domain strings
        """
        query = """
            SELECT cd.domain
            FROM `data-hub-468216.Meta.companies` c
            JOIN `data-hub-468216.Meta.company_domains` cd ON c.company_id = cd.company_id
            WHERE c.client_id = @client_id
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("client_id", "STRING", client_id)
            ]
        )
        result = self.client.query(query, job_config=job_config).result()
        return [row.domain for row in result]

    def get_project_domains(
        self,
        project_id: str,
        role: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Get domains linked to a project with optional role filter.

        Args:
            project_id: The project identifier
            role: Optional filter ("client" or "competitor")

        Returns:
            DataFrame with columns: company_id, domain, role, is_primary
        """
        params = [
            bigquery.ScalarQueryParameter("project_id", "STRING", project_id)
        ]

        role_filter = ""
        if role is not None:
            role_filter = "AND pc.role = @role"
            params.append(bigquery.ScalarQueryParameter("role", "STRING", role))

        query = f"""
            SELECT pc.company_id, cd.domain, pc.role, cd.is_primary
            FROM `data-hub-468216.Meta.project_companies` pc
            JOIN `data-hub-468216.Meta.company_domains` cd ON pc.company_id = cd.company_id
            WHERE pc.project_id = @project_id
            {role_filter}
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
