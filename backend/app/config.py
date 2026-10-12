"""
Central configuration for the Agentic AI Code Review & Debugging System.

All secrets come from environment variables (see .env.example). Nothing is
ever hard-coded here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)


def _bool_env(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
   
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    anthropic_max_tokens: int = int(os.getenv("ANTHROPIC_MAX_TOKENS", "4096"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "4"))
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "120"))

    github_token: str = os.getenv("GITHUB_TOKEN", "")
    github_api_base: str = os.getenv("GITHUB_API_BASE", "https://api.github.com")

    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/agentic_review.db")

    max_symbols_per_context_batch: int = int(os.getenv("MAX_SYMBOLS_PER_CONTEXT_BATCH", "40"))
    max_agent_tool_iterations: int = int(os.getenv("MAX_AGENT_TOOL_ITERATIONS", "20"))

    verification_timeout_seconds: int = int(os.getenv("VERIFICATION_TIMEOUT_SECONDS", "60"))
    verification_enabled: bool = _bool_env("VERIFICATION_ENABLED", True)
    allowed_test_commands: str = os.getenv(
        "ALLOWED_TEST_COMMANDS",
        "pytest,python -m pytest,python3 -m pytest,python -m unittest discover,python3 -m unittest discover",
    )

    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    app_env: str = os.getenv("APP_ENV", "development")
    cors_origins: str = os.getenv("CORS_ORIGINS", "*")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def require_anthropic_key() -> str:
    s = get_settings()
    if not s.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file "
            "(see .env.example) before running an AI review."
        )
    return s.anthropic_api_key


def get_allowed_test_commands() -> list[str]:
    s = get_settings()
    return [c.strip() for c in s.allowed_test_commands.split(",") if c.strip()]