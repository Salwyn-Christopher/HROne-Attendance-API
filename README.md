# Employee Attendance & Analytics API

FastAPI + MongoDB implementation for the HROne Software Engineer Trainee assignment.

## Run locally

Requirements: Python 3.11+ and MongoDB 6.0+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your own MongoDB Atlas connection string and database name, or set the same variables in your shell. The application loads `.env` for local development; real environment variables take precedence. Never commit `.env`. The recommended disposable development database is `hrone_attendance_test`.

Then run:

```bash
uvicorn app.main:app --port 8000
```

The application creates its indexes at startup. Open `http://localhost:8000/docs` to inspect the API. `GET /health` should return `{"status":"ok"}` only when MongoDB is reachable.

Do not commit `.env`, secrets, virtual environments, sample data dumps, the assignment statement, or a Dockerfile.

## Run automated tests

The regression suite runs against a dedicated test database (`hrone_attendance_pytest`) and cleans up its data automatically.

```bash
pytest -v
```

All 76 contract, validation, concurrency, and analytics tests pass against MongoDB 6.0+ (verified on MongoDB 8.0).

## Submission status

Verified and submission-ready. The complete API contract, edge cases, race conditions (employee duplication, concurrent punch-in, concurrent punch-out, and regularization compare-and-swap), and aggregation pipelines have been audited and tested. All 76 tests pass cleanly. Explain plans for all supported endpoints have been verified.
