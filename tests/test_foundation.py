from app.config import get_settings
from app.db.base import Base
from app.db.session import build_engine, build_session_factory
from app.models import Injury, RawTransaction, UpdateRun  # noqa: F401


def test_database_url_can_be_configured_from_environment(monkeypatch):
    database_url = "postgresql+psycopg://test:test@db:5432/test_db"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    try:
        assert get_settings().database_url == database_url
    finally:
        get_settings.cache_clear()


def test_sqlalchemy_foundation_builds_engine_and_session_factory():
    engine = build_engine("postgresql+psycopg://test:test@localhost:5432/test_db")
    session_factory = build_session_factory(engine)

    assert engine.dialect.name == "postgresql"
    assert session_factory.kw["bind"] is engine
    assert {"raw_transactions", "injuries", "update_runs"}.issubset(Base.metadata.tables)


def test_api_engine_is_lazy_until_first_request(monkeypatch):
    """Engine and session factory must not be created at import time.

    This is required for Vercel serverless: importing the app module should
    not attempt a database connection.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test_db")
    get_settings.cache_clear()

    import app.api as api_module

    # Reset lazy state so we can test the full cycle
    api_module._engine = None
    api_module._session_factory = None

    try:
        assert api_module._engine is None
        assert api_module._session_factory is None

        factory = api_module._get_session_factory()

        assert api_module._engine is not None
        assert api_module._session_factory is not None
        assert factory is api_module._session_factory
    finally:
        # Clean up: dispose engine and reset to None
        if api_module._engine is not None:
            api_module._engine.dispose()
        api_module._engine = None
        api_module._session_factory = None
        get_settings.cache_clear()
