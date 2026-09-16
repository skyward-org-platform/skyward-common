# tests/conftest.py
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

# Register the ephemeral-Postgres fixtures (pg_client, _pg_schema) so
# Supabase-backed tests can use them. They skip when TEST_DATABASE_URL is unset.
pytest_plugins = ["tests.conftest_pg"]


@pytest.fixture(autouse=True)
def _no_real_slack(monkeypatch):
    """Hard guard: NO test may ever post to Slack.

    `.env` is loaded into the process during tests, so `SLACK_WEBHOOK_*` can be live —
    a bare `Alerter()` (e.g. the one `run_forever` builds when no alerter is injected)
    would then post to the real channel. Strip those env vars so the Alerter defaults to
    disabled, and make the underlying sender raise if anything still tries to send.
    """
    for key in [k for k in os.environ if k.startswith("SLACK_WEBHOOK_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("DFS_COLLECTOR_ALERT_CHANNEL", raising=False)

    import skyward.notifications as _notif

    def _blocked(*_a, **_k):
        raise RuntimeError("send_slack invoked during tests — Slack must be stubbed/disabled")

    monkeypatch.setattr(_notif, "send_slack", _blocked)


@pytest.fixture(autouse=True)
def _no_real_dfs_balance(request, monkeypatch):
    """Unit tests never call DataForSEO for the balance guard. Live tests use the real API."""
    if "live" in request.keywords:
        return
    from skyward.data.dataforseo.client import DataForSEOClient

    monkeypatch.setattr(
        DataForSEOClient, "get_balance",
        lambda self: {"balance": 1_000_000.0, "total": 0.0, "raw": {"balance": 1_000_000.0}},
    )


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

    def to_dataframe(self, **kwargs):  # accept create_bqstorage_client= etc.
        return self._df


class FakeBQClient:
    """Minimal fake for google.cloud.bigquery.Client."""

    def __init__(self):
        self.project = "data-hub-468216"
        self.queries = []  # Track executed queries
        self._next_result = pd.DataFrame()
        self._results_queue = []
        self.loaded_tables = []
        self.inserted_rows = []   # streaming inserts: {"table": str, "rows": list}
        self.insert_errors = []   # queue of error payloads to return from insert_rows_json

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

    def insert_rows_json(self, table, rows, row_ids=None):
        if self.insert_errors:
            return self.insert_errors.pop(0)
        self.inserted_rows.append({
            "table": str(table),
            "rows": list(rows),
            "row_ids": list(row_ids) if row_ids is not None else None,
        })
        return []

    def get_table(self, table_ref):
        return SimpleNamespace(num_rows=0)


class FakeBigQueryClient:
    """Wraps FakeBQClient to mimic BigQueryClient interface."""

    def __init__(self):
        self.client = FakeBQClient()
        self.project_id = "data-hub-468216"
        # RunContext._after_save and BaseEndpoint.upload() both call log_upload_event()
        # after a successful save. Mocking it here (rather than per-test) means a test
        # that forgets to stub it gets a normal mock call, not a silently-swallowed
        # "Cost-log upload event failed" print that could mask a real regression.
        self.log_upload_event = MagicMock()


@pytest.fixture
def fake_bq():
    """Provide a FakeBigQueryClient."""
    return FakeBigQueryClient()


@pytest.fixture
def hub(pg_client, fake_bq):
    """Provide a DataHub: Supabase (pg_client) for entities, fake BQ for analytics."""
    from skyward.data.hub import DataHub
    return DataHub(pg_client, fake_bq)
