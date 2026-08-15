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
