# Employee Attendance & Analytics API

A REST API built with **Python, FastAPI, and MongoDB** for employee management, attendance tracking, manual attendance corrections, and workforce analytics.

<p>
  <img src="https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/MongoDB-47A248?logo=mongodb&logoColor=white" alt="MongoDB" />
  <img src="https://img.shields.io/badge/Tests-76%20passed-success" alt="Last recorded test result" />
</p>

## Features

- Employee creation, validation, filtering, and pagination
- Attendance punch-in and punch-out
- Shift rules, grace periods, and work-hour calculations
- Manual attendance regularization and audit trail
- Monthly attendance analytics and department summaries
- Late-arrival leaderboard and department trends
- Health checks and query explain endpoints

## Tech Stack

- **Backend:** Python, FastAPI
- **Database:** MongoDB, PyMongo
- **Testing:** pytest
- **API documentation:** OpenAPI, Swagger UI

## Getting Started

**1. Clone the repository**

```bash
git clone https://github.com/Salwyn-Christopher/HROne-Attendance-API.git
cd HROne-Attendance-API
```

**2. Create a virtual environment and install dependencies**

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**3. Configure MongoDB**

Use `.env.example` as your configuration reference. Set the environment variables required by the application using your own local credentials. Never commit `.env` or secrets.

**4. Run the API**

```bash
python -m uvicorn app.main:app --reload --port 8000
```

Open **http://127.0.0.1:8000/docs** to explore and test the API.

## Run Tests

```bash
python -m pytest -v
```

Last recorded result: **76 passed, 3 warnings**. Rerun the tests after any code changes.

## Project Documentation

- [`REVIEW.md`](REVIEW.md) — Code review findings
- [`DECISIONS.md`](DECISIONS.md) — Design decisions
- [`app/main.py`](app/main.py) — API implementation
- [`tests/`](tests/) — Automated tests

## Screenshots

*Add screenshots of the actual running application here.*

Recommended: Swagger UI (`/docs`), a successful employee creation response, and an analytics response. Use fictional data and ensure no credentials or personal information are visible.

---

**Project context:** Software Engineer Trainee assignment. Refer to the API implementation and tests for supported behavior.
