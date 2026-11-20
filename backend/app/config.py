from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_db: str = "emcache"
    postgres_user: str = "dev_user"
    postgres_password: str = "dev_secret_password"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432

    pluggy_client_id: str = ""
    pluggy_client_secret: str = ""
    pluggy_base_url: str = "https://api.pluggy.ai"
    pluggy_webhook_url: str = ""
    pluggy_include_sandbox: bool = True

    llm_provider: str = "openai"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    transcription_model: str = "whisper-1"
    vision_model: str = "gpt-4o-mini"

    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
