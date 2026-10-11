import pytest

@pytest.fixture
def seed_explain_data(test_client):
    test_client.post("/employees", json={
        "emp_code": "EMP9001",
        "name": "Explain Worker",
        "email": "exp9001@example.com",
        "department": "Engineering",
        "joined_on": "2026-01-01"
    })

def test_explain_unsupported_endpoint(test_client):
    res = test_client.get("/admin/explain/unsupported_route")
    assert res.status_code == 422
    assert res.json() == {"detail": "unsupported endpoint"}

def test_explain_attendance_list(test_client, seed_explain_data):
    res = test_client.get("/admin/explain/attendance_list", params={"emp_code": "EMP9001"})
    assert res.status_code == 200
    data = res.json()
    assert data["endpoint"] == "attendance_list"
    assert data["collection"] == "attendance_logs"
    assert "explain" in data
    assert isinstance(data["explain"], dict)

def test_explain_employee_monthly(test_client, seed_explain_data):
    # Missing params -> 422
    assert test_client.get("/admin/explain/employee_monthly").status_code == 422
    res = test_client.get("/admin/explain/employee_monthly", params={"emp_code": "EMP9001", "month": "2026-10"})
    assert res.status_code == 200
    data = res.json()
    assert data["endpoint"] == "employee_monthly"
    assert data["collection"] == "attendance_logs"
    assert "explain" in data

def test_explain_department_summary(test_client, seed_explain_data):
    # Missing month -> 422
    assert test_client.get("/admin/explain/department_summary").status_code == 422
    res = test_client.get("/admin/explain/department_summary", params={"month": "2026-10", "department": "Engineering"})
    assert res.status_code == 200
    data = res.json()
    assert data["endpoint"] == "department_summary"
    assert data["collection"] == "employees"
    assert "explain" in data

def test_explain_late_leaderboard(test_client, seed_explain_data):
    # Missing month -> 422
    assert test_client.get("/admin/explain/late_leaderboard").status_code == 422
    res = test_client.get("/admin/explain/late_leaderboard", params={"month": "2026-10"})
    assert res.status_code == 200
    data = res.json()
    assert data["endpoint"] == "late_leaderboard"
    assert data["collection"] == "attendance_logs"
    assert "explain" in data

def test_explain_department_trend(test_client, seed_explain_data):
    # Missing params -> 422
    assert test_client.get("/admin/explain/department_trend").status_code == 422
    res = test_client.get("/admin/explain/department_trend", params={
        "department": "Engineering", "from": "2026-10-01", "to": "2026-10-07"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["endpoint"] == "department_trend"
    assert data["collection"] == "employees"
    assert "explain" in data
