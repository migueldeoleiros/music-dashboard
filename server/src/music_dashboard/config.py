from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """API settings deliberately have no music-service credential fields."""

    model_config = SettingsConfigDict(env_prefix="MUSIC_", extra="ignore")
    database_url: str = "postgresql+psycopg://music_api@db/music_dashboard"
    fixture_mode: bool = True


settings = Settings()
