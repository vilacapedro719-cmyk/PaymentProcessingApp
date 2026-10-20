# Pocket payment API demo

A small FastAPI and SQLite payment demo with a browser interface for account login, registration, balances, transfers, and transfer history.

## Run locally

From this folder, create a Windows virtual environment, install the runtime packages, and start the API:

```powershell
python -m venv .venv-windows
.\.venv-windows\Scripts\Activate.ps1
python -m pip install fastapi uvicorn pydantic
python -m uvicorn main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The interactive API documentation is at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

The app initializes `payments.db` when the server starts. The sample accounts are **Vasquinho** (`vasquinho@example.test`) and **Filipe** (`filipe@example.test`); both use password **demo1234**. Newly registered accounts need a unique email and start with a €20,000 opening balance (2,000,000 cents), recorded in history as an initial deposit. Transfers in the browser use the recipient's email. All monetary amounts are represented in integer cents by the API and formatted as euros in the demo page.