"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings for PillSafe backend."""

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "PillSafe API"
    app_version: str = "0.1.0"
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000,http://localhost:5173,http://localhost:8080"

    # SQLite cache & user confirmed medicine list
    cache_db_path: str = "data/cache.db"
    cache_ttl_seconds: int = 43200  # 12 hours (NLM recommends 12-24h for RxNorm)

    # NLM RxNorm / RxNav REST API (public, no API key required)
    rxnorm_base_url: str = "https://rxnav.nlm.nih.gov/REST"
    http_timeout_seconds: float = 10.0

    # openFDA Drug Label API (optional free API key raises daily quota)
    openfda_base_url: str = "https://api.fda.gov/drug/label.json"
    openfda_api_key: str = ""

    # NRCeS Common Drug Codes for India (Flat Files directory)
    cdci_data_dir: str = "data/cdci"

    # ABDM Drug Registry (requires ABDM sandbox approval + credentials)
    abdm_client_id: str = ""
    abdm_client_secret: str = ""
    abdm_gateway_url: str = "https://dev.abdm.gov.in/gateway/v0.5/sessions"
    abdm_drug_registry_url: str = "https://drugregistrysbx.abdm.gov.in/dr/v3"

    # DrugBank API (requires commercial or approved academic license)
    drugbank_api_key: str = ""
    drugbank_base_url: str = "https://api.drugbank.com/v1"

    @property
    def resolved_cache_db_path(self) -> Path:
        path = Path(self.cache_db_path)
        return path if path.is_absolute() else (BACKEND_DIR / path)

    @property
    def resolved_cdci_data_dir(self) -> Path:
        path = Path(self.cdci_data_dir)
        return path if path.is_absolute() else (BACKEND_DIR / path)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_abdm_configured(self) -> bool:
        return bool(self.abdm_client_id.strip() and self.abdm_client_secret.strip())

    @property
    def is_drugbank_configured(self) -> bool:
        return bool(self.drugbank_api_key.strip())

    @property
    def is_openfda_key_configured(self) -> bool:
        return bool(self.openfda_api_key.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
