import sqlite3

import pytest
from fastapi.testclient import TestClient

import database
import main


@pytest.fixture
def accounts_db(tmp_path, monkeypatch):
    db_path = tmp_path / "payments.db"

    def get_test_connection():
        connection = sqlite3.connect(db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    monkeypatch.setattr(database, "get_connection", get_test_connection)
    database.init_db()
    database.seed_accounts()

    return get_test_connection


def test_transfer_updates_both_account_balances(accounts_db):
    transfer_id = database.transfer(1, 2, 10_000)

    assert transfer_id == 1
    assert database.get_balance(1) == 40_000
    assert database.get_balance(2) == 30_000
    transfer = database.get_transfers()[0]
    assert transfer[1:4] == (1, 2, 10_000)


def test_create_transfer_returns_consistent_response_with_transfer_id(accounts_db):
    client = TestClient(main.app)

    response = client.post(
        "/accounts/1/transfers",
        json={"receiver_id": 2, "amount_cents": 10_000},
    )

    assert response.status_code == 200
    assert response.json() == {
        "transfer_id": 1,
        "sender_id": 1,
        "receiver_id": 2,
        "amount_cents": 10_000,
    }


def test_account_transfer_history_includes_sent_and_received_transfers(accounts_db):
    database.transfer(1, 2, 1_000)
    database.transfer(2, 1, 2_000)
    connection = accounts_db()
    try:
        connection.execute(
            "INSERT INTO accounts (name, balance_cents) VALUES (?, ?)",
            ("Charlie", 10_000),
        )
        connection.commit()
    finally:
        connection.close()
    database.transfer(3, 2, 500)

    response = TestClient(main.app).get("/accounts/1/transfers")

    assert response.status_code == 200
    transfers = response.json()
    assert [transfer["transfer_id"] for transfer in transfers] == [2, 1]
    assert [
        (transfer["sender_id"], transfer["receiver_id"], transfer["amount_cents"])
        for transfer in transfers
    ] == [(2, 1, 2_000), (1, 2, 1_000)]
    assert all(transfer["created_at"] for transfer in transfers)


def test_account_transfer_history_returns_404_for_unknown_account(accounts_db):
    response = TestClient(main.app).get("/accounts/999/transfers")

    assert response.status_code == 404
    assert response.json() == {"detail": "Account not found"}


@pytest.mark.parametrize("amount_cents", [0, -1])
def test_create_transfer_rejects_invalid_amounts(accounts_db, amount_cents):
    client = TestClient(main.app)

    response = client.post(
        "/accounts/1/transfers",
        json={"receiver_id": 2, "amount_cents": amount_cents},
    )

    assert response.status_code == 422
    assert database.get_balance(1) == 50_000
    assert database.get_balance(2) == 20_000
    assert database.get_transfers() == []


@pytest.mark.parametrize(
    ("sender_id", "receiver_id", "amount_cents", "status_code", "detail"),
    [
        (999, 2, 1_000, 404, "Sender account does not exist"),
        (1, 999, 1_000, 404, "Receiver account does not exist"),
        (1, 2, 50_001, 409, "Insufficient funds"),
    ],
)
def test_create_transfer_returns_clean_errors_without_changing_state(
    accounts_db, sender_id, receiver_id, amount_cents, status_code, detail):
    client = TestClient(main.app)

    response = client.post(
        f"/accounts/{sender_id}/transfers",
        json={"receiver_id": receiver_id, "amount_cents": amount_cents},
    )

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    assert database.get_balance(1) == 50_000
    assert database.get_balance(2) == 20_000
    assert database.get_transfers() == []


def test_insufficient_funds_rejects_transfer_without_negative_balance(accounts_db):
    with pytest.raises(ValueError, match="Insufficient funds"):
        database.transfer(1, 2, 50_001)

    assert database.get_balance(1) == 50_000
    assert database.get_balance(2) == 20_000
    assert database.get_balance(1) >= 0
    assert database.get_transfers() == []


@pytest.mark.parametrize(
    ("sender_id", "receiver_id", "amount_cents", "error_message"),
    [
        (0, 2, 1_000, "Sender account does not exist"),
        (1, 0, 1_000, "Receiver account does not exist"),
        (1, 2, 0, "Amount must be greater than 0"),
        (1, 2, -1, "Amount must be greater than 0"),
    ],
)
def test_invalid_transfers_leave_balances_and_history_unchanged(
    accounts_db, sender_id, receiver_id, amount_cents, error_message
):
    initial_balances = (database.get_balance(1), database.get_balance(2))
    initial_transfers = database.get_transfers()

    with pytest.raises(ValueError, match=error_message):
        database.transfer(sender_id, receiver_id, amount_cents)

    assert (database.get_balance(1), database.get_balance(2)) == initial_balances
    assert database.get_transfers() == initial_transfers


def test_database_rejects_negative_account_balance(accounts_db):
    connection = accounts_db()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE accounts SET balance_cents = ? WHERE id = ?",
                (-1, 1),
            )
    finally:
        connection.close()

    assert database.get_balance(1) == 50_000
