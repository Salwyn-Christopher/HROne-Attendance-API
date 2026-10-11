"""
HROne Employee Attendance & Analytics API.
Run from repository root: uvicorn app.main:app --port 8000
"""
import os
import re
import calendar
import json
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from bson import ObjectId, json_util

load_dotenv()
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    tz_aware=True,
    tzinfo=UTC,
    serverSelectionTimeoutMS=2000,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}
STATUSES = PRESENCE | {"ABSENT", "LEAVE"}
EPOCH_MIN, EPOCH_MAX = 100_000_000_000, 4_102_444_800_000


def now_utc():
    return datetime.now(UTC).replace(microsecond=0)


def truncate_second(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(microsecond=0)


def epoch_ms(dt):
    if dt is None:
        return None
    return int(truncate_second(dt).timestamp() * 1000)


def from_epoch_ms(value):
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, UTC).replace(microsecond=0)


def as_ist(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(IST)


def api_record(doc):
    if not doc:
        return None
    out = dict(doc)
    out.pop("_id", None)
    for key in ("punch_in", "punch_out", "created_at"):
        if key in out:
            out[key] = epoch_ms(out[key])
    hist = []
    for entry in out.get("history") or []:
        e = dict(entry)
        e["at"] = epoch_ms(e.get("at"))
        changes = {}
        for key, val in (e.get("changes") or {}).items():
            pair = dict(val)
            if key in ("punch_in", "punch_out"):
                pair["from"] = epoch_ms(pair.get("from"))
                pair["to"] = epoch_ms(pair.get("to"))
            changes[key] = pair
        e["changes"] = changes
        hist.append(e)
    out["history"] = hist
    out.setdefault("half_day", False)
    out.setdefault("late_minutes", 0)
    out.setdefault("overtime_minutes", 0)
    out.setdefault("work_hours", None)
    return out


def date_or_422(value: str, label="date") -> date:
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise HTTPException(422, f"{label} must be YYYY-MM-DD")


def month_bounds(month: str):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise HTTPException(422, "month must be YYYY-MM")
    y, m = map(int, month.split("-"))
    start = date(y, m, 1)
    end = date(y, m, calendar.monthrange(y, m)[1])
    return start.isoformat(), end.isoformat(), start, end


def working_days(start: date, end: date) -> int:
    if end < start:
        return 0
    n = 0
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            n += 1
        cur += timedelta(days=1)
    return n


def rounded(value, places=2):
    if value is None:
        return None
    quantum = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def parse_shift(s):
    if not isinstance(s, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", s):
        raise HTTPException(422, "shift time must be HH:MM")
    h, m = map(int, s.split(":"))
    return time(h, m)


def shift_bounds(punch_in: datetime, employee):
    local = as_ist(punch_in)
    start_t = parse_shift(employee["shift_start"])
    end_t = parse_shift(employee["shift_end"])
    shift_date = local.date()
    overnight = end_t <= start_t
    if overnight and local.time().replace(tzinfo=None) < end_t:
        shift_date -= timedelta(days=1)
    start_local = datetime.combine(shift_date, start_t, IST)
    end_date = shift_date + (timedelta(days=1) if overnight else timedelta(days=0))
    end_local = datetime.combine(end_date, end_t, IST)
    return start_local.astimezone(UTC), end_local.astimezone(UTC), shift_date.isoformat()


def compute_late_minutes(punch_in, employee):
    if punch_in is None:
        return 0
    start, _, _ = shift_bounds(punch_in, employee)
    seconds = (truncate_second(punch_in) - start).total_seconds()
    return int(seconds // 60) if seconds > 600 else 0


def compute_work_hours(punch_in, punch_out):
    if punch_in is None or punch_out is None:
        return None
    seconds = (truncate_second(punch_out) - truncate_second(punch_in)).total_seconds()
    return rounded(Decimal(str(seconds)) / Decimal("3600"), 2)


def compute_overtime_minutes(punch_out, punch_in, employee):
    if punch_out is None or punch_in is None:
        return 0
    _, end, _ = shift_bounds(punch_in, employee)
    minutes = int((truncate_second(punch_out) - end).total_seconds() // 60)
    return minutes if minutes >= 30 else 0


def derive_fields(doc, employee):
    pin, pout = doc.get("punch_in"), doc.get("punch_out")
    if doc.get("status") not in PRESENCE:
        doc.update(punch_in=None, punch_out=None, work_hours=None, late_minutes=0,
                   overtime_minutes=0, half_day=False)
        return doc
    doc["late_minutes"] = compute_late_minutes(pin, employee)
    doc["work_hours"] = compute_work_hours(pin, pout)
    doc["overtime_minutes"] = compute_overtime_minutes(pout, pin, employee) if pout else 0
    doc["half_day"] = bool(doc["work_hours"] is not None and doc["work_hours"] < 4.50)
    return doc


class StrictModel(BaseModel):
    # Reject undeclared fields (including client-supplied derived values).
    model_config = ConfigDict(extra="forbid")


class EmployeeIn(StrictModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def valid_joined(cls, v):
        date_or_422(v, "joined_on")
        return v

    @model_validator(mode="after")
    def valid_shifts(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInIn(StrictModel):
    emp_code: str
    punched_at: Optional[int] = Field(default=None, strict=True)
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def valid_timestamp(cls, v):
        if v is not None and (type(v) is not int or not EPOCH_MIN <= v <= EPOCH_MAX):
            raise ValueError("punched_at must be epoch milliseconds")
        return v


class PunchOutIn(StrictModel):
    emp_code: str
    punched_at: Optional[int] = Field(default=None, strict=True)

    @field_validator("punched_at")
    @classmethod
    def valid_timestamp(cls, v):
        if v is not None and (type(v) is not int or not EPOCH_MIN <= v <= EPOCH_MAX):
            raise ValueError("punched_at must be epoch milliseconds")
        return v


class RegularizeIn(StrictModel):
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[int] = Field(default=None, strict=True)
    punch_out: Optional[int] = Field(default=None, strict=True)
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("punch_in", "punch_out")
    @classmethod
    def valid_timestamp(cls, v):
        if v is not None and (type(v) is not int or not EPOCH_MIN <= v <= EPOCH_MAX):
            raise ValueError("timestamps must be epoch milliseconds")
        return v


def ensure_indexes():
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_employee_code")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="ix_employee_department_joined")
    db.employees.create_index([("joined_on", ASCENDING)], name="ix_employee_joined")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_attendance_employee_date")
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="ix_attendance_date_employee")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", DESCENDING), ("status", ASCENDING)], name="ix_attendance_employee_date_status")
    db.attendance_logs.create_index([("date", ASCENDING), ("late_minutes", DESCENDING), ("emp_code", ASCENDING)], name="ix_attendance_date_late")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("punch_in", DESCENDING)], name="ix_attendance_employee_punchin")


@app.on_event("startup")
def startup():
    ensure_indexes()


def fail_db(exc):
    raise HTTPException(503, "database unavailable")


@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(503, "database unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = now_utc()
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    doc.pop("_id", None)
    return {**doc, "created_at": epoch_ms(doc["created_at"])}


@app.get("/employees")
def list_employees(department: Optional[str] = None,
                   page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {"department": department} if department is not None else {}
    total = db.employees.count_documents(q)
    docs = list(db.employees.find(q, {"_id": 0}).sort("emp_code", ASCENDING).skip((page-1)*page_size).limit(page_size))
    for d in docs:
        d["created_at"] = epoch_ms(d["created_at"])
    return {"items": docs, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")
    ts = truncate_second(from_epoch_ms(body.punched_at) if body.punched_at is not None else now_utc())
    _, _, attendance_date = shift_bounds(ts, emp)
    doc = {
        "emp_code": body.emp_code, "date": attendance_date, "status": body.status,
        "punch_in": ts, "punch_out": None, "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp), "overtime_minutes": 0,
        "half_day": False, "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this attendance date")
    return api_record(doc)


@app.get("/attendance")
def list_attendance(emp_code: Optional[str] = None, date_from: Optional[str] = None,
                    date_to: Optional[str] = None, status: Optional[str] = None,
                    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    if status is not None and status not in STATUSES:
        raise HTTPException(422, "invalid status")
    if date_from:
        date_or_422(date_from, "date_from")
    if date_to:
        date_or_422(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be <= date_to")
    q = {}
    if emp_code: q["emp_code"] = emp_code
    if status: q["status"] = status
    if date_from or date_to:
        q["date"] = {}
        if date_from: q["date"]["$gte"] = date_from
        if date_to: q["date"]["$lte"] = date_to
    total = db.attendance_logs.count_documents(q)
    docs = list(db.attendance_logs.find(q).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip((page-1)*page_size).limit(page_size))
    return {"items": [api_record(d) for d in docs], "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")
    out_at = truncate_second(from_epoch_ms(body.punched_at) if body.punched_at is not None else now_utc())
    # Find the most recent open record whose punch-in is no later than the requested instant.
    candidate = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$ne": None}},
        sort=[("punch_in", DESCENDING)]
    )
    if not candidate:
        raise HTTPException(404, "no punch-in found")
    if candidate.get("punch_out") is not None:
        raise HTTPException(409, "record already punched out")
    if out_at <= candidate["punch_in"] or out_at - candidate["punch_in"] > timedelta(hours=24):
        raise HTTPException(422, "punched_at must be after punch-in and within 24 hours")
    update = derive_fields({**candidate, "punch_out": out_at}, emp)
    result = db.attendance_logs.update_one(
        {"_id": candidate["_id"], "punch_out": None},
        {"$set": {"punch_out": out_at, "work_hours": update["work_hours"],
                  "overtime_minutes": update["overtime_minutes"], "half_day": update["half_day"]}}
    )
    if result.modified_count != 1:
        raise HTTPException(409, "record already punched out")
    fresh = db.attendance_logs.find_one({"_id": candidate["_id"]})
    return api_record(fresh)


@app.patch("/attendance/{emp_code}/{date}")
def regularize(emp_code: str, date: str, body: RegularizeIn):
    date_or_422(date)
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(404, "attendance record not found")
    current = {k: record.get(k) for k in ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day")}
    current["late_minutes"] = current["late_minutes"] if current["late_minutes"] is not None else 0
    current["overtime_minutes"] = current["overtime_minutes"] if current["overtime_minutes"] is not None else 0
    current["half_day"] = current["half_day"] if current["half_day"] is not None else False
    proposed = dict(current)
    supplied = body.model_dump(exclude_unset=True)
    if "status" in supplied: proposed["status"] = supplied["status"]
    if proposed["status"] in {"ABSENT", "LEAVE"}:
        if "punch_in" in supplied or "punch_out" in supplied:
            raise HTTPException(422, "ABSENT/LEAVE cannot include punch times")
        proposed["punch_in"] = proposed["punch_out"] = None
    else:
        if "punch_in" in supplied: proposed["punch_in"] = from_epoch_ms(supplied["punch_in"])
        if "punch_out" in supplied: proposed["punch_out"] = from_epoch_ms(supplied["punch_out"])
        if proposed["punch_in"] is None:
            raise HTTPException(422, "presence status requires punch_in")
        pin_local = as_ist(proposed["punch_in"])
        _, _, expected_date = shift_bounds(proposed["punch_in"], emp)
        if expected_date != date:
            raise HTTPException(422, "punch_in must belong to the attendance date")
        if proposed["punch_out"] is not None:
            if proposed["punch_out"] <= proposed["punch_in"] or proposed["punch_out"] - proposed["punch_in"] > timedelta(hours=24):
                raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
    proposed = derive_fields(proposed, emp)
    changed = {}
    for key in ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day"):
        old, new = current.get(key), proposed.get(key)
        if isinstance(old, datetime): old = truncate_second(old)
        if isinstance(new, datetime): new = truncate_second(new)
        if old != new:
            changed[key] = {"from": current.get(key), "to": proposed.get(key)}
    if not changed:
        raise HTTPException(422, "request does not change the record")
    history_entry = {"at": now_utc(), "by": body.regularized_by, "reason": body.reason, "changes": changed}
    # Compare-and-swap prevents concurrent edits from overwriting each other's history.
    old_history = record.get("history", [])
    update_fields = {k: proposed.get(k) for k in ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day")}
    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "$or": [{"history": old_history}, {"history": {"$exists": False}}]},
        {"$set": update_fields, "$push": {"history": history_entry}}
    )
    if result.modified_count != 1:
        raise HTTPException(409, "record changed concurrently; retry")
    return api_record(db.attendance_logs.find_one({"_id": record["_id"]}))


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    start_s, end_s, start_d, end_d = month_bounds(month)
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")
    effective_start = max(start_d, date_or_422(emp["joined_on"], "joined_on"))
    wd = working_days(effective_start, end_d) if effective_start <= end_d else 0
    pipeline = [
        {"$match": {"emp_code": emp_code, "date": {"$gte": max(start_s, emp["joined_on"]), "$lte": end_s}}},
        {"$set": {"_dow": {"$dayOfWeek": {"date": {"$dateFromString": {"dateString": "$date"}}}}}},
        {"$group": {"_id": None,
            "present_days": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$status", list(PRESENCE)]}, {"$in": ["$_dow", [2,3,4,5,6]]}]},
                {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]}, 0]}},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}}
        }}
    ]
    result = list(db.attendance_logs.aggregate(pipeline))
    row = result[0] if result else {}
    pct = rounded((row.get("present_days", 0) / wd * 100), 4) if wd else None
    return {"emp_code": emp_code, "month": month, "working_days": wd,
            "present_days": row.get("present_days", 0), "leave_days": row.get("leave_days", 0),
            "late_count": row.get("late_count", 0), "total_late_minutes": row.get("total_late_minutes", 0),
            "total_overtime_minutes": row.get("total_overtime_minutes", 0), "attendance_pct": pct}


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    start_s, end_s, start_d, end_d = month_bounds(month)
    match = {"joined_on": {"$lte": end_s}}
    if department is not None: match["department"] = department
    pipeline = [
        {"$match": match},
        {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code", "joined": "$joined_on"},
            "pipeline": [{"$match": {"$expr": {"$and": [
                {"$eq": ["$emp_code", "$$code"]}, {"$gte": ["$date", start_s]},
                {"$gte": ["$date", "$$joined"]}, {"$lte": ["$date", end_s]}]}}},
                {"$set": {"_dow": {"$dayOfWeek": {"date": {"$dateFromString": {"dateString": "$date"}}}}}}],
            "as": "logs"}},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {"_id": "$department", "headcount": {"$addToSet": "$emp_code"},
            "present_days": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$logs.status", list(PRESENCE)]}, {"$in": ["$logs._dow", [2,3,4,5,6]]}]},
                {"$cond": [{"$eq": [{"$ifNull": ["$logs.half_day", False]}, True]}, 0.5, 1]}, 0]}},
            "work_hours_sum": {"$sum": {"$cond": [{"$and": [
                {"$in": ["$logs.status", list(PRESENCE)]}, {"$ne": ["$logs.work_hours", None]}]}, "$logs.work_hours", 0]}},
            "work_hours_count": {"$sum": {"$cond": [{"$and": [
                {"$in": ["$logs.status", list(PRESENCE)]}, {"$ne": ["$logs.work_hours", None]}]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
            "leave_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}}
        }},
        {"$set": {"headcount": {"$size": "$headcount"}, "avg_work_hours": {"$cond": [
            {"$gt": ["$work_hours_count", 0]}, {"$divide": ["$work_hours_sum", "$work_hours_count"]}, None]}}},
        {"$sort": {"_id": 1}}
    ]
    rows = list(db.employees.aggregate(pipeline))
    items = []
    for r in rows:
        if r["headcount"] <= 0: continue
        items.append({"department": r["_id"], "headcount": r["headcount"],
            "present_days": r["present_days"], "avg_work_hours": rounded(r["avg_work_hours"], 2),
            "late_count": r["late_count"], "total_late_minutes": r["total_late_minutes"],
            "leave_count": r["leave_count"], "on_duty_count": r["on_duty_count"]})
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int = Query(10, ge=1, le=50), department: Optional[str] = None):
    start_s, end_s, _, _ = month_bounds(month)
    pipeline = [
        {"$match": {"date": {"$gte": start_s, "$lte": end_s}, "late_minutes": {"$gt": 0}}},
        {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"},
                    "late_count": {"$sum": 1}}},
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline += [
        {"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$project": {"_id": 0, "rank": 1, "emp_code": "$_id", "name": "$employee.name",
                      "department": "$employee.department", "total_late_minutes": 1, "late_count": 1}}
    ]
    return {"month": month, "items": list(db.attendance_logs.aggregate(pipeline))}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_: str = Query(..., alias="from"), to: str = Query(...)):
    start_d, end_d = date_or_422(from_, "from"), date_or_422(to, "to")
    if end_d < start_d:
        raise HTTPException(422, "to must be >= from")
    if (end_d - start_d).days + 1 > 92:
        raise HTTPException(422, "range cannot exceed 92 days")
    if not db.employees.find_one({"department": department}):
        raise HTTPException(404, "unknown department")
    # Generate the calendar series inside MongoDB; no Python-side per-day loop.
    start_instant = datetime.combine(start_d, time.min, IST).astimezone(UTC)
    pipeline = [
        {"$documents": [{"_id": 1}]},
        {"$set": {"days": {"$map": {
            "input": {"$range": [0, (end_d - start_d).days + 1]},
            "as": "offset",
            "in": {"$dateAdd": {"startDate": start_instant, "unit": "day", "amount": "$$offset"}}
        }}}},
        {"$unwind": "$days"},
        {"$set": {"date": {"$dateToString": {"date": "$days", "format": "%Y-%m-%d", "timezone": "Asia/Kolkata"}},
                  "is_working_day": {"$in": [{"$dayOfWeek": {"date": "$days", "timezone": "Asia/Kolkata"}}, [2,3,4,5,6]]}}},
        {"$lookup": {"from": "employees", "let": {"day": "$date"},
            "pipeline": [{"$match": {"$expr": {"$and": [
                {"$eq": ["$department", department]}, {"$lte": ["$joined_on", "$$day"]}]}}},
                {"$count": "count"}], "as": "hc"}},
        {"$set": {"headcount": {"$ifNull": [{"$arrayElemAt": ["$hc.count", 0]}, 0]}}},
        {"$lookup": {"from": "attendance_logs", "let": {"day": "$date"},
            "pipeline": [{"$match": {"$expr": {"$eq": ["$date", "$$day"]}}},
                {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "emp"}},
                {"$unwind": "$emp"}, {"$match": {"$expr": {"$eq": ["$emp.department", department]}}},
                {"$group": {"_id": None,
                    "present_count": {"$sum": {"$cond": [{"$in": ["$status", list(PRESENCE)]},
                        {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]}, 0]}},
                    "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}}}],
            "as": "daily"}},
        {"$set": {"present_count": {"$ifNull": [{"$arrayElemAt": ["$daily.present_count", 0]}, 0]},
                  "late_count": {"$ifNull": [{"$arrayElemAt": ["$daily.late_count", 0]}, 0]}}},
        {"$set": {"attendance_rate": {
            "$cond": [
                {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                {"$divide": [
                    {"$floor": {"$add": [{"$multiply": [{"$divide": ["$present_count", "$headcount"]}, 10000]}, 0.5]}},
                    10000
                ]},
                None
            ]
        }}},
        {"$setWindowFields": {"sortBy": {"date": 1}, "output": {
            "moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}}}}},
        {"$set": {"moving_avg_7d": {"$cond": [{"$eq": ["$moving_avg_7d", None]}, None,
            {"$divide": [{"$floor": {"$add": [{"$multiply": ["$moving_avg_7d", 10000]}, 0.5]}}, 10000]}]}}},
        {"$project": {"_id": 0, "date": 1, "is_working_day": 1, "headcount": 1,
                      "present_count": 1, "late_count": 1, "attendance_rate": 1, "moving_avg_7d": 1}},
        {"$sort": {"date": 1}}
    ]
    rows = list(db.aggregate(pipeline))
    return {"department": department, "items": rows}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str, emp_code: Optional[str] = None, month: Optional[str] = None,
                     department: Optional[str] = None, limit: int = Query(10, ge=1, le=50),
                     date_from: Optional[str] = None, date_to: Optional[str] = None,
                     status: Optional[str] = None, page: int = Query(1, ge=1),
                     page_size: int = Query(20, ge=1, le=100),
                     from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None):
    allowed = {"attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"}
    if endpoint not in allowed:
        raise HTTPException(422, "unsupported endpoint")
    if endpoint == "attendance_list":
        q = {}
        if emp_code: q["emp_code"] = emp_code
        if status: q["status"] = status
        if date_from or date_to:
            q["date"] = {}
            if date_from: q["date"]["$gte"] = date_from
            if date_to: q["date"]["$lte"] = date_to
        command = {"find": "attendance_logs", "filter": q,
                   "sort": {"date": -1, "emp_code": 1}, "skip": (page-1)*page_size, "limit": page_size}
        coll = "attendance_logs"
    elif endpoint == "employee_monthly":
        if not emp_code or not month: raise HTTPException(422, "emp_code and month are required")
        start_s, end_s, _, _ = month_bounds(month)
        pipeline = [{"$match": {"emp_code": emp_code, "date": {"$gte": start_s, "$lte": end_s}}}]
        command = {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}
        coll = "attendance_logs"
    elif endpoint == "department_summary":
        if not month: raise HTTPException(422, "month is required")
        start_s, end_s, _, _ = month_bounds(month)
        q = {"joined_on": {"$lte": end_s}}
        if department: q["department"] = department
        command = {"find": "employees", "filter": q, "sort": {"department": 1}}
        coll = "employees"
    elif endpoint == "late_leaderboard":
        if not month: raise HTTPException(422, "month is required")
        start_s, end_s, _, _ = month_bounds(month)
        pipeline = [{"$match": {"date": {"$gte": start_s, "$lte": end_s}, "late_minutes": {"$gt": 0}}}]
        command = {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}
        coll = "attendance_logs"
    else:
        if not department or not from_ or not to: raise HTTPException(422, "department, from and to are required")
        date_or_422(from_, "from"); date_or_422(to, "to")
        pipeline = [{"$match": {"department": department, "joined_on": {"$lte": to}}}]
        command = {"aggregate": "employees", "pipeline": pipeline, "cursor": {}}
        coll = "employees"
    try:
        if "find" in command:
            explain = db.command({"explain": {"find": command["find"], "filter": command["filter"],
                **({"sort": command["sort"]} if command.get("sort") else {}),
                **({"skip": command["skip"]} if command.get("skip") else {}),
                **({"limit": command["limit"]} if command.get("limit") else {})},
                "verbosity": "executionStats"})
        else:
            explain = db.command({"explain": {"aggregate": command["aggregate"], "pipeline": command["pipeline"],
                "cursor": command["cursor"]}, "verbosity": "executionStats"})
        return {"endpoint": endpoint, "collection": coll, "explain": json.loads(json_util.dumps(explain))}
    except PyMongoError as exc:
        raise HTTPException(422, f"could not explain endpoint: {str(exc)}")
