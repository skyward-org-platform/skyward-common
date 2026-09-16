import pandas as pd
import pytest

from skyward.data.dataforseo import DataForSEOClient
from tests.conftest import FakeBigQueryClient


def _client(bq):
    return DataForSEOClient(username="u", password="p", bq_client=bq)


def test_get_job_cost_by_endpoint():
    bq = FakeBigQueryClient()
    bq.client.queue_result(pd.DataFrame([{"endpoint": "e", "endpoint_mode": "live",
                                          "total_cost_usd": 0.5}]))
    df = _client(bq).get_job_cost("job-1")
    assert df["total_cost_usd"].iloc[0] == 0.5
    q = bq.client.queries[0]
    assert "DataForSEO.job_costs" in q["sql"]
    assert "GROUP BY endpoint, endpoint_mode" in q["sql"]
    assert q["job_config"].query_parameters[0].value == "job-1"


def test_get_job_cost_total_and_upload_id():
    bq = FakeBigQueryClient()
    client = _client(bq)
    client.get_job_cost("j", by="total")
    client.get_job_cost("j", by="upload_id")
    assert "GROUP BY" not in bq.client.queries[0]["sql"]
    assert "GROUP BY endpoint, endpoint_mode, upload_id" in bq.client.queries[1]["sql"]


def test_get_job_cost_validates():
    with pytest.raises(ValueError):
        _client(FakeBigQueryClient()).get_job_cost("j", by="day")
    with pytest.raises(RuntimeError):
        _client(None).get_job_cost("j")


def test_get_job_progress_filters_endpoint():
    bq = FakeBigQueryClient()
    _client(bq).get_job_progress("j", endpoint="serp_google_organic")
    q = bq.client.queries[0]
    assert "DataForSEO.job_progress" in q["sql"]
    assert "endpoint = @endpoint" in q["sql"]
    assert [p.name for p in q["job_config"].query_parameters] == ["job_id", "endpoint"]
