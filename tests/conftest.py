# tests/conftest.py
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

# Register the ephemeral-Postgres fixtures (pg_client, _pg_schema) so
# Supabase-backed tests can use them. They skip when TEST_DATABASE_URL is unset.
pytest_plugins = ["tests.conftest_pg"]


class FakeLoadJob:
    """Mimics a BQ load job."""

    def result(self):
        return self


class FakeQueryResult:
    """Wraps a DataFrame to mimic BQ query result."""

    def __init__(self, df: pd.DataFrame):
        self._df = df

    def result(self):
        return self

    def to_dataframe(self):
        return self._df


class FakeBQClient:
    """Minimal fake for google.cloud.bigquery.Client."""

    def __init__(self):
        self.project = "data-hub-468216"
        self.queries = []  # Track executed queries
        self._next_result = pd.DataFrame()
        self._results_queue = []
        self.loaded_tables = []

    def set_next_result(self, df: pd.DataFrame):
        """Set the DataFrame returned by the next query."""
        self._next_result = df

    def queue_result(self, df: pd.DataFrame):
        """Queue a result for a future query call."""
        self._results_queue.append(df)

    def query(self, sql, job_config=None):
        self.queries.append({"sql": sql, "job_config": job_config})
        if self._results_queue:
            result_df = self._results_queue.pop(0)
        elif self._next_result is not None and not self._next_result.empty:
            result_df = self._next_result
            self._next_result = pd.DataFrame()
        else:
            result_df = pd.DataFrame()
        return FakeQueryResult(result_df)

    def load_table_from_dataframe(self, df, table_ref, job_config=None):
        self.loaded_tables.append({"table_ref": table_ref, "df": df.copy(), "job_config": job_config})
        return FakeLoadJob()

    def get_table(self, table_ref):
        return SimpleNamespace(num_rows=0)


class FakeBigQueryClient:
    """Wraps FakeBQClient to mimic BigQueryClient interface."""

    def __init__(self):
        self.client = FakeBQClient()
        self.project_id = "data-hub-468216"


@pytest.fixture
def fake_bq():
    """Provide a FakeBigQueryClient."""
    return FakeBigQueryClient()


@pytest.fixture
def hub(pg_client, fake_bq):
    """Provide a DataHub: Supabase (pg_client) for entities, fake BQ for analytics."""
    from skyward.data.hub import DataHub
    return DataHub(pg_client, fake_bq)
