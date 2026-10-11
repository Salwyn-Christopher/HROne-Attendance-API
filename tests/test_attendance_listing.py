import pytest
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

def to_ms(dt: datetime) -> int:
    return int(dt.replace(microsecond=0).timestamp() * 1000)

@pytest.fixture
def seed_attendance_records(test_client):
    # Create two employees
    for i in [1, 2]:
        test_client.post("/employees", json={
            "emp_code": f"EMP600{i}",
            "name": f"Worker {i}",
            "email": f"worker600{i}@example.com",
            "department": "Engineering",
            "joined_on": "2026-01-01"
        })

    # Employee 1 punches in for Oct 1, Oct 2, Oct 3
    for d in [1, 2, 3]:
        t = datetime(2026, 10, d, 9, 30, 0, tzinfo=IST)
        test_client.post("/attendance/punch-in", json={"emp_code": "EMP6001", "punched_at": to_ms(t)})

    # Employee 2 punches in for Oct 2, Oct 3 (WFH)
    t = datetime(2026, 10, 2, 9, 30, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP6002", "punched_at": to_ms(t), "status": "WFH"})
    t = datetime(2026, 10, 3, 9, 30, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP6002", "punched_at": to_ms(t)})

def test_list_attendance_all(test_client, seed_attendance_records):
    res = test_client.get("/attendance", params={"page": 1, "page_size": 10})
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 5
    items = data["items"]
    assert len(items) == 5

    # Check sort order: date DESC, emp_code ASC
    keys = [(item["date"], item["emp_code"]) for item in items]
    expected_keys = sorted(keys, key=lambda k: (-int(k[0].replace("-", "")), k[1]))
    assert keys == expected_keys

    # Check fields
    for item in items:
        assert "_id" not in item
        assert isinstance(item["punch_in"], int)
        assert item["half_day"] in (True, False)
        assert isinstance(item["history"], list)

def test_list_attendance_filter_emp_code(test_client, seed_attendance_records):
    res = test_client.get("/attendance", params={"emp_code": "EMP6001"})
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 3
    for item in data["items"]:
        assert item["emp_code"] == "EMP6001"

def test_list_attendance_filter_status(test_client, seed_attendance_records):
    res = test_client.get("/attendance", params={"status": "WFH"})
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["emp_code"] == "EMP6002"
    assert data["items"][0]["status"] == "WFH"

def test_list_attendance_filter_date_range(test_client, seed_attendance_records):
    res = test_client.get("/attendance", params={"date_from": "2026-10-02", "date_to": "2026-10-02"})
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert {item["emp_code"] for item in data["items"]} == {"EMP6001", "EMP6002"}

def test_list_attendance_invalid_filters(test_client):
    # date_from > date_to
    assert test_client.get("/attendance", params={"date_from": "2026-10-05", "date_to": "2026-10-01"}).status_code == 422
    # Invalid date
    assert test_client.get("/attendance", params={"date_from": "2026-02-30"}).status_code == 422
    assert test_client.get("/attendance", params={"date_to": "invalid"}).status_code == 422
    # Invalid status
    assert test_client.get("/attendance", params={"status": "VACATION"}).status_code == 422
    # Pagination bounds
    assert test_client.get("/attendance", params={"page": 0}).status_code == 422
    assert test_client.get("/attendance", params={"page": 100001}).status_code == 422
    assert test_client.get("/attendance", params={"page": 6703743605062011702589576445952}).status_code == 422
    assert test_client.get("/attendance", params={"page_size": 0}).status_code == 422
    assert test_client.get("/attendance", params={"page_size": 101}).status_code == 422
