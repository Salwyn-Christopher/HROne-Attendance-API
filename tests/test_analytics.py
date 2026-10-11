import pytest
from datetime import datetime, timezone, timedelta
import app.main as app_module

IST = timezone(timedelta(hours=5, minutes=30))

def to_ms(dt: datetime) -> int:
    return int(dt.replace(microsecond=0).timestamp() * 1000)

@pytest.fixture
def seed_analytics_data(test_client):
    # Setup department 'Engineering' with 3 employees:
    # EMP8001: joined 2026-09-01
    # EMP8002: joined 2026-10-15 (joined mid-month)
    # EMP8003: joined 2026-11-01 (joined after October)
    # Setup department 'Design' with 1 employee:
    # EMP8004: joined 2026-10-01
    test_client.post("/employees", json={
        "emp_code": "EMP8001", "name": "Eng Full", "email": "e1@example.com",
        "department": "Engineering", "joined_on": "2026-09-01"
    })
    test_client.post("/employees", json={
        "emp_code": "EMP8002", "name": "Eng Mid", "email": "e2@example.com",
        "department": "Engineering", "joined_on": "2026-10-15"
    })
    test_client.post("/employees", json={
        "emp_code": "EMP8003", "name": "Eng Late", "email": "e3@example.com",
        "department": "Engineering", "joined_on": "2026-11-01"
    })
    test_client.post("/employees", json={
        "emp_code": "EMP8004", "name": "Design One", "email": "d1@example.com",
        "department": "Design", "joined_on": "2026-10-01"
    })

    # Oct 2026 calendar:
    # Oct 1 (Thu) - Oct 2 (Fri) - Oct 3 (Sat) - Oct 4 (Sun) - Oct 5 (Mon)
    # Seed logs for EMP8001 in Oct 2026:
    # 2026-10-01 (Thu): PRESENT, on time, 8 hours (09:30 to 17:30)
    pin1 = datetime(2026, 10, 1, 9, 30, 0, tzinfo=IST)
    pout1 = datetime(2026, 10, 1, 17, 30, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP8001", "punched_at": to_ms(pin1)})
    test_client.post("/attendance/punch-out", json={"emp_code": "EMP8001", "punched_at": to_ms(pout1)})

    # 2026-10-02 (Fri): PRESENT, 15 min late (09:45 to 19:15 -> 45 min overtime, 9.5 hours)
    pin2 = datetime(2026, 10, 2, 9, 45, 0, tzinfo=IST)
    pout2 = datetime(2026, 10, 2, 19, 15, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP8001", "punched_at": to_ms(pin2)})
    test_client.post("/attendance/punch-out", json={"emp_code": "EMP8001", "punched_at": to_ms(pout2)})

    # 2026-10-03 (Sat): PRESENT on weekend (09:30 to 17:30) -> should NOT count to weekday present_days
    pin3 = datetime(2026, 10, 3, 9, 30, 0, tzinfo=IST)
    pout3 = datetime(2026, 10, 3, 17, 30, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP8001", "punched_at": to_ms(pin3)})
    test_client.post("/attendance/punch-out", json={"emp_code": "EMP8001", "punched_at": to_ms(pout3)})

    # 2026-10-05 (Mon): PRESENT half-day (09:30 to 13:00 -> 3.5 hrs -> half_day = True)
    pin4 = datetime(2026, 10, 5, 9, 30, 0, tzinfo=IST)
    pout4 = datetime(2026, 10, 5, 13, 0, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP8001", "punched_at": to_ms(pin4)})
    test_client.post("/attendance/punch-out", json={"emp_code": "EMP8001", "punched_at": to_ms(pout4)})

    # 2026-10-06 (Tue): LEAVE
    # Create via punch-in then regularize to LEAVE
    pin5 = datetime(2026, 10, 6, 9, 30, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP8001", "punched_at": to_ms(pin5)})
    res_patch = test_client.patch("/attendance/EMP8001/2026-10-06", json={"status": "LEAVE", "reason": "Sick day approval", "regularized_by": "admin"})
    assert res_patch.status_code == 200

    # Seed EMP8004 (Design):
    # 2026-10-01 (Thu): PRESENT ON_DUTY
    pind1 = datetime(2026, 10, 1, 9, 30, 0, tzinfo=IST)
    poutd1 = datetime(2026, 10, 1, 18, 0, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP8004", "punched_at": to_ms(pind1), "status": "ON_DUTY"})
    test_client.post("/attendance/punch-out", json={"emp_code": "EMP8004", "punched_at": to_ms(poutd1)})

def test_employee_monthly_analytics(test_client, seed_analytics_data):
    # EMP8001 in October 2026 (31 days, 22 working days):
    # Present weekdays: Oct 1 (1.0), Oct 2 (1.0), Oct 5 (half-day 0.5) -> present_days = 2.5
    # Weekend Oct 3 (Sat) is excluded from present_days
    # Leave days: Oct 6 (1.0)
    # Late count: 1 (Oct 2), total late minutes: 15
    # Overtime minutes: 45 (Oct 2)
    # Attendance pct: 2.5 / 22 * 100 = 11.363636... -> rounded to 4 decimals = 11.3636%
    res = test_client.get("/analytics/employees/EMP8001/monthly", params={"month": "2026-10"})
    assert res.status_code == 200
    data = res.json()
    assert data["emp_code"] == "EMP8001"
    assert data["month"] == "2026-10"
    assert data["working_days"] == 22
    assert data["present_days"] == 2.5
    assert data["leave_days"] == 1
    assert data["late_count"] == 1
    assert data["total_late_minutes"] == 15
    assert data["total_overtime_minutes"] == 45
    assert data["attendance_pct"] == 11.3636

def test_employee_monthly_joined_mid_month(test_client, seed_analytics_data):
    # EMP8002 joined 2026-10-15:
    # Working days from Oct 15 to Oct 31 in 2026:
    # Oct 15(Thu), 16(Fri), 19-23(5), 26-30(5) = 12 working days
    res = test_client.get("/analytics/employees/EMP8002/monthly", params={"month": "2026-10"})
    assert res.status_code == 200
    data = res.json()
    assert data["working_days"] == 12
    assert data["present_days"] == 0
    assert data["attendance_pct"] == 0.0

def test_employee_monthly_joined_after_month(test_client, seed_analytics_data):
    # EMP8003 joined 2026-11-01 -> 0 working days in October
    res = test_client.get("/analytics/employees/EMP8003/monthly", params={"month": "2026-10"})
    assert res.status_code == 200
    data = res.json()
    assert data["working_days"] == 0
    assert data["attendance_pct"] is None

def test_employee_monthly_validations(test_client):
    assert test_client.get("/analytics/employees/EMP9999/monthly", params={"month": "2026-10"}).status_code == 404
    assert test_client.get("/analytics/employees/EMP8001/monthly", params={"month": "2026-13"}).status_code == 422
    assert test_client.get("/analytics/employees/EMP8001/monthly", params={"month": "invalid"}).status_code == 422

def test_departments_summary(test_client, seed_analytics_data):
    # In October 2026:
    # Engineering headcount: EMP8001 (joined Sep 1) + EMP8002 (joined Oct 15) = 2
    # (EMP8003 joined Nov 1 so excluded)
    # Engineering present_days: 2.5
    # Engineering work_hours: Oct 1 (8.0) + Oct 2 (9.5) + Oct 3 (8.0) + Oct 5 (3.5) = 29.0 / 4 records = 7.25 avg
    # Design headcount: EMP8004 = 1
    res = test_client.get("/analytics/departments/summary", params={"month": "2026-10"})
    assert res.status_code == 200
    data = res.json()
    assert data["month"] == "2026-10"
    items = {it["department"]: it for it in data["items"]}

    assert "Engineering" in items
    eng = items["Engineering"]
    assert eng["headcount"] == 2
    assert eng["present_days"] == 2.5
    assert eng["avg_work_hours"] == 7.25
    assert eng["late_count"] == 1
    assert eng["total_late_minutes"] == 15
    assert eng["leave_count"] == 1

    assert "Design" in items
    des = items["Design"]
    assert des["headcount"] == 1
    assert des["on_duty_count"] == 1

    # Filter department Engineering
    res_eng = test_client.get("/analytics/departments/summary", params={"month": "2026-10", "department": "Engineering"})
    assert res_eng.status_code == 200
    assert len(res_eng.json()["items"]) == 1
    assert res_eng.json()["items"][0]["department"] == "Engineering"

def test_late_leaderboard_ranking_and_ties(test_client):
    # Create 3 employees
    for i in [1, 2, 3]:
        test_client.post("/employees", json={
            "emp_code": f"EMP801{i}", "name": f"Late User {i}", "email": f"late{i}@example.com",
            "department": "Engineering", "joined_on": "2026-01-01"
        })

    # EMP8011: Late 30 mins
    t1 = datetime(2026, 10, 1, 10, 0, 0, tzinfo=IST) # 30 min late
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP8011", "punched_at": to_ms(t1)})

    # EMP8012: Late 30 mins (TIED with EMP8011!)
    t2 = datetime(2026, 10, 1, 10, 0, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP8012", "punched_at": to_ms(t2)})

    # EMP8013: Late 15 mins (Rank 3)
    t3 = datetime(2026, 10, 1, 9, 45, 0, tzinfo=IST)
    test_client.post("/attendance/punch-in", json={"emp_code": "EMP8013", "punched_at": to_ms(t3)})

    # Query leaderboard with limit=1:
    # Both EMP8011 and EMP8012 have rank 1, so both should be included when rank <= 1!
    res = test_client.get("/analytics/leaderboard/late", params={"month": "2026-10", "limit": 1})
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 2
    assert items[0]["rank"] == 1
    assert items[1]["rank"] == 1
    # Sorted by emp_code ASC for tie: EMP8011 before EMP8012
    assert items[0]["emp_code"] == "EMP8011"
    assert items[1]["emp_code"] == "EMP8012"

    # Query limit=3
    res3 = test_client.get("/analytics/leaderboard/late", params={"month": "2026-10", "limit": 3})
    assert res3.status_code == 200
    items3 = res3.json()["items"]
    assert len(items3) == 3
    # $rank skips rank 2: ranks are 1, 1, 3
    assert items3[2]["rank"] == 3
    assert items3[2]["emp_code"] == "EMP8013"

def test_department_trend(test_client, seed_analytics_data):
    # Test trend for Engineering from Oct 1 to Oct 7 (7 days)
    res = test_client.get("/analytics/departments/Engineering/trend", params={"from": "2026-10-01", "to": "2026-10-07"})
    assert res.status_code == 200
    data = res.json()
    assert data["department"] == "Engineering"
    items = data["items"]
    assert len(items) == 7

    # Check date progression
    dates = [it["date"] for it in items]
    assert dates == ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07"]

    # Oct 1 (Thu, working day): headcount=1 (EMP8002 joined Oct 15), present_count=1 -> attendance_rate = 1.0
    assert items[0]["is_working_day"] is True
    assert items[0]["headcount"] == 1
    assert items[0]["present_count"] == 1.0
    assert items[0]["attendance_rate"] == 1.0

    # Oct 3 (Sat, non-working day): attendance_rate should be None
    assert items[2]["is_working_day"] is False
    assert items[2]["attendance_rate"] is None

    # Check 7d moving average is present
    assert "moving_avg_7d" in items[-1]

def test_department_trend_validations(test_client, seed_analytics_data):
    # Unknown department -> 404
    assert test_client.get("/analytics/departments/Finance/trend", params={"from": "2026-10-01", "to": "2026-10-05"}).status_code == 404
    # Reversed dates -> 422
    assert test_client.get("/analytics/departments/Engineering/trend", params={"from": "2026-10-05", "to": "2026-10-01"}).status_code == 422
    # Range > 92 days -> 422
    assert test_client.get("/analytics/departments/Engineering/trend", params={"from": "2026-01-01", "to": "2026-05-01"}).status_code == 422
