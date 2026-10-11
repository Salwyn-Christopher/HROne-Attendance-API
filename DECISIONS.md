# DECISIONS.md

1. **Indexes.** A unique `employees.emp_code` index enforces employee identity. A unique compound `attendance_logs(emp_code, date)` index enforces one record per attendance day and makes punch-in race-safe. Other indexes support department/join-date filtering, attendance sorting, employee/date/status filters, leaderboard input and punch-out lookup. I would avoid overlapping indexes unless explain plans justify their write cost.

2. **Punch-in race.** Both requests may check the employee, but neither relies on a prior attendance lookup for uniqueness. Both attempt the insert; MongoDB's unique compound index permits one and rejects the other with `DuplicateKeyError`, which becomes HTTP 409.

3. **Ties.** `$rank` gives equal totals the same rank and skips the next rank. Filtering `rank <= limit` after ranking includes every tied employee at the cutoff; results then sort by minutes descending and `emp_code` ascending.

4. **Headcount.** The summary starts with employees joined by period end and preserves employees without logs in its lookup. It counts distinct employee codes, not attendance records.

5. **100x data.** I would benchmark execution-statistics explain plans on representative data and consider daily pre-aggregations if analytics latency becomes unacceptable.
6. **Date and Day-of-Week Normalization in Aggregations.** Attendance dates are stored as ISO calendar date strings (`YYYY-MM-DD`). In aggregation pipelines calculating weekday attendance (`Mon-Fri`), parsing strings via UTC `{"$dayOfWeek": {"date": {"$dateFromString": {"dateString": "$date"}}}}` avoids timezone shift discrepancies where midnight IST (+05:30) maps into the previous UTC day, ensuring deterministic weekday matching against `[2, 3, 4, 5, 6]`.
7. **Explain Serialization.** Explain execution plans returned by MongoDB Atlas include internal BSON `Timestamp` objects (cluster and operation time). These must be serialized to native JSON primitives via `json.loads(json_util.dumps(explain))` so FastAPI's response encoder outputs valid JSON without crashing.
