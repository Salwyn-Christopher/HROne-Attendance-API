import pytest
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

IST = timezone(timedelta(hours=5, minutes=30))

def to_ms(dt: datetime) -> int:
    return int(dt.replace(microsecond=0).timestamp() * 1000)

@pytest.fixture
def seed_employee(test_client):
    res = test_client.post("/employees", json={
        "emp_code": "EMP4001",
        "name": "Standard Shift Worker",
        "email": "standard@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-01"
    })
    assert res.status_code == 201
    return res.json()

@pytest.fixture
def seed_night_employee(test_client):
    res = test_client.post("/employees", json={
        "emp_code": "EMP4002",
        "name": "Night Shift Worker",
        "email": "night@example.com",
        "department": "Operations",
        "shift_start": "22:00",
        "shift_end": "06:00",
        "joined_on": "2026-01-01"
    })
    assert res.status_code == 201
    return res.json()

def test_punch_in_unknown_employee(test_client):
    res = test_client.post("/attendance/punch-in", json={"emp_code": "EMP9999"})
    assert res.status_code == 404
    assert res.json() == {"detail": "unknown employee"}

def test_punch_in_on_time_and_grace(test_client, seed_employee):
    # 09:30:00 IST -> late_minutes = 0
    t1 = datetime(2026, 10, 1, 9, 30, 0, tzinfo=IST)
    res1 = test_client.post("/attendance/punch-in", json={
        "emp_code": "EMP4001",
        "punched_at": to_ms(t1)
    })
    assert res1.status_code == 201
    data1 = res1.json()
    assert data1["date"] == "2026-10-01"
    assert data1["status"] == "PRESENT"
    assert data1["late_minutes"] == 0
    assert data1["punch_out"] is None
    assert data1["work_hours"] is None
    assert data1["half_day"] is False
    assert data1["history"] == []
    assert "_id" not in data1

def test_punch_in_grace_period_boundaries(test_client):
    # Create distinct employees for each test date
    test_client.post("/employees", json={
        "emp_code": "EMP4010",
        "name": "Grace Tester",
        "email": "grace@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-01"
    })
    # Day 1: Exactly 09:40:00 (10 min boundary, grace allowed -> late = 0)
    t_exact_grace = datetime(2026, 10, 2, 9, 40, 0, tzinfo=IST)
    res = test_client.post("/attendance/punch-in", json={
        "emp_code": "EMP4010",
        "punched_at": to_ms(t_exact_grace)
    })
    assert res.status_code == 201
    assert res.json()["late_minutes"] == 0

    # Day 2: 09:40:00.999 (should truncate to 09:40:00 -> late = 0)
    t_subsecond = int(datetime(2026, 10, 3, 9, 40, 0, tzinfo=IST).timestamp() * 1000) + 999
    res = test_client.post("/attendance/punch-in", json={
        "emp_code": "EMP4010",
        "punched_at": t_subsecond
    })
    assert res.status_code == 201
    assert res.json()["late_minutes"] == 0

    # Day 3: 09:40:01 (601 seconds > 600 seconds -> late = 10 minutes)
    t_over_grace = datetime(2026, 10, 4, 9, 40, 1, tzinfo=IST)
    res = test_client.post("/attendance/punch-in", json={
        "emp_code": "EMP4010",
        "punched_at": to_ms(t_over_grace)
    })
    assert res.status_code == 201
    assert res.json()["late_minutes"] == 10

    # Day 4: 09:45:00 (15 minutes late)
    t_15m = datetime(2026, 10, 5, 9, 45, 0, tzinfo=IST)
    res = test_client.post("/attendance/punch-in", json={
        "emp_code": "EMP4010",
        "punched_at": to_ms(t_15m)
    })
    assert res.status_code == 201
    assert res.json()["late_minutes"] == 15

def test_punch_in_overnight_shift(test_client, seed_night_employee):
    # Overnight shift: 22:00 to 06:00
    # Punch in at 22:05 on 2026-10-10 -> date should be 2026-10-10
    t1 = datetime(2026, 10, 10, 22, 5, 0, tzinfo=IST)
    res1 = test_client.post("/attendance/punch-in", json={
        "emp_code": "EMP4002",
        "punched_at": to_ms(t1)
    })
    assert res1.status_code == 201
    assert res1.json()["date"] == "2026-10-10"
    assert res1.json()["late_minutes"] == 0

    # Punch in for next shift: late punch-in at 01:15 AM on 2026-10-12
    # Should attribute to 2026-10-11 shift date!
    t2 = datetime(2026, 10, 12, 1, 15, 0, tzinfo=IST)
    res2 = test_client.post("/attendance/punch-in", json={
        "emp_code": "EMP4002",
        "punched_at": to_ms(t2)
    })
    assert res2.status_code == 201
    assert res2.json()["date"] == "2026-10-11"
    # From 22:00 on Oct 11 to 01:15 on Oct 12 is 3 hours 15 min = 195 min
    assert res2.json()["late_minutes"] == 195

def test_punch_in_status_options(test_client):
    test_client.post("/employees", json={
        "emp_code": "EMP4020",
        "name": "Status Tester",
        "email": "status@example.com",
        "department": "Engineering",
        "joined_on": "2026-01-01"
    })
    for idx, st in enumerate(["WFH", "ON_DUTY"]):
        t = datetime(2026, 10, 1 + idx, 9, 30, 0, tzinfo=IST)
        res = test_client.post("/attendance/punch-in", json={
            "emp_code": "EMP4020",
            "punched_at": to_ms(t),
            "status": st
        })
        assert res.status_code == 201
        assert res.json()["status"] == st

@pytest.mark.parametrize("invalid_status", ["ABSENT", "LEAVE", "HOLIDAY", "UNKNOWN"])
def test_punch_in_invalid_status(test_client, seed_employee, invalid_status):
    res = test_client.post("/attendance/punch-in", json={
        "emp_code": "EMP4001",
        "status": invalid_status
    })
    assert res.status_code == 422

@pytest.mark.parametrize("bad_ts", [0, -1, 12345, "invalid", 999999999999999999])
def test_punch_in_invalid_timestamp(test_client, seed_employee, bad_ts):
    res = test_client.post("/attendance/punch-in", json={
        "emp_code": "EMP4001",
        "punched_at": bad_ts
    })
    assert res.status_code == 422

def test_punch_in_duplicate_conflict(test_client, seed_employee):
    t = datetime(2026, 10, 20, 9, 30, 0, tzinfo=IST)
    res1 = test_client.post("/attendance/punch-in", json={
        "emp_code": "EMP4001",
        "punched_at": to_ms(t)
    })
    assert res1.status_code == 201

    res2 = test_client.post("/attendance/punch-in", json={
        "emp_code": "EMP4001",
        "punched_at": to_ms(t)
    })
    assert res2.status_code == 409
    assert res2.json() == {"detail": "already punched in for this attendance date"}

def test_punch_in_concurrency_race(test_client, seed_employee):
    t = datetime(2026, 10, 25, 9, 30, 0, tzinfo=IST)
    def do_punch():
        return test_client.post("/attendance/punch-in", json={
            "emp_code": "EMP4001",
            "punched_at": to_ms(t)
        }).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: do_punch(), range(2)))

    assert 201 in results
    assert 409 in results
