from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bybit_api_key: str = ""
    bybit_api_secret: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    database_url: str = "sqlite:///./tradeflow.db"
    scan_interval_seconds: int = 60
    alert_cooldown_seconds: int = 300
    cors_origins: str = "*"

    class Config:
        env_file = ".env"


settings = Settings()
