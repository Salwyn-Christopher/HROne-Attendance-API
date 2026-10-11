import pytest
from datetime import datetime, timezone, timedelta
import app.main as app_module

IST = timezone(timedelta(hours=5, minutes=30))

def to_ms(dt: datetime) -> int:
    return int(dt.replace(microsecond=0).timestamp() * 1000)

@pytest.fixture
def seed_record(test_client):
    test_client.post("/employees", json={
        "emp_code": "EMP7001",
        "name": "Regularize Worker",
        "email": "reg7001@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-01"
    })
    pin = datetime(2026, 10, 1, 9, 30, 0, tzinfo=IST)
    res = test_client.post("/attendance/punch-in", json={"emp_code": "EMP7001", "punched_at": to_ms(pin)})
    assert res.status_code == 201
    return res.json()

def test_regularize_unknown_employee(test_client):
    res = test_client.patch("/attendance/EMP9999/2026-10-01", json={
        "reason": "Correcting attendance",
        "regularized_by": "admin"
    })
    assert res.status_code == 404
    assert res.json() == {"detail": "unknown employee"}

def test_regularize_record_not_found(test_client, seed_record):
    res = test_client.patch("/attendance/EMP7001/2026-10-05", json={
        "reason": "Correcting attendance",
        "regularized_by": "admin"
    })
    assert res.status_code == 404
    assert res.json() == {"detail": "attendance record not found"}

def test_regularize_to_leave(test_client, seed_record):
    res = test_client.patch("/attendance/EMP7001/2026-10-01", json={
        "status": "LEAVE",
        "reason": "Sick leave approved",
        "regularized_by": "manager1"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "LEAVE"
    assert data["punch_in"] is None
    assert data["punch_out"] is None
    assert data["work_hours"] is None
    assert data["late_minutes"] == 0
    assert data["overtime_minutes"] == 0
    assert data["half_day"] is False
    assert len(data["history"]) == 1
    hist = data["history"][0]
    assert hist["by"] == "manager1"
    assert hist["reason"] == "Sick leave approved"
    assert "status" in hist["changes"]
    assert hist["changes"]["status"] == {"from": "PRESENT", "to": "LEAVE"}
    assert "punch_in" in hist["changes"]
    assert isinstance(hist["changes"]["punch_in"]["from"], int)
    assert hist["changes"]["punch_in"]["to"] is None

def test_regularize_leave_with_punch_times_rejected(test_client, seed_record):
    pin = datetime(2026, 10, 1, 9, 30, 0, tzinfo=IST)
    res = test_client.patch("/attendance/EMP7001/2026-10-01", json={
        "status": "LEAVE",
        "punch_in": to_ms(pin),
        "reason": "Mistaken punches",
        "regularized_by": "manager1"
    })
    assert res.status_code == 422
    assert res.json() == {"detail": "ABSENT/LEAVE cannot include punch times"}

def test_regularize_adjust_times_and_recalculate(test_client, seed_record):
    # Adjust punch_in to 09:45 (15 min late) and punch_out to 19:15 (45 min overtime)
    pin = datetime(2026, 10, 1, 9, 45, 0, tzinfo=IST)
    pout = datetime(2026, 10, 1, 19, 15, 0, tzinfo=IST)
    res = test_client.patch("/attendance/EMP7001/2026-10-01", json={
        "punch_in": to_ms(pin),
        "punch_out": to_ms(pout),
        "reason": "Punch regularized with manager approval",
        "regularized_by": "hr_admin"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["late_minutes"] == 15
    assert data["overtime_minutes"] == 45
    assert data["work_hours"] == 9.5
    assert data["half_day"] is False
    assert len(data["history"]) == 1
    changes = data["history"][0]["changes"]
    assert "late_minutes" in changes
    assert changes["late_minutes"] == {"from": 0, "to": 15}
    assert "overtime_minutes" in changes
    assert changes["overtime_minutes"] == {"from": 0, "to": 45}

def test_regularize_mismatched_date_rejected(test_client, seed_record):
    # Pass punch_in on Oct 2 for an Oct 1 record
    pin_oct2 = datetime(2026, 10, 2, 9, 30, 0, tzinfo=IST)
    res = test_client.patch("/attendance/EMP7001/2026-10-01", json={
        "punch_in": to_ms(pin_oct2),
        "reason": "Wrong date punch",
        "regularized_by": "admin"
    })
    assert res.status_code == 422
    assert res.json() == {"detail": "punch_in must belong to the attendance date"}

def test_regularize_no_change_rejected(test_client, seed_record):
    # Try regularizing without changing anything
    res = test_client.patch("/attendance/EMP7001/2026-10-01", json={
        "status": "PRESENT",
        "reason": "No actual change",
        "regularized_by": "admin"
    })
    assert res.status_code == 422
    assert res.json() == {"detail": "request does not change the record"}

def test_regularize_concurrency_cas_conflict(test_client, seed_record):
    from pymongo.collection import Collection
    real_find_one = Collection.find_one
    def racing_find_one(self, *args, **kwargs):
        doc = real_find_one(self, *args, **kwargs)
        # Simulate a concurrent request committing changes right after find_one reads snapshot
        if self.name == "attendance_logs" and doc and "history" in doc:
            self.update_one(
                {"_id": doc["_id"]},
                {"$push": {"history": {"at": datetime.now(timezone.utc), "by": "race_winner", "reason": "first", "changes": {}}}}
            )
        return doc

    from unittest.mock import patch
    with patch.object(Collection, "find_one", autospec=True, side_effect=racing_find_one):
        res = test_client.patch("/attendance/EMP7001/2026-10-01", json={
            "status": "LEAVE",
            "reason": "Test CAS conflict",
            "regularized_by": "admin"
        })
        assert res.status_code == 409
        assert res.json() == {"detail": "record changed concurrently; retry"}
