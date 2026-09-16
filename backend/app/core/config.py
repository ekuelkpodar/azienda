"""Runtime configuration via pydantic-settings. Prefix: AZIENDA_.

Every knob a builder needs is a setting here — no magic constants for pricing,
thresholds, or model names. Secrets come only from the environment.
"""
from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from the environment. No secrets in code."""

    model_config = SettingsConfigDict(env_prefix="AZIENDA_", extra="ignore")

    # --- core ---
    database_url: str = "postgresql+asyncpg://azienda:azienda@localhost:5432/azienda"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = ""  # REQUIRED in prod; empty fails fast at startup
    environment: str = "development"  # development | staging | production
    version: str = "0.1.0"
    debug: bool = False

    # --- auth (ADR-008) ---
    jwt_access_ttl_minutes: int = 10
    jwt_refresh_ttl_days: int = 7
    jwt_issuer: str = "azienda"
    jwt_algorithm: str = "HS256"
    bcrypt_rounds: int = 12
    api_key_prefix: str = "azk_"

    # --- governance: risk + approvals ---
    risk_low_max: float = 30.0        # score < 30  -> LOW
    risk_high_min: float = 70.0       # score >= 70 -> HIGH
    approval_ttl_seconds: int = 900   # approval request expiry; timeout == DENY
    approval_medium_require: bool = True  # MEDIUM risk requires approval (configurable)
    cost_approval_threshold_usd: float = 5.0  # est. cost above this -> approval

    # --- governance: budgets ---
    budget_default_credits_monthly: float = 1000.0
    budget_alert_thresholds: str = "0.5,0.8,0.95"  # comma-separated fractions

    # --- governance: rate limiting ---
    rate_limit_per_minute: int = 120
    rate_limit_burst: int = 240

    # --- governance: DLP ---
    dlp_enabled: bool = True

    # --- billing ---
    billing_default_plan: str = "starter"
    billing_currency: str = "USD"
    credit_usd_rate: float = 100.0  # credits per 1 USD (1 credit = $0.01)

    # --- idempotency ---
    idempotency_ttl_hours: int = 24

    # --- observability ---
    log_level: str = "INFO"
    log_json: bool = True
    otlp_endpoint: str = ""

    @field_validator("jwt_secret")
    @classmethod
    def _warn_empty_secret(cls, v: str) -> str:
        return v

    def require_jwt_secret(self) -> str:
        """Fail fast if no JWT secret is configured (production safety)."""
        if not self.jwt_secret:
            raise RuntimeError(
                "AZIENDA_JWT_SECRET is not set. Generate one with "
                "`openssl rand -hex 32`. Refusing to start without it."
            )
        return self.jwt_secret

    @property
    def budget_alert_threshold_list(self) -> list[float]:
        return [float(x) for x in self.budget_alert_thresholds.split(",") if x.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


settings = Settings()
