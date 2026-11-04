from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The value committed in .env.example and in this file. It is public, so anyone who
# reads the repo can mint a session token with it.
DEV_SESSION_SECRET = "dev-only-insecure-secret-change-me"
MIN_SESSION_SECRET_BYTES = 32


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

    @field_validator("daily_budget_usd", "static_dir", mode="before")
    @classmethod
    def _blank_is_none(cls, value):
        """An env var set to the empty string means "unset", not "zero" or "cwd"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _refuse_to_deploy_with_dev_defaults(self):
        """Fail at startup, not at the first request.

        `PROVIDER=fake` is a local/test configuration, so the permissive defaults are
        fine there. Anything that spends real money or faces the internet must not
        boot with a secret that is printed in the repository.
        """
        if self.provider == "fake":
            return self

        problems: list[str] = []
        if self.session_secret == DEV_SESSION_SECRET:
            problems.append(
                "SESSION_SECRET is still the public default; generate one with "
                "python -c \"import secrets;print(secrets.token_urlsafe(48))\""
            )
        elif len(self.session_secret) < MIN_SESSION_SECRET_BYTES:
            problems.append(
                f"SESSION_SECRET is {len(self.session_secret)} bytes; "
                f"use at least {MIN_SESSION_SECRET_BYTES}"
            )
        if not self.admin_password_hash and not self.admin_password:
            problems.append("set ADMIN_PASSWORD_HASH (or ADMIN_PASSWORD for dev)")
        if self.provider == "openrouter" and not self.openrouter_api_key:
            problems.append("PROVIDER=openrouter needs OPENROUTER_API_KEY")

        if problems:
            raise ValueError(
                "refusing to start with an unsafe configuration:\n  - "
                + "\n  - ".join(problems)
            )
        return self

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
