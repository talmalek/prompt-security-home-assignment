"""Pydantic settings for tests and API client."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _REPO_ROOT / ".env"


def _default_headless() -> bool:
    """Headless in GitHub Actions; headed by default on a normal dev machine."""
    return os.environ.get("GITHUB_ACTIONS", "").lower() in ("true", "1", "yes")


class TestConfig(BaseSettings):
    """Playwright and test run configuration."""

    model_config = SettingsConfigDict(env_prefix="TEST_", env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    base_url: str = Field(validation_alias="TEST_BASE_URL", default="https://chatgpt.com/")
    headless: bool = Field(validation_alias="TEST_HEADLESS", default_factory=_default_headless)
    browser_type: str = Field(validation_alias="TEST_BROWSER", default="chromium")
    default_timeout_ms: int = Field(validation_alias="TEST_DEFAULT_TIMEOUT", default=15_000)
    screenshot_on_failure: bool = Field(validation_alias="TEST_SCREENSHOT_ON_FAILURE", default=True)
    slow_mo_ms: int = Field(validation_alias="TEST_SLOW_MO", default=50)
    record_video: bool = Field(validation_alias="TEST_RECORD_VIDEO", default=False)
    browser_args: list[str] = Field(
        default_factory=lambda: [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ],
    )

    @field_validator("browser_args", mode="before")
    @classmethod
    def parse_browser_args(cls, v: object) -> object:
        if isinstance(v, str):
            import json

            return json.loads(v)
        return v


class ExtensionConfig(BaseSettings):
    """Prompt Security extension: API credentials and unpacked extension path.

    `api_key` is optional at settings import so optional tooling can run; UI tests fail fast
    in the Playwright fixture if unset.
    Unpacked extensions get a runtime Chrome id (from the service worker URL);
    `chrome_store_extension_id` is only used when downloading the CRX.
    """

    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # Optional at import time so CI/scripts (e.g. push_to_notion) can run without it;
    # UI tests require it — see `browser_context` in tests/conftest.py.
    api_key: SecretStr | None = Field(default=None, validation_alias="PROMPT_SECURITY_API_KEY")
    api_domain: str = Field(default="eu.prompt.security", validation_alias="PROMPT_SECURITY_API_DOMAIN")
    chrome_store_extension_id: str = Field(
        default="iidnankcocecmgpcafggbgbmkbcldmno",
        validation_alias="CHROME_STORE_EXTENSION_ID",
    )
    extension_path: Path = Field(default=Path("extension"), validation_alias="EXTENSION_PATH")

    @field_validator("extension_path", mode="before")
    @classmethod
    def coerce_extension_path(cls, v: object) -> object:
        if isinstance(v, str):
            return Path(v)
        return v

    def resolved_extension_dir(self) -> Path:
        p = self.extension_path
        if not p.is_absolute():
            p = _REPO_ROOT / p
        return p.resolve()


class NotionConfig(BaseSettings):
    """Notion reporter configuration (stakeholder dashboard).

    The reporter is opt-in: if `token` or `runs_database_id` is unset, CI
    skips the publish step silently (`enabled` returns False). On the
    customer side only the env values change — no code edits.
    """

    model_config = SettingsConfigDict(
        env_prefix="NOTION_", env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    token: SecretStr | None = Field(default=None, validation_alias="NOTION_TOKEN")
    runs_database_id: str | None = Field(default=None, validation_alias="NOTION_RUNS_DATABASE_ID")
    api_version: str = Field(default="2022-06-28", validation_alias="NOTION_API_VERSION")
    timeout_seconds: float = Field(default=15.0, validation_alias="NOTION_TIMEOUT")

    @property
    def enabled(self) -> bool:
        return self.token is not None and self.runs_database_id is not None


class LogConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOG_", env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    level: str = Field(validation_alias="LOG_LEVEL", default="INFO")


class Settings(BaseSettings):
    """Root settings singleton."""

    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    test: TestConfig = Field(default_factory=lambda: TestConfig())
    extension: ExtensionConfig = Field(default_factory=lambda: ExtensionConfig())
    notion: NotionConfig = Field(default_factory=lambda: NotionConfig())
    log: LogConfig = Field(default_factory=lambda: LogConfig())


settings = Settings()
