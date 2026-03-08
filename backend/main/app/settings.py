from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_url: str  # postgresql://user:pass@host:port/db

    class Config:
        env_file = None
        env_prefix = ""
        case_sensitive = False

settings = Settings()