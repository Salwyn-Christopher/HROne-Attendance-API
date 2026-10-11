import pytest
from unittest.mock import patch
from pymongo.errors import ServerSelectionTimeoutError
import app.main as app_module

def test_health_success(test_client):
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_health_failure_returns_503(test_client):
    with patch.object(app_module.db, "command", side_effect=ServerSelectionTimeoutError("unreachable")):
        response = test_client.get("/health")
        assert response.status_code == 503
        assert response.json() == {"detail": "database unavailable"}
