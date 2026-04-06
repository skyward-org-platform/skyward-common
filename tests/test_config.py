"""Tests for skyward.config settings and load_config."""
import os
from unittest.mock import patch

from skyward.config import load_config


@patch.dict(os.environ, {
    "ENV": "TEST",
    "GCP_DATAHUB_PROJECT_ID": "test-project",
    "GCP_DATAHUB_CREDENTIALS": "",
    "GDRIVE_CREDENTIALS": "",
    "GDRIVE_OAUTH_TOKEN": "",
    "DATAFORSEO_API_LOGIN": "",
    "DATAFORSEO_API_PASSWORD": "",
    "OPENAI_API_KEY": "",
    "GEMINI_API_KEY": "",
}, clear=False)
def test_load_config_with_empty_credentials_returns_none():
    """When credential env vars are empty, credentials fields should be None (ADC mode)."""
    cfg = load_config()
    assert cfg.datahub_credentials is None
    assert cfg.datahub_project_id == "test-project"


@patch.dict(os.environ, {
    "ENV": "TEST",
    "GCP_DATAHUB_CREDENTIALS": "",
    "GDRIVE_CREDENTIALS": "",
    "GDRIVE_OAUTH_TOKEN": "",
    "DATAFORSEO_API_LOGIN": "",
    "DATAFORSEO_API_PASSWORD": "",
    "OPENAI_API_KEY": "",
    "GEMINI_API_KEY": "",
}, clear=True)
def test_load_config_defaults_project_id():
    """When GCP_DATAHUB_PROJECT_ID is not set, defaults to data-hub-468216."""
    cfg = load_config()
    assert cfg.datahub_project_id == "data-hub-468216"
