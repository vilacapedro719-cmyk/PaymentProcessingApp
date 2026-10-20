from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import re
from pydantic import BaseModel, Field

import database

app = FastAPI(title="Mini Revolut API")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.on_event("startup")
def prepare_database():
    database.init_db()
    database.seed_accounts()


class Credentials(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str | None = Field(default=None, min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)


@app.post("/auth/register", status_code=201)
def register(credentials: Credentials):
    name = credentials.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name cannot be empty")
    email = (credentials.email or "").strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    try:
        account_id = database.create_account(name, email, credentials.password)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    account = database.get_account(account_id)
    return {"account_id": account[0], "name": account[1], "balance_cents": account[2], "email": account[4]}


@app.post("/auth/login")
def login(credentials: Credentials):
    account = database.authenticate_account(credentials.name.strip(), credentials.password)
    if account is None:
        raise HTTPException(status_code=401, detail="Invalid account name or password")
    return {"account_id": account[0], "name": account[1], "balance_cents": account[2], "email": account[3]}


class TransferRequest(BaseModel):
    receiver_id: int | None = Field(default=None, gt=0)
    receiver_email: str | None = Field(default=None, min_length=3, max_length=254)
    amount_cents: int = Field(gt=0)


class TransferResponse(BaseModel):
    transfer_id: int
    sender_id: int
    receiver_id: int
    amount_cents: int


class TransferHistoryItem(TransferResponse):
    created_at: str
    kind: str
    counterparty_name: str | None = None
    counterparty_email: str | None = None


@app.get("/")
def home():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/accounts/{account_id}")
def read_account(account_id: int):
    account = database.get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    return {
        "account_id": account[0],
        "name": account[1],
        "balance_cents": account[2],
        "email": account[4],
    }


@app.get("/accounts")
def list_accounts():
    con = database.get_connection()
    try:
        rows = con.execute("SELECT id, name, email FROM accounts ORDER BY name").fetchall()
    finally:
        con.close()
    return [{"account_id": row[0], "name": row[1], "email": row[2]} for row in rows]


@app.get(
    "/accounts/{account_id}/transfers",
    response_model=list[TransferHistoryItem],
)
def read_account_transfers(account_id: int):
    if database.get_account(account_id) is None:
        raise HTTPException(status_code=404, detail="Account not found")

    return [
        {
            "transfer_id": transfer[0],
            "sender_id": transfer[1],
            "receiver_id": transfer[2],
            "amount_cents": transfer[3],
            "created_at": transfer[4],
            "kind": transfer[5],
            "counterparty_name": transfer[6],
            "counterparty_email": transfer[7],
        }
        for transfer in database.get_account_transfer_history(account_id)
    ]


@app.post("/accounts/{sender_id}/transfers", response_model=TransferResponse)
def create_transfer(
    sender_id: int,
    transfer_request: TransferRequest,
    idempotency_key: str | None = Header(default=None),
):
    if (transfer_request.receiver_id is None) == (transfer_request.receiver_email is None):
        raise HTTPException(status_code=422, detail="Provide exactly one of receiver_id or receiver_email")
    receiver_id = transfer_request.receiver_id
    if transfer_request.receiver_email is not None:
        email = transfer_request.receiver_email.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise HTTPException(status_code=422, detail="Enter a valid recipient email address")
        receiver = database.get_account_by_email(email)
        if receiver is None:
            raise HTTPException(status_code=404, detail="Receiver account does not exist")
        receiver_id = receiver[0]
    try:
        transfer_id = database.transfer(
            sender_id,
            receiver_id,
            transfer_request.amount_cents,
            idempotency_key,
        )
    except database.AccountNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except database.InsufficientFunds as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except database.IdempotencyKeyConflict as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except database.InvalidTransfer as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return {
        "transfer_id": transfer_id,
        "sender_id": sender_id,
        "receiver_id": receiver_id,
        "amount_cents": transfer_request.amount_cents,
    }
