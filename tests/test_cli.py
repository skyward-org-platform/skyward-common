"""Tests for the skyward CLI."""
import json
from unittest.mock import patch, MagicMock

import pandas as pd
from click.testing import CliRunner

from skyward.cli import cli


@patch("skyward.cli._get_hub")
def test_list_clients_json(mock_get_hub):
    hub = MagicMock()
    mock_get_hub.return_value = hub
    hub.list_clients.return_value = pd.DataFrame([
        {"client_id": 1, "name": "Acme Corp", "is_active": True},
        {"client_id": 2, "name": "Globex", "is_active": True},
    ])
    runner = CliRunner()
    result = runner.invoke(cli, ["meta", "list-clients", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2
    assert data[0]["name"] == "Acme Corp"


def test_cli_has_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_meta_group_exists():
    runner = CliRunner()
    result = runner.invoke(cli, ["meta", "--help"])
    assert result.exit_code == 0
    assert "list-clients" in result.output


@patch("skyward.cli._get_hub")
def test_list_clients_with_search(mock_get_hub):
    hub = MagicMock()
    mock_get_hub.return_value = hub
    hub.list_clients.return_value = pd.DataFrame([
        {"client_id": 1, "name": "Acme Corp", "is_active": True},
    ])
    runner = CliRunner()
    result = runner.invoke(cli, ["meta", "list-clients", "--search", "acme", "--format", "json"])
    assert result.exit_code == 0
    hub.list_clients.assert_called_once_with(search="acme", include_counts=False)


@patch("skyward.cli._get_hub")
def test_list_clients_with_counts(mock_get_hub):
    hub = MagicMock()
    mock_get_hub.return_value = hub
    hub.list_clients.return_value = pd.DataFrame([
        {"client_id": 1, "name": "Acme Corp", "is_active": True, "domain_count": 3},
    ])
    runner = CliRunner()
    result = runner.invoke(cli, ["meta", "list-clients", "--counts", "--format", "json"])
    assert result.exit_code == 0
    hub.list_clients.assert_called_once_with(search=None, include_counts=True)


@patch("skyward.cli._get_hub")
def test_add_client(mock_get_hub):
    hub = MagicMock()
    mock_get_hub.return_value = hub
    hub.add_client.return_value = 5
    runner = CliRunner()
    result = runner.invoke(cli, ["meta", "add-client", "--name", "New Corp"])
    assert result.exit_code == 0
    hub.add_client.assert_called_once_with(name="New Corp", abbreviation=None)
    assert "5" in result.output


@patch("skyward.cli._get_hub")
def test_deactivate_client_with_cascade(mock_get_hub):
    hub = MagicMock()
    mock_get_hub.return_value = hub
    runner = CliRunner()
    result = runner.invoke(cli, ["meta", "deactivate-client", "--id", "3", "--cascade"])
    assert result.exit_code == 0
    hub.deactivate_client.assert_called_once_with(client_id=3, cascade=True)


@patch("skyward.cli._get_hub")
def test_list_domains(mock_get_hub):
    hub = MagicMock()
    mock_get_hub.return_value = hub
    hub.get_client_domains.return_value = pd.DataFrame([
        {"domain_id": 1, "domain": "acme.com", "is_competitor": False},
    ])
    runner = CliRunner()
    result = runner.invoke(cli, ["meta", "list-domains", "--client-id", "1", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data[0]["domain"] == "acme.com"
    hub.get_client_domains.assert_called_once_with(client_id=1, is_competitor=None)


@patch("skyward.cli._get_hub")
def test_add_domains(mock_get_hub):
    hub = MagicMock()
    mock_get_hub.return_value = hub
    hub.add_domains.return_value = [{"domain_id": 10, "domain": "new.com"}]
    runner = CliRunner()
    result = runner.invoke(cli, ["meta", "add-domains", "--client-id", "1", "--domains", "new.com,other.com"])
    assert result.exit_code == 0
    hub.add_domains.assert_called_once_with(
        domains=["new.com", "other.com"], client_id=1, is_competitor=False, priority="NORMAL"
    )


@patch("skyward.cli._get_hub")
def test_list_projects(mock_get_hub):
    hub = MagicMock()
    mock_get_hub.return_value = hub
    hub.list_projects.return_value = pd.DataFrame([
        {"project_id": 1, "name": "SEO Audit", "client_id": 1},
    ])
    runner = CliRunner()
    result = runner.invoke(cli, ["meta", "list-projects", "--client-id", "1", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data[0]["name"] == "SEO Audit"


@patch("skyward.cli._get_hub")
def test_list_datasets(mock_get_hub):
    hub = MagicMock()
    mock_get_hub.return_value = hub
    hub.get_client_datasets.return_value = pd.DataFrame([
        {"dataset_id": "analytics_123", "dataset_type": "ga4", "client_id": 1},
    ])
    runner = CliRunner()
    result = runner.invoke(cli, ["meta", "list-datasets", "--client-id", "1", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data[0]["dataset_type"] == "ga4"


def test_llm_cost():
    runner = CliRunner()
    result = runner.invoke(cli, ["llm", "cost", "--provider", "openai", "--model", "gpt-4o", "--input", "1000", "--output", "500"])
    assert result.exit_code == 0
    assert "$" in result.output


def test_llm_estimate():
    runner = CliRunner()
    result = runner.invoke(cli, [
        "llm", "estimate",
        "--provider", "openai", "--model", "gpt-4o",
        "--items", "100", "--input-per", "2000", "--output-per", "500",
    ])
    assert result.exit_code == 0
    assert "$" in result.output


@patch("skyward.cli._get_hub")
def test_bq_search_uploads(mock_get_hub):
    hub = MagicMock()
    mock_get_hub.return_value = hub
    hub.search_uploads.return_value = pd.DataFrame([
        {"job_id": "j1", "table": "backlinks", "row_count": 100},
    ])
    runner = CliRunner()
    result = runner.invoke(cli, ["bq", "search-uploads", "--client-id", "1", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data[0]["job_id"] == "j1"


@patch("skyward.cli.load_config")
@patch("skyward.cli.BigQueryClient")
@patch("skyward.cli.DataHub")
def test_get_hub_bootstraps_from_config(mock_datahub, mock_bq_cls, mock_load_config):
    from skyward.cli import _get_hub
    mock_cfg = MagicMock()
    mock_cfg.datahub_credentials = {"key": "val"}
    mock_cfg.datahub_project_id = "my-project"
    mock_load_config.return_value = mock_cfg
    hub = _get_hub()
    mock_bq_cls.assert_called_once_with(credentials_info={"key": "val"}, project_id="my-project")
    mock_datahub.assert_called_once_with(mock_bq_cls.return_value)
    assert hub == mock_datahub.return_value



def test_get_hub_does_lazy_import():
    """_get_hub populates module-level names via lazy import when they are None."""
    import skyward.cli as cli_module
    # Save originals and reset to None
    orig_lc, orig_bq, orig_dh = cli_module.load_config, cli_module.BigQueryClient, cli_module.DataHub
    cli_module.load_config = None
    cli_module.BigQueryClient = None
    cli_module.DataHub = None
    try:
        cli_module._get_hub()
    except Exception:
        pass  # Will fail without .env, but imports should have run
    # After the call, the lazy import should have populated the names
    assert cli_module.load_config is not None
    assert cli_module.BigQueryClient is not None
    assert cli_module.DataHub is not None
    # Restore
    cli_module.load_config, cli_module.BigQueryClient, cli_module.DataHub = orig_lc, orig_bq, orig_dh


@patch("skyward.cli._get_hub")
def test_list_clients_table(mock_get_hub):
    hub = MagicMock()
    mock_get_hub.return_value = hub
    hub.list_clients.return_value = pd.DataFrame([
        {"client_id": 1, "name": "Acme Corp", "is_active": True},
    ])
    runner = CliRunner()
    result = runner.invoke(cli, ["meta", "list-clients"])
    assert result.exit_code == 0
    assert "Acme Corp" in result.output
