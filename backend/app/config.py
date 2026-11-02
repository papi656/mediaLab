from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- provider -------------------------------------------------------
    provider: Literal["fake", "openrouter"] = "fake"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # --- auth -----------------------------------------------------------
    session_secret: str = "dev-only-insecure-secret-change-me"
    session_days: int = 7
    admin_password: str = ""          # plaintext, dev convenience only
    admin_password_hash: str = ""     # bcrypt; takes precedence when set

    # --- storage --------------------------------------------------------
    data_dir: Path = Path("./data")
    static_dir: Path | None = None     # built SPA, mounted at / when present
    max_upload_mb: int = 20

    # --- cost / limits --------------------------------------------------
    daily_budget_usd: float | None = None
    max_n_hard_cap: int = 4
    job_timeout_seconds: float = 120.0

    # --- retention ------------------------------------------------------
    soft_delete_retention_days: int = 7
    gc_interval_hours: float = 6.0

    # --- fake provider --------------------------------------------------
    fake_latency_ms: int = 50
    fake_fail: bool = False

    @property
    def assets_dir(self) -> Path:
        return self.data_dir / "assets"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "medialab.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+pysqlite:///{self.db_path}"

    @property
    def catalogue_path(self) -> Path:
        return Path(__file__).resolve().parent / "data" / "models.json"

    def prepare_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
