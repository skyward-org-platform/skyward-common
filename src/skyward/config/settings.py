# skyward/config/settings.py
"""
Central configuration loader for Skyward projects.

IMPORTANT: load_config() changes the working directory to PROJECT_ROOT.
Always save and restore cwd if needed:
    RUNNING_DIR = os.getcwd()
    cfg = load_config()
    os.chdir(RUNNING_DIR)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import json
import warnings
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# In src layout: src/skyward/config/settings.py -> parents[3] = repo root
# When installed as editable (-e), this resolves to the repo root.
# When installed as a package, callers should pass env_file path explicitly.
PROJECT_ROOT = Path(__file__).resolve().parents[3]

@dataclass
class Settings:

    # ENV
    ENV: str

    # DB
    datahub_project_id: str
    datahub_credentials: dict

    # Data For SEO
    dataforseo_username: str
    dataforseo_password: str

    # Google Drive
    gdrive_credentials: dict
    gdrive_oauth_token_path: Path

    # OpenAI Key
    openai_key: str

    # Gemini Key
    gemini_key: str

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )



def load_config(env_file: str | None = ".env") -> Settings:

    # making sure we are using the config in the root directory
    os.chdir(PROJECT_ROOT)

    # Load .env if present (no-op in CI unless you create one)
    load_dotenv(env_file, override=False)

    # Normalize backslashes in .env paths so they work on both Windows and Linux/WSL
    _raw_datahub = os.getenv("GCP_DATAHUB_CREDENTIALS", "")
    gcp_datahub_credentials_path = PROJECT_ROOT / _raw_datahub.replace("\\", "/")
    gcp_datahub_credentials = {}

    if gcp_datahub_credentials_path:
        cred_path = Path(gcp_datahub_credentials_path)
        if cred_path.exists():
            try:
                with cred_path.open("r", encoding="utf-8") as f:
                    gcp_datahub_credentials = json.load(f)
            except Exception as e:
                warnings.warn(f"Failed to load JSON from {cred_path}: {e}")
        else:
            warnings.warn(
                f"GCP_DATAHUB_CREDENTIALS path '{cred_path}' does not exist. Using empty credentials."
            )

    _raw_gdrive = os.getenv("GDRIVE_CREDENTIALS", "")
    gdrive_credentials_path = PROJECT_ROOT / _raw_gdrive.replace("\\", "/")
    gdrive_credentials = {}

    if gdrive_credentials_path:
        cred_path = Path(gdrive_credentials_path)
        if cred_path.exists():
            try:
                with cred_path.open("r", encoding="utf-8") as f:
                    gdrive_credentials = json.load(f)
            except Exception as e:
                warnings.warn(f"Failed to load JSON from {cred_path}: {e}")
        else:
            warnings.warn(
                f"GDRIVE_CREDENTIALS path '{cred_path}' does not exist. Using empty credentials."
            )

    _raw_oauth = os.getenv("GDRIVE_OAUTH_TOKEN", "")
    gdrive_oauth_token_path = PROJECT_ROOT / _raw_oauth.replace("\\", "/")

    if gdrive_oauth_token_path:
        cred_path = Path(gdrive_oauth_token_path)
        if cred_path.exists():
            try:
                with cred_path.open("r", encoding="utf-8") as f:
                    gdrive_oauth = json.load(f)
            except Exception as e:
                warnings.warn(f"Failed to load JSON from {cred_path}: {e}")
        else:
            warnings.warn(
                f"GDRIVE_OAUTH_TOKEN path '{cred_path}' does not exist. Using empty credentials."
            )
            gdrive_oauth_token_path = Path("__missing__")


    return Settings(
        ENV=os.getenv("ENV"),
        datahub_project_id=os.getenv("GCP_DATAHUB_PROJECT_ID", ""),
        datahub_credentials=gcp_datahub_credentials,
        dataforseo_username=os.getenv("DATAFORSEO_API_LOGIN", ""),
        dataforseo_password=os.getenv("DATAFORSEO_API_PASSWORD", ""),
        gdrive_credentials=gdrive_credentials,
        gdrive_oauth_token_path=gdrive_oauth_token_path,
        openai_key=os.getenv("OPENAI_API_KEY", ""),
        gemini_key=os.getenv("GEMINI_API_KEY", ""),
    )
