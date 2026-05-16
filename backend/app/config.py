from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    app_secret_key: str = "change-me"
    database_url: str = "sqlite:///./mini_chatgpt.db"
    cors_origins: str = "http://localhost:5173"

    access_token_ttl_minutes: int = 30

    ai_provider: str = "fake"
    ai_model: str = "meta/llama3-8b-instruct"
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"

    session_participant_cap: int = 25

    agent_max_steps: int = 8
    agent_max_model_calls: int = 4
    agent_max_tool_calls: int = 3
    agent_max_same_tool_calls: int = 2
    agent_max_run_duration_seconds: int = 60

    model_gateway_max_retries: int = 2
    database_transaction_max_retries: int = 2
    tool_gateway_max_retries: int = 0

    seed_demo_data: bool = True
    demo_admin_email: str = "admin@mini.local"
    demo_admin_password: str = "password"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
