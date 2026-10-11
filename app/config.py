import os


class Settings:
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://app:changeme@localhost:5432/anzhelika_merdenova",
    )
    db_schema = os.environ.get("DB_SCHEMA", "anzhelika_merdenova")
    secret_key = os.environ.get("SECRET_KEY", "changeme")
    session_max_age = 8 * 3600


settings = Settings()
