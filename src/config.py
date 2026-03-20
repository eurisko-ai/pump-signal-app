"""Configuration management for Pump Signal app"""
from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    # Database
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "pump_signal"
    db_user: str = "pump_user"
    db_password: str = "secure_password"
    
    # Moralis API
    moralis_api_key: str = ""
    
    # Telegram
    telegram_bot_token: str = ""
    telegram_group_id: str = "-5137818458"  # Pump Signals group
    
    # FastAPI
    fastapi_host: str = "0.0.0.0"
    fastapi_port: int = 8000
    fastapi_env: str = "development"
    
    # Scanner
    scan_interval_seconds: int = 60
    alert_threshold: int = 70
    min_market_cap: int = 10000
    min_holders: int = 50
    dedup_window_hours: int = 6
    
    # Housekeeper (cleanup) — reduced to prevent memory bloat
    data_retention_hours: int = 2
    housekeeper_interval_minutes: int = 15
    
    # Logging
    log_level: str = "INFO"
    
    # API URLs
    dexscreener_api_url: str = "https://api.dexscreener.com"
    pump_fun_api_url: str = "https://frontend-api-v3.pump.fun"
    moralis_api_url: str = "https://solana-gateway.moralis.io"
    
    class Config:
        env_file = ".env"
        case_sensitive = False
    
    @property
    def database_url(self) -> str:
        return f"postgresql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

@lru_cache()
def get_settings():
    return Settings()
