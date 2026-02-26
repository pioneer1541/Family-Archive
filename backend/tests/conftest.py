import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

TEST_DB_PATH = ROOT_DIR / ".test_family_vault.db"
os.environ["FAMILY_VAULT_DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"
os.environ["FAMILY_VAULT_CELERY_TASK_ALWAYS_EAGER"] = "1"
os.environ["FAMILY_VAULT_QDRANT_ENABLE"] = "0"
os.environ["FAMILY_VAULT_NAS_AUTO_SCAN_ENABLED"] = "0"
os.environ["FAMILY_VAULT_MAIL_POLL_ENABLED"] = "0"

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def reset_database(request):
    if request.node.get_closest_marker("no_db_reset"):
        yield
        return
    try:
        Base.metadata.drop_all(bind=engine)
    except OperationalError:
        pass
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
