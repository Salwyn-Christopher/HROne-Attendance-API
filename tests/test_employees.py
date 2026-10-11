import pytest
from concurrent.futures import ThreadPoolExecutor

def test_create_employee_success(test_client):
    payload = {
        "emp_code": "EMP1001",
        "name": "Alice Johnson",
        "email": "alice@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-15"
    }
    res = test_client.post("/employees", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["emp_code"] == "EMP1001"
    assert data["name"] == "Alice Johnson"
    assert data["email"] == "alice@example.com"
    assert data["department"] == "Engineering"
    assert data["shift_start"] == "09:30"
    assert data["shift_end"] == "18:30"
    assert data["joined_on"] == "2026-01-15"
    assert "_id" not in data
    assert isinstance(data["created_at"], int)

def test_create_employee_defaults(test_client):
    payload = {
        "emp_code": "EMP1002",
        "name": "Bob Smith",
        "email": "bob@example.com",
        "department": "Sales",
        "joined_on": "2026-02-01"
    }
    res = test_client.post("/employees", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["shift_start"] == "09:30"
    assert data["shift_end"] == "18:30"

def test_create_employee_extra_fields_forbidden(test_client):
    payload = {
        "emp_code": "EMP1003",
        "name": "Charlie",
        "email": "charlie@example.com",
        "department": "HR",
        "joined_on": "2026-01-01",
        "extra_field": "disallowed"
    }
    res = test_client.post("/employees", json=payload)
    assert res.status_code == 422

@pytest.mark.parametrize("invalid_code", ["EMP1", "EMP12", "EMP123", "emp1001", "EMP1234567", "1001", "USER1001"])
def test_create_employee_invalid_code(test_client, invalid_code):
    payload = {
        "emp_code": invalid_code,
        "name": "Invalid Code",
        "email": "invalid@example.com",
        "department": "HR",
        "joined_on": "2026-01-01"
    }
    res = test_client.post("/employees", json=payload)
    assert res.status_code == 422

@pytest.mark.parametrize("invalid_email", ["notanemail", "user@", "@domain.com", "user@domain"])
def test_create_employee_invalid_email(test_client, invalid_email):
    payload = {
        "emp_code": "EMP1004",
        "name": "Bad Email",
        "email": invalid_email,
        "department": "HR",
        "joined_on": "2026-01-01"
    }
    res = test_client.post("/employees", json=payload)
    assert res.status_code == 422

def test_create_employee_equal_shifts_rejected(test_client):
    payload = {
        "emp_code": "EMP1005",
        "name": "Equal Shifts",
        "email": "equal@example.com",
        "department": "HR",
        "shift_start": "10:00",
        "shift_end": "10:00",
        "joined_on": "2026-01-01"
    }
    res = test_client.post("/employees", json=payload)
    assert res.status_code == 422

@pytest.mark.parametrize("bad_date", ["2026-02-30", "2026-13-01", "not-a-date", "2026/01/01", "26-01-01"])
def test_create_employee_invalid_joined_on(test_client, bad_date):
    payload = {
        "emp_code": "EMP1006",
        "name": "Bad Date",
        "email": "baddate@example.com",
        "department": "HR",
        "joined_on": bad_date
    }
    res = test_client.post("/employees", json=payload)
    assert res.status_code == 422

def test_create_employee_duplicate_code_conflict(test_client):
    payload = {
        "emp_code": "EMP1007",
        "name": "First User",
        "email": "user1@example.com",
        "department": "HR",
        "joined_on": "2026-01-01"
    }
    res1 = test_client.post("/employees", json=payload)
    assert res1.status_code == 201

    res2 = test_client.post("/employees", json=payload)
    assert res2.status_code == 409
    assert res2.json() == {"detail": "emp_code already exists"}

def test_create_employee_concurrency_race(test_client):
    payload = {
        "emp_code": "EMP1008",
        "name": "Concurrent User",
        "email": "concurrent@example.com",
        "department": "HR",
        "joined_on": "2026-01-01"
    }
    def do_post():
        return test_client.post("/employees", json=payload).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: do_post(), range(2)))

    assert 201 in results
    assert 409 in results

def test_list_employees_pagination_and_filter(test_client):
    # Seed 5 employees in Engineering, 3 in Marketing
    for i in range(1, 6):
        test_client.post("/employees", json={
            "emp_code": f"EMP{2000+i}",
            "name": f"Engineer {i}",
            "email": f"eng{i}@example.com",
            "department": "Engineering",
            "joined_on": "2026-01-01"
        })
    for i in range(1, 4):
        test_client.post("/employees", json={
            "emp_code": f"EMP{3000+i}",
            "name": f"Marketer {i}",
            "email": f"mkt{i}@example.com",
            "department": "Marketing",
            "joined_on": "2026-01-01"
        })

    # List all
    res_all = test_client.get("/employees", params={"page": 1, "page_size": 10})
    assert res_all.status_code == 200
    data_all = res_all.json()
    assert data_all["total"] == 8
    assert len(data_all["items"]) == 8
    # Assert sorted by emp_code ASC
    codes = [d["emp_code"] for d in data_all["items"]]
    assert codes == sorted(codes)

    # Filter department Engineering
    res_eng = test_client.get("/employees", params={"department": "Engineering", "page": 1, "page_size": 3})
    assert res_eng.status_code == 200
    data_eng = res_eng.json()
    assert data_eng["total"] == 5
    assert len(data_eng["items"]) == 3
    assert data_eng["page"] == 1
    assert data_eng["page_size"] == 3

    # Page 2
    res_eng_p2 = test_client.get("/employees", params={"department": "Engineering", "page": 2, "page_size": 3})
    assert res_eng_p2.status_code == 200
    data_eng_p2 = res_eng_p2.json()
    assert len(data_eng_p2["items"]) == 2

    # Check bounds validation
    assert test_client.get("/employees", params={"page": 0}).status_code == 422
    assert test_client.get("/employees", params={"page": 100001}).status_code == 422
    assert test_client.get("/employees", params={"page": 6703743605062011702589576445952}).status_code == 422
    assert test_client.get("/employees", params={"page_size": 0}).status_code == 422
    assert test_client.get("/employees", params={"page_size": 101}).status_code == 422
