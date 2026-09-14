from datetime import date
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = Field(default="gpt-4.1-mini", min_length=1)
    tavily_api_key: SecretStr = SecretStr("")
    enable_search: bool = False
    max_pages_per_domain: int = Field(default=8, gt=0, le=50)
    max_depth: int = Field(default=2, ge=0, le=5)
    domain_concurrency: int = Field(default=2, gt=0, le=10)
    navigation_timeout_ms: int = Field(default=30000, gt=0)
    render_wait_ms: int = Field(default=5000, gt=0)
    http_timeout_seconds: float = Field(default=15, gt=0, allow_inf_nan=False)
    llm_timeout_seconds: float = Field(default=45, gt=0, allow_inf_nan=False)
    domain_budget_seconds: float = Field(default=180, gt=0, allow_inf_nan=False)
    max_context_tokens: int = Field(default=8000, ge=2000)
    max_output_tokens: int = Field(default=3500, gt=0)
    max_llm_requests_per_domain: int = Field(default=4, gt=0, le=10)
    headed: bool = False
    pricing_model: str | None = None
    input_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cached_input_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    pricing_source: str | None = None
    pricing_verified_at: date | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.render_wait_ms > self.navigation_timeout_ms:
            raise ValueError("RENDER_WAIT_MS must not exceed NAVIGATION_TIMEOUT_MS")
        prices = [
            self.input_price_per_million,
            self.cached_input_price_per_million,
            self.output_price_per_million,
        ]
        if any(p is not None for p in prices) and not (
            all(p is not None for p in prices)
            and self.pricing_model
            and self.pricing_source
            and self.pricing_verified_at
        ):
            raise ValueError(
                "Pricing requires all three rates, model, source, and verification date"
            )
        return self

    def require_live_key(self) -> None:
        if not self.openai_api_key.get_secret_value().strip():
            raise ValueError(
                "Set OPENAI_API_KEY in .env or the environment, then rerun; demo is offline"
            )

    def redacted(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"openai_api_key", "tavily_api_key"})
