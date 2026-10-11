import pytest
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

IST = timezone(timedelta(hours=5, minutes=30))

def to_ms(dt: datetime) -> int:
    return int(dt.replace(microsecond=0).timestamp() * 1000)

@pytest.fixture
def seed_employee(test_client):
    res = test_client.post("/employees", json={
        "emp_code": "EMP5001",
        "name": "Punch Out Worker",
        "email": "worker5001@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-01"
    })
    assert res.status_code == 201
    return res.json()

def test_punch_out_unknown_employee(test_client):
    res = test_client.post("/attendance/punch-out", json={"emp_code": "EMP9999"})
    assert res.status_code == 404
    assert res.json() == {"detail": "unknown employee"}

def test_punch_out_no_punch_in(test_client, seed_employee):
    res = test_client.post("/attendance/punch-out", json={"emp_code": "EMP5001"})
    assert res.status_code == 404
    assert res.json() == {"detail": "no punch-in found"}

def test_punch_out_work_hours_half_up_rounding(test_client, seed_employee):
    # Punch in at 09:30:00
    pin = datetime(2026, 10, 1, 9, 30, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={
        "emp_code": "EMP5001",
        "punched_at": to_ms(pin)
    })
    # Punch out at 10:37:30 (1 hr 7 min 30 sec = 4050s / 3600s = 1.125 hours)
    # ROUND_HALF_UP rounds 1.125 to 1.13
    pout = datetime(2026, 10, 1, 10, 37, 30, tzinfo=IST)
    res = test_client.post("/attendance/punch-out", json={
        "emp_code": "EMP5001",
        "punched_at": to_ms(pout)
    })
    assert res.status_code == 200
    data = res.json()
    assert data["work_hours"] == 1.13
    assert data["half_day"] is True  # < 4.5 hours

def test_punch_out_half_day_boundary(test_client, seed_employee):
    # Exactly 4.50 hours: 09:30 to 14:00 (4.5 hours -> half_day = False)
    pin = datetime(2026, 10, 2, 9, 30, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={
        "emp_code": "EMP5001",
        "punched_at": to_ms(pin)
    })
    pout = datetime(2026, 10, 2, 14, 0, 0, tzinfo=IST)
    res = test_client.post("/attendance/punch-out", json={
        "emp_code": "EMP5001",
        "punched_at": to_ms(pout)
    })
    assert res.status_code == 200
    data = res.json()
    assert data["work_hours"] == 4.5
    assert data["half_day"] is False

def test_punch_out_overtime_thresholds(test_client):
    test_client.post("/employees", json={
        "emp_code": "EMP5010",
        "name": "Overtime Tester",
        "email": "ot@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-01"
    })
    # Day 1: Punch out at 18:59 (29 min after shift end -> overtime = 0)
    pin1 = datetime(2026, 10, 3, 9, 30, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP5010", "punched_at": to_ms(pin1)})
    pout1 = datetime(2026, 10, 3, 18, 59, 0, tzinfo=IST)
    res1 = test_client.post("/attendance/punch-out", json={"emp_code": "EMP5010", "punched_at": to_ms(pout1)})
    assert res1.status_code == 200
    assert res1.json()["overtime_minutes"] == 0

    # Day 2: Punch out at 19:00 (30 min after shift end -> overtime = 30)
    pin2 = datetime(2026, 10, 4, 9, 30, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP5010", "punched_at": to_ms(pin2)})
    pout2 = datetime(2026, 10, 4, 19, 0, 0, tzinfo=IST)
    res2 = test_client.post("/attendance/punch-out", json={"emp_code": "EMP5010", "punched_at": to_ms(pout2)})
    assert res2.status_code == 200
    assert res2.json()["overtime_minutes"] == 30

    # Day 3: Punch out at 19:15 (45 min after shift end -> overtime = 45)
    pin3 = datetime(2026, 10, 5, 9, 30, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP5010", "punched_at": to_ms(pin3)})
    pout3 = datetime(2026, 10, 5, 19, 15, 0, tzinfo=IST)
    res3 = test_client.post("/attendance/punch-out", json={"emp_code": "EMP5010", "punched_at": to_ms(pout3)})
    assert res3.status_code == 200
    assert res3.json()["overtime_minutes"] == 45

def test_punch_out_overnight_shift(test_client):
    test_client.post("/employees", json={
        "emp_code": "EMP5020",
        "name": "Night Overtime Tester",
        "email": "not@example.com",
        "department": "Operations",
        "shift_start": "22:00",
        "shift_end": "06:00",
        "joined_on": "2026-01-01"
    })
    # Punch in at 22:00 on Oct 6
    pin = datetime(2026, 10, 6, 22, 0, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP5020", "punched_at": to_ms(pin)})
    # Punch out at 06:40 on Oct 7 (40 min after 06:00 end -> overtime = 40)
    pout = datetime(2026, 10, 7, 6, 40, 0, tzinfo=IST)
    res = test_client.post("/attendance/punch-out", json={"emp_code": "EMP5020", "punched_at": to_ms(pout)})
    assert res.status_code == 200
    data = res.json()
    assert data["date"] == "2026-10-06"
    assert data["work_hours"] == 8.67
    assert data["overtime_minutes"] == 40
    assert data["half_day"] is False

def test_punch_out_validations(test_client, seed_employee):
    pin = datetime(2026, 10, 8, 9, 30, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP5001", "punched_at": to_ms(pin)})

    # Punch out before punch in -> 422
    pout_before = datetime(2026, 10, 8, 9, 0, 0, tzinfo=IST)
    res_before = test_client.post("/attendance/punch-out", json={"emp_code": "EMP5001", "punched_at": to_ms(pout_before)})
    assert res_before.status_code == 422

    # Punch out equal to punch in -> 422
    res_equal = test_client.post("/attendance/punch-out", json={"emp_code": "EMP5001", "punched_at": to_ms(pin)})
    assert res_equal.status_code == 422

    # Punch out > 24 hours -> 422
    pout_over_24h = datetime(2026, 10, 9, 9, 31, 0, tzinfo=IST)
    res_24h = test_client.post("/attendance/punch-out", json={"emp_code": "EMP5001", "punched_at": to_ms(pout_over_24h)})
    assert res_24h.status_code == 422

    # Valid punch out
    pout_valid = datetime(2026, 10, 8, 18, 30, 0, tzinfo=IST)
    res_valid = test_client.post("/attendance/punch-out", json={"emp_code": "EMP5001", "punched_at": to_ms(pout_valid)})
    assert res_valid.status_code == 200

    # Repeat punch out on closed record -> 409
    res_repeat = test_client.post("/attendance/punch-out", json={"emp_code": "EMP5001", "punched_at": to_ms(pout_valid)})
    assert res_repeat.status_code == 409

def test_punch_out_concurrency_race(test_client, seed_employee):
    pin = datetime(2026, 10, 10, 9, 30, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP5001", "punched_at": to_ms(pin)})

    pout = datetime(2026, 10, 10, 18, 30, 0, tzinfo=IST)
    def do_punch_out():
        return test_client.post("/attendance/punch-out", json={"emp_code": "EMP5001", "punched_at": to_ms(pout)}).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: do_punch_out(), range(2)))

    assert 200 in results
    assert 409 in results
