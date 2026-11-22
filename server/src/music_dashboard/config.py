from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """API settings deliberately have no music-service credential fields."""

    model_config = SettingsConfigDict(env_prefix="MUSIC_", extra="ignore")
    database_url: str = "postgresql+psycopg://music_api@db/music_dashboard"
    fixture_mode: bool = True
    # Opt-in public refresh (MUSIC_PUBLIC_REFRESH_ENABLED). Needs a live database: it is
    # reported as disabled in fixture mode.
    public_refresh_enabled: bool = False
    # Exact public HTTPS origin (e.g. https://music.miguel.md). When non-empty, refresh
    # admission fails closed unless Origin and Host match it; forwarded headers are ignored.
    public_origin: str = ""


settings = Settings()
