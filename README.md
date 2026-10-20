## PaymentProcessing API

A simplified peer-to-peer payments API built with Python, FastAPI, and SQLite, with a minimal web frontend for logging in, sending money, and viewing transaction history. It’s a learning project focused on API design and data integrity rather than a production banking app.

Highlights

Account registration and login, with passwords hashed using salted PBKDF2
New accounts are seeded with a demo balance, recorded as a ledger entry
Transfers by account ID or email, executed atomically so debit, credit and ledger entry succeed or fail together
Concurrency-safe transfers via SQLite write locking (BEGIN IMMEDIATE)
Idempotency keys so retried requests never move money twice
Money stored as integer cents to avoid floating-point errors
Database constraints (non-negative balances, unique emails, foreign keys) as a safety net
Clear separation between the HTTP layer (main.py) and the data layer (database.py), with domain errors mapped to proper status codes
Auto-generated interactive docs at /docs

## Run locally

From this folder, create a Windows virtual environment, install the runtime packages, and start the API:

```powershell
python -m venv .venv-windows
.\.venv-windows\Scripts\Activate.ps1
python -m pip install fastapi uvicorn pydantic
python -m uvicorn main:app --reload
```

For Linux:
source .venv/bin/activate
python -m uvicorn main:app --reload


Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The interactive API documentation is at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

The app initializes `payments.db` when the server starts. The sample accounts are **Vasquinho** (`vasquinho@example.test`) and **Filipe** (`filipe@example.test`); both use password **demo1234**. Newly registered accounts need a unique email and start with a €20,000 opening balance (2,000,000 cents), recorded in history as an initial deposit. Transfers in the browser use the recipient's email. All monetary amounts are represented in integer cents by the API and formatted as euros in the demo page.