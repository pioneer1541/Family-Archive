import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

TEST_DB_PATH = ROOT_DIR / ".test_family_vault.db"
os.environ["FAMILY_VAULT_DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"
os.environ["FAMILY_VAULT_JWT_SECRET"] = "test-jwt-secret-not-for-production-use"
os.environ["FAMILY_VAULT_CELERY_TASK_ALWAYS_EAGER"] = "1"
os.environ["FAMILY_VAULT_QDRANT_ENABLE"] = "0"
os.environ["FAMILY_VAULT_NAS_AUTO_SCAN_ENABLED"] = "0"
os.environ["FAMILY_VAULT_MAIL_POLL_ENABLED"] = "0"

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


def _wal_checkpoint() -> None:
    """强制 WAL checkpoint，确保所有事务已落盘，避免 SQLite 状态残留。"""
    with engine.connect() as _conn:
        try:
            _conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            _conn.commit()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def reset_database(request):
    if request.node.get_closest_marker("no_db_reset"):
        yield
        return
    try:
        Base.metadata.drop_all(bind=engine)
    except OperationalError:
        pass
    # 强制 WAL checkpoint，防止上一测试残留的事务锁导致 create_all 冲突
    _wal_checkpoint()
    try:
        Base.metadata.create_all(bind=engine)
    except OperationalError:
        # create_all 遇到残留表时（drop_all 未完全成功），再做一次 drop → create
        try:
            Base.metadata.drop_all(bind=engine)
        except Exception:
            pass
        _wal_checkpoint()
        Base.metadata.create_all(bind=engine)
    yield
    try:
        Base.metadata.drop_all(bind=engine)
    except OperationalError:
        pass


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
