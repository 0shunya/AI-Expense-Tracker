"""PostgreSQL-only behaviour. Run with:  TEST_DATABASE_URL=postgresql+psycopg://... pytest -m postgres

Uses a disposable database: the schema is dropped and recreated through Alembic.
"""

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

pytestmark = pytest.mark.postgres
URL = os.environ.get("TEST_DATABASE_URL", "")
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture()
def fresh_pg(monkeypatch):
    if not URL.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL is not a PostgreSQL URL")
    engine = create_engine(URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    engine.dispose()
    env = {**os.environ, "DATABASE_URL": URL}
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    monkeypatch.setenv("DATABASE_URL", URL)
    from app.config import get_settings
    from app.db import reset_engine
    get_settings.cache_clear()
    reset_engine()
    yield URL
    reset_engine()
    get_settings.cache_clear()


def test_migrations_create_full_schema_on_fresh_database(fresh_pg):
    engine = create_engine(fresh_pg)
    tables = set(inspect(engine).get_table_names())
    assert {"receipts", "extractions", "extraction_fields", "line_items", "validation_checks", "corrections",
            "review_decisions", "expenses", "model_calls", "stored_files", "alembic_version"} <= tables
    with engine.connect() as conn:
        assert conn.scalar(text("select version_num from alembic_version")) == "0001"
    engine.dispose()


def test_database_constraints_enforce_rules(fresh_pg):
    from sqlalchemy.exc import IntegrityError
    engine = create_engine(fresh_pg)
    base = ("insert into receipts (id, original_filename, content_type, size_bytes, sha256, storage_backend, "
            "storage_key, status, processing_attempts, review_reasons, duplicate_status, duplicate_reasons, "
            "created_at, updated_at, currency) values (gen_random_uuid(), 'a', 'image/png', 10, :sha, 'database', 'k', "
            ":status, 0, '[]', 'none', '[]', now(), now(), :cur)")
    with engine.begin() as conn:
        conn.execute(text(base), {"sha": "a" * 64, "status": "queued", "cur": "USD"})
    for params in ({"sha": "a" * 64, "status": "queued", "cur": "USD"},      # duplicate sha256
                   {"sha": "b" * 64, "status": "bogus", "cur": "USD"},       # unknown status
                   {"sha": "c" * 64, "status": "queued", "cur": "usd"}):     # lowercase currency
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(base), params)
    engine.dispose()


def test_concurrent_workers_never_claim_the_same_receipt(fresh_pg):
    from app.db import session_scope
    from app.services.pipeline import claim_next, ingest_upload
    from tests.factories import image_bytes

    with session_scope() as db:
        for i in range(6):
            ingest_upload(db, image_bytes(text=f"r{i}"), f"r{i}.png")
    claimed: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(6)

    def worker():
        barrier.wait()
        with session_scope() as db:
            receipt_id = claim_next(db)
        with lock:
            claimed.append(receipt_id)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = [c for c in claimed if c is not None]
    assert len(ids) == len(set(ids)) == 6


def test_expired_lease_is_reclaimed_and_poison_job_stops(fresh_pg, settings_override):
    from datetime import timedelta

    from app.db import session_scope
    from app.models import Receipt, utcnow
    from app.services.pipeline import claim_next, ingest_upload
    from tests.factories import image_bytes

    settings_override(max_job_attempts="2", database_url=fresh_pg)
    with session_scope() as db:
        rid = ingest_upload(db, image_bytes(text="lease"), "lease.png").receipt.id
    for _ in range(2):
        with session_scope() as db:
            assert claim_next(db) == rid
            db.get(Receipt, rid).locked_at = utcnow() - timedelta(hours=1)  # simulate a crashed worker
    with session_scope() as db:
        assert claim_next(db) is None
        receipt = db.get(Receipt, rid)
        assert receipt.status == "failed" and receipt.error_code == "abandoned"
