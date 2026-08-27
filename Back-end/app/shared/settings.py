# ============================================================
# PaySentinelIQ — Application Settings (pydantic-settings)
# All config from env vars — never hardcode secrets
# ============================================================

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──
    APP_NAME: str = "PaySentinelIQ"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = Field(
        default="development",
        pattern="^(development|staging|production|test)$",
    )
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ── Server ──
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 4
    CORS_ORIGINS: list[str] = Field(default=[
        "http://localhost:3000",
        "https://pay-sentinel-iq.vercel.app",
        "https://paysentineliq.vercel.app",
    ])

    # ── Database (PostgreSQL) ──
    DATABASE_URL: SecretStr = Field(
        default=SecretStr("postgresql+asyncpg://psi:psi_secret@localhost:5432/paysentineliq"),
    )
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10
    DATABASE_POOL_TIMEOUT: int = 30
    DATABASE_ECHO: bool = False

    # ── Redis ──
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_CACHE_TTL: int = 300

    # ── RabbitMQ / Event-Driven Messaging ──
    # Master switch. When false, the API uses a no-op publisher and
    # workers refuse to start (local dev without RabbitMQ stays usable).
    RABBITMQ_ENABLED: bool = True
    # amqp(s)://user:pass@host:5672/vhost — credentials come from env, never hardcoded.
    RABBITMQ_URL: SecretStr = Field(
        default=SecretStr("amqp://psi:psi_secret@localhost:5672/"),
    )
    RABBITMQ_EXCHANGE: str = "sentinel.events"       # main topic exchange
    RABBITMQ_DLX: str = "sentinel.dlx"               # dead-letter exchange
    RABBITMQ_CONNECT_TIMEOUT: float = 10.0           # seconds
    RABBITMQ_HEARTBEAT: int = 60                     # seconds
    RABBITMQ_PREFETCH_COUNT: int = 10                # per consumer (fair dispatch)
    # Logical source stamped into every event envelope.
    EVENT_SOURCE: str = "paysentinel-api"
    # Retry policy for consumers: max handler attempts before DLQ and
    # the backoff (seconds) applied per attempt (CSV, last value repeats).
    EVENT_RETRY_MAX_ATTEMPTS: int = 3
    EVENT_RETRY_BACKOFF_SECONDS: str = "5,30,120"

    @property
    def retry_backoff_seconds(self) -> list[int]:
        """Parse EVENT_RETRY_BACKOFF_SECONDS CSV into a list of ints."""
        values: list[int] = []
        for chunk in self.EVENT_RETRY_BACKOFF_SECONDS.split(","):
            chunk = chunk.strip()
            if chunk.isdigit():
                values.append(int(chunk))
        return values or [5]

    # ── Email (transactional, via workers — never inside HTTP requests) ──
    EMAIL_ENABLED: bool = False  # false → ConsoleEmailService (logs instead of sending)
    EMAIL_FROM: str = "SentinelaPay <no-reply@paysentineliq.com>"
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: SecretStr | None = None
    SMTP_USE_TLS: bool = True
    SMTP_TIMEOUT: float = 15.0
    # Public URL of the frontend — used to build links inside emails.
    APP_BASE_URL: str = "http://localhost:3000"

    # ── Bill due-soon scheduler (standalone worker) ──
    BILL_SCHEDULER_ENABLED: bool = True
    BILL_SCHEDULER_INTERVAL_SECONDS: int = 3600  # how often the scan runs
    BILL_DUE_SOON_DAYS: int = 2                  # "due soon" horizon

    # ── Celery ──
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"
    CELERY_TASK_TIME_LIMIT: int = 600
    CELERY_TASK_SOFT_TIME_LIMIT: int = 540

    # ── Auth / JWT ──
    JWT_SECRET_KEY: SecretStr = Field(default=SecretStr("change-me-in-production-use-256-bit-key"))
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    MFA_ISSUER: str = "PaySentinelIQ"

    # ── Google OIDC ──
    GOOGLE_CLIENT_ID: str = ""

    # ── AWS ──
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: SecretStr | None = None
    AWS_REGION: str = "us-east-1"
    S3_BUCKET: str = "psi-documents"
    S3_PRESIGNED_URL_EXPIRY: int = 3600
    AWS_TEXTRACT_ROLE_ARN: str | None = None

    # ── AI / LLM Provider Selection ──
    # Supported providers: ollama, openai, anthropic, bedrock, groq, gemini
    # Default is 'ollama' for zero-cost local inference.
    LLM_PROVIDER: str = Field(
        default="ollama",
        pattern="^(ollama|openai|anthropic|bedrock|groq|gemini)$",
    )

    # ── Shared LLM Settings ──
    AI_TEMPERATURE: float = 0.1
    AI_MAX_TOKENS: int = 4096

    # ── Ollama (Local LLM) ──
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3"
    OLLAMA_TIMEOUT: float = 120.0  # seconds — local models can be slower
    OLLAMA_MAX_RETRIES: int = 3
    OLLAMA_NUM_GPU: int | None = None  # None = auto-detect; set to 0 for CPU-only
    OLLAMA_NUM_THREAD: int | None = None  # None = auto-detect

    # ── OpenAI (Cloud LLM — optional, for fallback or specific use cases) ──
    OPENAI_API_KEY: SecretStr | None = None
    OPENAI_MODEL: str = "gpt-4o"

    # ── Gemini (Google AI — production cloud LLM) ──
    GEMINI_API_KEY: SecretStr | None = None
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # ── Anthropic (Cloud LLM — reserved for future use) ──
    ANTHROPIC_API_KEY: SecretStr | None = None

    # ── Sentry ──
    SENTRY_DSN: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1
    SENTRY_PROFILES_SAMPLE_RATE: float = 0.1

    # ── Rate Limiting ──
    RATE_LIMIT_PER_USER: int = 100  # requests per window
    RATE_LIMIT_WINDOW: int = 60  # seconds
    RATE_LIMIT_LOGIN_MAX: int = 5  # attempts per window

    # ── Feature Flags ──
    ENABLE_AI_AGENTS: bool = True
    ENABLE_OCR: bool = True
    ENABLE_COMPLIANCE_CHECKS: bool = True

    # ── Fase 3A Feature Flags ──
    USE_CANONICAL_PIPELINE: bool = True     # Fase 5: CanonicalPipeline is now the default
    ENABLE_SHADOW_PIPELINE: bool = False
    ENABLE_PIPELINE_EVENTS: bool = True
    ENABLE_EXPLAINABILITY_PREVIEW: bool = False

    # ── Fase 4 Feature Flags ──
    ENABLE_STRUCTURED_LOGGING: bool = True       # JSON-formatted logs
    ENABLE_PROMETHEUS: bool = False              # Prometheus metrics endpoint
    ENABLE_TRACING: bool = False                 # OpenTelemetry distributed tracing
    ENABLE_HEALTHCHECK: bool = True              # /health, /ready, /live endpoints
    ENABLE_RATE_LIMIT: bool = False              # Per-IP/user rate limiting
    ENABLE_SECURITY_VALIDATION: bool = True      # Magic bytes + MIME + size validation
    ENABLE_GOLDEN_DATASET: bool = False          # Golden dataset test runner
    ENABLE_BENCHMARK: bool = False               # Performance benchmark mode

    # ── OCR ──
    OCR_PROVIDER: str = "tesseract"  # "tesseract" (today) or "textract" (future)
    OCR_LANGUAGE: str = "por+eng"    # Tesseract language codes
    OCR_PREPROCESS: bool = True      # Enable image preprocessing
    OCR_DPI: int = 300               # DPI for PDF-to-image conversion

    # ── Business ──
    MAX_UPLOAD_SIZE_MB: int = 50
    # ── Fase 3B: Unified thresholds (single source of truth in ThresholdProvider) ──
    RISK_SCORE_THRESHOLD_HIGH: int = 70    # >= 70 = HIGH (REJECT)
    RISK_SCORE_THRESHOLD_MEDIUM: int = 40   # >= 40 = MEDIUM (MANUAL_REVIEW)
    # < 40 = LOW (ACCEPT)
    DOCUMENT_RETENTION_DAYS: int = 2555  # 7 years for payroll records

    # ── LGPD / Privacy Compliance ──
    TERMS_VERSION: str = "1.0.0"
    PRIVACY_VERSION: str = "1.0.0"
    DATA_RETENTION_DAYS: int = 2555  # Default: 7 years (Brazilian labor law)
    OCR_TEMP_FILE_RETENTION_HOURS: int = 24  # Temp OCR files expire after 24h
    ACCOUNT_DELETION_GRACE_PERIOD_DAYS: int = 30  # Soft-delete grace period
    CONSENT_REQUIRED_FOR_LOGIN: bool = True
    PRESIGNED_URL_EXPIRY_SECONDS: int = 3600  # 1 hour
    AUDIT_RETENTION_DAYS: int = 1825  # 5 years for audit logs
    ANONYMIZE_ON_DELETION: bool = True
    MAX_EXPORT_SIZE_MB: int = 500

    # ── Data Breach / ANPD (LGPD Art. 48) ──
    ANPD_NOTIFICATION_EMAIL: str = "anpd@example.gov.br"  # Replace with real ANPD contact
    ANPD_NOTIFICATION_NAME: str = "ANPD — Autoridade Nacional de Proteção de Dados"
    BREACH_DEADLINE_HOURS: int = 72  # LGPD Art. 48 — 72-hour notification window
    BREACH_AFFECTED_THRESHOLD: int = 50  # Notify all subjects if affected > this number
    BREACH_AUTO_NOTIFY_ANPD: bool = True  # Auto-trigger notification task on registration


@lru_cache
def get_settings() -> Settings:
    return Settings()
