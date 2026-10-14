from typing import List, Optional, Union

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- Identity -------------------------------------------------------
    PROJECT_NAME: str = "AIMONITOR"
    VERSION: str = "0.6.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # --- Storage --------------------------------------------------------
    DATABASE_URL: str = "sqlite:///./aimonitor.db"

    # --- CORS -----------------------------------------------------------
    # Defaults to the local API origins. Set CORS_ORIGINS=* only when the API
    # is genuinely public; credentials are disabled automatically in that case
    # because browsers reject `Access-Control-Allow-Origin: *` with cookies.
    CORS_ORIGINS: Union[List[str], str] = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
    # Without API_KEY the server only answers requests addressed to localhost, so a web page cannot
    # reach it by pointing its own domain at 127.0.0.1 (DNS rebinding). Add host names here only for
    # a trusted network; setting API_KEY is the usual way to serve other names.
    ALLOWED_HOSTS: Union[List[str], str] = []

    # --- Authentication -------------------------------------------------
    # When API_KEY is unset the server runs in open mode (single-user / local
    # development) and logs a warning at startup.
    API_KEY: Optional[str] = None
    REQUIRE_AUTH_FOR_READS: bool = False

    # --- Ingestion limits ----------------------------------------------
    MAX_BATCH_SIZE: int = 500
    # Applied to Content-Length before the body is read, unlike MAX_BATCH_SIZE
    # which can only reject a payload after it has been parsed.
    MAX_REQUEST_BYTES: int = 8_388_608  # 8 MiB
    MAX_ERROR_MESSAGE_CHARS: int = 2000
    MAX_METADATA_BYTES: int = 8192
    MAX_TAGS: int = 20
    MAX_TAG_LENGTH: int = 64

    # --- Aggregation ----------------------------------------------------
    DEFAULT_WINDOW_HOURS: int = 24
    MAX_AGGREGATION_ROWS: int = 200_000

    # --- Background jobs ------------------------------------------------
    SCHEDULER_ENABLED: bool = True
    ALERT_INTERVAL_SECONDS: int = 60
    ALERT_ALLOW_PRIVATE_WEBHOOKS: bool = False
    RETENTION_INTERVAL_SECONDS: int = 3600
    # 0 disables automatic purging; otherwise traces older than N days are removed.
    RETENTION_DAYS: int = 0

    @field_validator("CORS_ORIGINS", "ALLOWED_HOSTS", mode="before")
    @classmethod
    def split_comma_separated(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, (list, str)):
            return v  # type: ignore[return-value]
        raise ValueError(v)

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def normalize_log_level(cls, v: str) -> str:
        return str(v).strip().upper()

    @field_validator("API_KEY", mode="before")
    @classmethod
    def normalize_api_key(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        stripped = str(v).strip()
        return stripped or None

    @property
    def cors_origin_list(self) -> List[str]:
        origins = self.CORS_ORIGINS
        if isinstance(origins, str):
            origins = [origins]
        return [str(o).rstrip("/") for o in origins if str(o).strip()]

    @property
    def allowed_host_list(self) -> List[str]:
        hosts = self.ALLOWED_HOSTS
        if isinstance(hosts, str):
            hosts = [hosts]
        return [str(h).strip().lower() for h in hosts if str(h).strip()]

    @property
    def cors_allows_wildcard(self) -> bool:
        return "*" in self.cors_origin_list

    @property
    def auth_enabled(self) -> bool:
        return self.API_KEY is not None

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
