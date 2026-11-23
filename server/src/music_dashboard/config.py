from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """API settings deliberately have no music-service credential fields."""

    model_config = SettingsConfigDict(env_prefix="MUSIC_", extra="ignore")
    database_url: str = "postgresql+psycopg://music_api@db/music_dashboard"
    fixture_mode: bool = True
    # Public per-listen feeds are separately opt-in; fixture mode always fails closed.
    public_event_feeds_enabled: bool = False


settings = Settings()


def refresh_enabled() -> bool:
    """Refresh needs a live database, so fixture mode disables it."""
    return not settings.fixture_mode
