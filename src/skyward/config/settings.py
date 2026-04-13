# skyward/config/settings.py
"""
Central configuration loader for Skyward projects.

Credentials come from environment variables. For local dev, a .env file
is loaded automatically if found (searching cwd and parents via python-dotenv).
GCP auth uses Application Default Credentials by default.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import json
import warnings
from dotenv import load_dotenv


@dataclass
class Settings:

    # ENV
    ENV: str

    # DB
    datahub_project_id: str
    datahub_credentials: dict | None

    # Data For SEO
    dataforseo_username: str
    dataforseo_password: str

    # Google Drive
    gdrive_credentials: dict | None
    gdrive_oauth_token_path: Path | None

    # OpenAI Key
    openai_key: str

    # Gemini Key
    gemini_key: str

    # Perplexity Key
    perplexity_key: str

    # Anthropic Key
    anthropic_key: str

    # xAI (Grok) Key
    xai_key: str


def load_config() -> Settings:
    """Load configuration from environment variables.

    Automatically loads a .env file if found (python-dotenv searches cwd and parents).
    Does NOT change the working directory.
    """
    load_dotenv(override=False)

    def _load_json_credential(env_var: str) -> dict | None:
        """Load a JSON credential file from a path in an env var. Returns None for ADC."""
        raw = os.getenv(env_var, "").strip()
        if not raw:
            return None
        cred_path = Path(raw.replace("\\", "/")).expanduser()
        if not cred_path.is_absolute():
            cred_path = Path.cwd() / cred_path
        if not cred_path.is_file():
            warnings.warn(f"{env_var} path '{cred_path}' is not a file. Using ADC.")
            return None
        try:
            with cred_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            warnings.warn(f"Failed to load JSON from {cred_path}: {e}")
            return None

    gcp_datahub_credentials = _load_json_credential("GCP_DATAHUB_CREDENTIALS")
    gdrive_credentials = _load_json_credential("GDRIVE_CREDENTIALS")

    _raw_oauth = os.getenv("GDRIVE_OAUTH_TOKEN", "").strip()
    if _raw_oauth:
        oauth_path = Path(_raw_oauth.replace("\\", "/")).expanduser()
        if not oauth_path.is_absolute():
            oauth_path = Path.cwd() / oauth_path
        gdrive_oauth_token_path = oauth_path if oauth_path.is_file() else None
    else:
        gdrive_oauth_token_path = None

    return Settings(
        ENV=os.getenv("ENV"),
        datahub_project_id=os.getenv("GCP_DATAHUB_PROJECT_ID", "data-hub-468216"),
        datahub_credentials=gcp_datahub_credentials,
        dataforseo_username=os.getenv("DATAFORSEO_API_LOGIN", ""),
        dataforseo_password=os.getenv("DATAFORSEO_API_PASSWORD", ""),
        gdrive_credentials=gdrive_credentials,
        gdrive_oauth_token_path=gdrive_oauth_token_path,
        openai_key=os.getenv("OPENAI_API_KEY", ""),
        gemini_key=os.getenv("GEMINI_API_KEY", ""),
        perplexity_key=os.getenv("PERPLEXITY_API_KEY", ""),
        anthropic_key=os.getenv("ANTHROPIC_API_KEY", ""),
        xai_key=os.getenv("XAI_API_KEY", ""),
    )
