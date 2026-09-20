"""Runtime configuration for the CivitasX service.

The local defaults are deliberately self-contained so a developer can run the
case experience without an AWS account or any paid model calls. Cloud adapters
are selected explicitly through environment variables.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CIVITAS_",
        env_file=(".env", ".env.local"),
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "CivitasX API"
    environment: str = "development"
    api_version: str = "0.1.0"
    frontend_origin: str = "http://localhost:5173"
    # Comma-separated host/origin allowlists used by the Streamable HTTP MCP
    # transport. Keep these explicit so DNS-rebinding protection stays on.
    mcp_allowed_hosts: str = "localhost:*,127.0.0.1:*"
    mcp_allowed_origins: str = ""

    auth_mode: str = "local"
    # Optional local-pilot recovery secret. Keep unset in deployments that use
    # Cognito or do not want self-service local recovery.
    local_recovery_code: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CIVITAS_LOCAL_RECOVERY_CODE"),
    )
    session_ttl_hours: int = Field(default=24, ge=1, le=168)
    local_db_path: str = Field(
        default=".civitas/civitas.sqlite3",
        validation_alias=AliasChoices("CIVITAS_SQLITE_PATH", "CIVITAS_LOCAL_DB_PATH"),
    )
    storage: str = "sqlite"

    dynamodb_table: str = "civitasx"
    dynamodb_region: str = "ap-south-1"
    artifacts_bucket: str | None = None
    corpus_bucket: str | None = None

    # The local build uses the checked-in reference corpus for development. A
    # deployed build can point this at a packaged or refreshed manifest without
    # changing the research API.
    corpus_path: str = "data/corpus/manifest.json"
    police_registry_path: str = "data/police/bengaluru_stations.json"
    capability_registry_path: str = "data/capabilities/civic_capabilities.json"
    opa_url: str | None = None
    spatial_database_url: str | None = None
    temporal_target: str | None = None
    telemetry_enabled: bool = False
    translation_cache_path: str = ".civitas/translation-cache.json"
    live_cache_path: str = ".civitas/live-sources.json"
    live_cache_bucket: str | None = None
    live_cache_prefix: str = "runtime/live-sources/"
    live_status_path: str = ".civitas/live-connector-status.json"
    live_status_bucket: str | None = None
    live_status_prefix: str = "runtime/live-status/"
    live_fetch_timeout_seconds: int = Field(default=20, ge=3, le=120)
    research_stale_after_days: int = Field(default=180, ge=1, le=3650)
    research_max_sources: int = Field(default=6, ge=1, le=12)
    # Groq is the hosted primary for the local pilot; the read-only Codex CLI
    # is the automatic fallback when Groq is unavailable or unconfigured.
    agent_provider: str = "groq"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "hermes3:8b"
    agent_max_iterations: int = Field(default=8, ge=1, le=16)
    agent_request_timeout_seconds: int = Field(default=90, ge=5, le=300)
    agent_tool_timeout_seconds: int = Field(default=30, ge=3, le=120)
    agent_provider_retries: int = Field(default=3, ge=1, le=5)
    agent_context_max_chars: int = Field(default=48000, ge=8000, le=200000)
    agent_context_keep_messages: int = Field(default=12, ge=4, le=40)
    agent_grounding_verification: bool = True
    agent_codex_cli_fallback: bool = True
    agent_codex_cli_timeout_seconds: int = Field(default=120, ge=10, le=600)
    agent_codex_cli_persist_sessions: bool = True
    groq_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CIVITAS_GROQ_API_KEY", "GROQ_API_KEY"),
    )
    groq_model: str = "llama-3.3-70b-versatile"
    bedrock_model: str = "amazon.nova-lite-v1:0"
    data_gov_api_key: str | None = None
    apisetu_client_id: str | None = None
    digilocker_client_id: str | None = None
    cpgrams_client_id: str | None = None
    parivahan_client_id: str | None = None
    gstn_client_id: str | None = None
    abdm_client_id: str | None = None
    abha_client_id: str | None = None
    attachment_max_bytes: int = Field(default=15 * 1024 * 1024, ge=1_024, le=50 * 1024 * 1024)
    moderator_emails: str = "moderator@civitas.local"

    cognito_region: str | None = None
    cognito_user_pool_id: str | None = None
    cognito_client_id: str | None = None
    cognito_domain: str | None = None

    global_budget_usd: float = Field(default=80.0, ge=0)
    browser_concurrency: int = Field(default=2, ge=1, le=20)
    max_model_cost_per_case_usd: float = Field(default=2.0, ge=0)
    max_browser_seconds_per_case: int = Field(default=300, ge=1)
    max_browser_actions_per_attempt: int = Field(default=30, ge=1)
    # Only enables the local synthetic connector used for deterministic
    # end-to-end tests. Real government submission remains connector-gated.
    demo_submission_enabled: bool = False
    # Enables the reviewed Karnataka iPGRS browser connector. It opens the
    # official form, fills approved fields, and pauses for resident OTP/CAPTCHA
    # interaction before any final submission.
    ipgrs_submission_enabled: bool = False
    ipgrs_browser_url: str = "https://ipgrs.karnataka.gov.in/Citizens/GrievanceSelfService"
    ipgrs_browser_headless: bool = False
    # A certified authority API can be enabled without changing the agent or
    # ticket workflow. Keep this unset until the authority/API gateway has
    # issued a real endpoint and scoped credential for this use case.
    official_api_authority_id: str = "gba"
    official_api_url: str | None = None
    official_api_status_url: str | None = None
    official_api_token: str | None = None
    official_api_timeout_seconds: int = Field(default=30, ge=3, le=120)

    @field_validator("auth_mode")
    @classmethod
    def validate_auth_mode(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {"local", "cognito"}:
            raise ValueError("auth_mode must be local or cognito")
        return value

    @field_validator("storage")
    @classmethod
    def validate_storage(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {"sqlite", "dynamodb"}:
            raise ValueError("storage must be sqlite or dynamodb")
        return value

    @field_validator("agent_provider")
    @classmethod
    def validate_agent_provider(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {"ollama", "groq", "bedrock", "codex"}:
            raise ValueError("agent_provider must be ollama, groq, bedrock, or codex")
        return value

    @property
    def db_path(self) -> Path:
        return Path(self.local_db_path).expanduser().resolve()

    @property
    def corpus_manifest_path(self) -> Path:
        path = Path(self.corpus_path).expanduser()
        if path.is_absolute():
            return path
        # Resolve relative to the API package so `uvicorn` works from either
        # the repository root or services/api.
        return (Path(__file__).resolve().parents[2] / path).resolve()

    @property
    def police_registry(self) -> Path:
        path = Path(self.police_registry_path).expanduser()
        if path.is_absolute():
            return path
        return (Path(__file__).resolve().parents[2] / path).resolve()

    @property
    def capability_registry(self) -> Path:
        path = Path(self.capability_registry_path).expanduser()
        if path.is_absolute():
            return path
        return (Path(__file__).resolve().parents[2] / path).resolve()

    @property
    def translation_cache(self) -> Path:
        return Path(self.translation_cache_path).expanduser().resolve()

    @property
    def live_cache(self) -> Path:
        return Path(self.live_cache_path).expanduser().resolve()

    @property
    def live_status(self) -> Path:
        return Path(self.live_status_path).expanduser().resolve()

    @property
    def cognito_issuer(self) -> str | None:
        if self.cognito_region and self.cognito_user_pool_id:
            return (
                f"https://cognito-idp.{self.cognito_region}.amazonaws.com/"
                f"{self.cognito_user_pool_id}"
            )
        return None

    def validate_startup(self) -> None:
        """Reject unsafe production defaults before serving any request."""

        if self.environment.lower() in {"production", "prod"}:
            if self.auth_mode != "cognito":
                raise RuntimeError("CIVITAS_AUTH_MODE=cognito is required in production")
            required = {
                "CIVITAS_COGNITO_REGION": self.cognito_region,
                "CIVITAS_COGNITO_USER_POOL_ID": self.cognito_user_pool_id,
                "CIVITAS_COGNITO_CLIENT_ID": self.cognito_client_id,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise RuntimeError(f"Missing Cognito settings in production: {', '.join(missing)}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_startup()
    return settings
