import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Configure dedicated test database
TEST_DB_NAME = "hrone_attendance_pytest"
os.environ["MONGO_DB"] = TEST_DB_NAME

import pytest
from fastapi.testclient import TestClient
import app.main as app_module

@pytest.fixture(scope="session")
def test_client():
    # Point app db to the dedicated test database
    app_module.db = app_module.client[TEST_DB_NAME]
    app_module.ensure_indexes()
    with TestClient(app_module.app) as client:
        yield client
    # Clean up test database after session
    app_module.client.drop_database(TEST_DB_NAME)

@pytest.fixture(autouse=True)
def clean_db():
    app_module.db = app_module.client[TEST_DB_NAME]
    app_module.ensure_indexes()
    app_module.db.employees.delete_many({})
    app_module.db.attendance_logs.delete_many({})
    yield
    app_module.db.employees.delete_many({})
    app_module.db.attendance_logs.delete_many({})
