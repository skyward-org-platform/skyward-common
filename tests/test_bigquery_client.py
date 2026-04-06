"""Tests for BigQueryClient initialization and credential handling."""
from unittest.mock import patch, MagicMock

from skyward.data.bigquery import BigQueryClient


@patch("skyward.data.bigquery.client.bigquery.Client")
def test_adc_when_no_credentials_info(mock_bq_client):
    """BigQueryClient uses ADC when credentials_info is not provided."""
    client = BigQueryClient(project_id="my-project")
    mock_bq_client.assert_called_once_with(project="my-project")


@patch("skyward.data.bigquery.client.service_account.Credentials.from_service_account_info")
@patch("skyward.data.bigquery.client.bigquery.Client")
def test_explicit_credentials_when_provided(mock_bq_client, mock_from_sa):
    """BigQueryClient uses service account when credentials_info is provided."""
    creds_dict = {"type": "service_account", "project_id": "test"}
    mock_creds = MagicMock()
    mock_from_sa.return_value = mock_creds
    client = BigQueryClient(project_id="my-project", credentials_info=creds_dict)
    mock_from_sa.assert_called_once_with(creds_dict)
    mock_bq_client.assert_called_once_with(credentials=mock_creds, project="my-project")


def test_missing_project_id_raises():
    """BigQueryClient raises RuntimeError when project_id is missing."""
    try:
        BigQueryClient(project_id=None)
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "project_id" in str(e)


@patch("skyward.data.bigquery.client.bigquery.Client", side_effect=Exception("Could not automatically determine credentials"))
def test_adc_missing_gives_helpful_error(mock_bq_client):
    """When ADC is not configured, raise RuntimeError with setup instructions."""
    try:
        BigQueryClient(project_id="my-project")
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "gcloud auth application-default login" in str(e)
