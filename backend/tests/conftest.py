import os
import sys
from pathlib import Path

# Configure before importing the app: isolated SQLite DB, no worker thread, and never a real API key.
os.environ.update({
    "APP_ENV": "test",
    "GEMINI_API_KEY": "",
    "WORKER_MODE": "off",
    "STORAGE_BACKEND": "database",
    "DEMO_ACCESS_CODE": "",
    "DAILY_MODEL_CALL_LIMIT": "0",
    "AUTO_APPROVE_CLEAN_RECEIPTS": "false",
    "EXTRACTION_PASSES": "1",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base, get_engine, reset_engine  # noqa: E402


@pytest.fixture()
def database(tmp_path, monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL") if os.environ.get("USE_TEST_POSTGRES") else None
    monkeypatch.setenv("DATABASE_URL", url or f"sqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()
    reset_engine()
    from app import models  # noqa: F401
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture()
def client(database):
    from fastapi.testclient import TestClient

    from app.api.ratelimit import upload_limiter
    from app.main import create_app

    upload_limiter.reset()
    with TestClient(create_app(start_worker=False)) as test_client:
        yield test_client


@pytest.fixture()
def settings_override(monkeypatch):
    def apply(**values):
        for key, value in values.items():
            monkeypatch.setenv(key.upper(), str(value))
        get_settings.cache_clear()
    yield apply
    get_settings.cache_clear()
