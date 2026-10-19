"""Application configuration for the OmniRetail AI Analytics Platform.

WHY THIS FILE EXISTS
--------------------
Every part of the project (scripts, FastAPI, Alembic, notebooks) needs the same
answers to questions like "where is the database?" and "which LLM model are we
using?". If each file read os.environ itself, those answers would be scattered
and could drift apart.

So we centralise them here:

    .env file  ->  pydantic-settings  ->  Settings object  ->  rest of the app

Rules we follow:
* Secrets live in ``.env`` (never committed), or in real environment variables.
* Code only ever refers to ``settings.<name>`` — no hardcoded passwords.
* Settings are validated once, at startup, instead of failing deep in a query.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed view of everything we configure through the environment."""

    # ``env_file`` makes pydantic read the local .env during development.
    # ``extra="ignore"`` means unrelated variables in .env (or in the OS
    # environment, e.g. PATH) do not crash validation.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "OmniRetail AI Analytics Platform"
    app_env: str = "development"
    log_level: str = "INFO"

    # --- PostgreSQL, read/write role (loading data, migrations, writes) ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "omniretail"
    postgres_user: str = "omniretail"
    postgres_password: str = "change_me"

    # --- PostgreSQL, read-only role (used by the AI SQL analyst) ---
    postgres_readonly_user: str = "omniretail_readonly"
    postgres_readonly_password: str = "change_me_too"

    # --- Query cost protection ---
    # PostgreSQL's default `statement_timeout` is 0: wait forever. On a
    # 106-million-row database with a 59-million-row fact table that is not a
    # neutral default, it is an availability risk - one unlucky plan holds a
    # pooled connection for as long as it likes, and the pool is small.
    #
    # 300 s is chosen from a measurement, not a guess. The slowest of the 41
    # verified queries is `m5.demand_scale_by_hierarchy`, recorded at ~199-210 s
    # by the batch runner over the full 30,490 series. A ceiling BELOW that would
    # mean the API cannot serve a query this project has already verified, which
    # would be a false claim of completeness; a ceiling far above it would not
    # protect anything. 300 s leaves ~45% headroom over the measured worst case
    # while still bounding a runaway statement.
    #
    # This is a PER-STATEMENT ceiling applied to every read-only connection, so
    # it bounds each of the three executions a paginated query makes (the column
    # probe, the page, and the total) rather than the request as a whole.
    #
    # Set it to 0 to disable, which is only honest on a trusted host.
    statement_timeout_ms: int = 300_000

    # --- Overview cache ---
    # `/api/overview` runs 26 metric queries, several of them full scans of
    # `m5.sales_long` (59.2M rows). Measured cold, it takes about 80 seconds,
    # and two different pages need those numbers - so without a cache every
    # dashboard load pays for the same scan.
    #
    # The response carries `generated_at`, `cached` and `age_seconds`, so what
    # is reused is always visible. Set to 0 to recompute on every request.
    overview_cache_ttl_seconds: float = 120.0

    # --- LLM (optional; see app/ai/llm.py) ---
    # `OPENAI_API_KEY` empty is the shipped state and a supported one. The
    # analyst is deterministic and fully verified without it; the model may only
    # restate an answer that has already been checked against real query rows.
    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"

    # Off by default even with a key present, so adding a key to `.env` cannot
    # silently change what the API returns. Turning the LLM on is a second,
    # explicit decision.
    llm_enabled: bool = False

    # OpenAI-compatible, so Azure OpenAI, Ollama, vLLM and LM Studio all work by
    # changing this value. Implemented over `urllib`, which is in the standard
    # library, so no dependency has to be installed to use a model.
    llm_base_url: str = "https://api.openai.com/v1"
    llm_timeout_seconds: float = 20.0
    llm_max_tokens: int = 400

    # --- Retrieval ---
    # The vector half of the RAG pipeline. It is TF-IDF + truncated SVD
    # (latent semantic indexing) over the same passages BM25 indexes, built with
    # the scikit-learn that is already installed - offline, deterministic, no
    # model download. Turning it off leaves BM25 as the only retriever and the
    # API discloses that in `retrieval.mode`; it is a degradation, not a change
    # of behaviour, and answers stay grounded either way.
    rag_vector_enabled: bool = True

    # Latent dimensions to project onto. The index takes the smallest of this
    # and what the corpus mathematically allows (one fewer than the smaller of
    # the vocabulary and the chunk count), and reports which it got.
    rag_vector_dimensions: int = 128

    # Reciprocal-rank-fusion constant. 60 is the value from the original paper
    # and is not tuned; see `vector_index.reciprocal_rank_fusion`.
    rag_rrf_k: int = 60

    # --- Rate limiting ---
    # A statement timeout caps how long one query runs; it says nothing about
    # how many a caller may start. These bound the second thing.
    #
    # The default is generous because this is a local, read-only analytics tool
    # with a human user, not a public endpoint. It is here so that a shared
    # instance fails predictably rather than exhausting the connection pool.
    # Set `rate_limit_enabled=false` to remove the middleware entirely.
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 240
    rate_limit_burst: int = 40

    # --- Filesystem ---
    # Where the measurement artefacts live: `data/processed/_*.json`, written by
    # the ML and audit scripts and read by the AI verification layer and the
    # /api/forecasting and /api/anomalies endpoints.
    #
    # A relative path is resolved against the repository root rather than the
    # working directory, because the API is started with `--app-dir backend`
    # from the repo root while Alembic is run from either, and a settings value
    # that means different things depending on where you typed the command is a
    # bug waiting for someone to run uvicorn from the wrong shell.
    data_dir: Path = Path("data")

    # ------------------------------------------------------------------
    # Derived values
    # ------------------------------------------------------------------
    def _build_dsn(self, user: str, password: str) -> str:
        """Build a SQLAlchemy connection URL.

        The format is:  dialect+driver://user:password@host:port/database
        and we use the psycopg 3 driver, hence ``postgresql+psycopg``.
        ``quote_plus`` escapes characters such as @ / : that would otherwise
        break the URL if they appear in the password.
        """
        return (
            f"postgresql+psycopg://{quote_plus(user)}:{quote_plus(password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url(self) -> str:
        """Connection URL for the read/write role."""
        return self._build_dsn(self.postgres_user, self.postgres_password)

    @property
    def readonly_database_url(self) -> str:
        """Connection URL for the read-only role (AI analytics layer)."""
        return self._build_dsn(
            self.postgres_readonly_user, self.postgres_readonly_password
        )

    @property
    def resolved_data_dir(self) -> Path:
        """`data_dir`, absolute, relative paths anchored to the repository root."""
        if self.data_dir.is_absolute():
            return self.data_dir
        # .../backend/app/core/config.py -> .../
        return Path(__file__).resolve().parents[3] / self.data_dir


# ``lru_cache`` means the .env file is parsed once per process and the same
# Settings object is reused everywhere. Cheap, and it guarantees consistency.
_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the single, shared Settings instance for this process."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
