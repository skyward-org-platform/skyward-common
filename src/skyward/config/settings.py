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

    # Helper: load a JSON credential file from an env var path.
    # Returns None if the env var is empty (signals ADC / no credentials).
    def _load_json_credential(env_var: str) -> dict | None:
        raw = os.getenv(env_var, "").strip()
        if not raw:
            return None
        cred_path = PROJECT_ROOT / raw.replace("\\", "/")
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
    gdrive_oauth_token_path = (PROJECT_ROOT / _raw_oauth.replace("\\", "/")) if _raw_oauth else None


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
    )
