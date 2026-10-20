import asyncio
import sqlite3

import httpx
import pytest

import database
import main


@pytest.fixture
def api_db(tmp_path, monkeypatch):
    db_path = tmp_path / "api-payments.db"

    def get_test_connection():
        connection = sqlite3.connect(db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    monkeypatch.setattr(database, "get_connection", get_test_connection)
    database.init_db()
    database.seed_accounts()


def request_api(method, path, **kwargs):
    async def send_request():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send_request())


def test_get_account_returns_account_data(api_db):
    response = request_api("GET", "/accounts/1")

    assert response.status_code == 200
    assert response.json() == {
        "account_id": 1,
        "name": "Alice",
        "balance_cents": 50_000,
    }


def test_post_transfer_returns_transfer_and_updates_database(api_db):
    response = request_api(
        "POST",
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
    assert database.get_balance(1) == 40_000
    assert database.get_balance(2) == 30_000
    transfer = database.get_transfers()[0]
    assert transfer[0:4] == (1, 1, 2, 10_000)


def test_post_transfer_is_idempotent_for_a_matching_key(api_db):
    first = request_api(
        "POST", "/accounts/1/transfers",
        headers={"Idempotency-Key": "demo-transfer-001"},
        json={"receiver_id": 2, "amount_cents": 10_000},
    )
    retry = request_api(
        "POST", "/accounts/1/transfers",
        headers={"Idempotency-Key": "demo-transfer-001"},
        json={"receiver_id": 2, "amount_cents": 10_000},
    )

    assert first.status_code == retry.status_code == 200
    assert retry.json() == first.json()
    assert database.get_balance(1) == 40_000
    assert database.get_balance(2) == 30_000
    assert len(database.get_transfers()) == 1


def test_post_transfer_rejects_idempotency_key_reuse_for_different_request(api_db):
    headers = {"Idempotency-Key": "demo-transfer-002"}
    first = request_api(
        "POST", "/accounts/1/transfers", headers=headers,
        json={"receiver_id": 2, "amount_cents": 1_000},
    )
    conflict = request_api(
        "POST", "/accounts/1/transfers", headers=headers,
        json={"receiver_id": 2, "amount_cents": 2_000},
    )

    assert first.status_code == 200
    assert conflict.status_code == 422
    assert conflict.json() == {"detail": "Idempotency key was already used for a different request"}
    assert database.get_balance(1) == 49_000
    assert database.get_balance(2) == 21_000
    assert len(database.get_transfers()) == 1


def test_register_and_login_account(api_db):
    created = request_api(
        "POST", "/auth/register",
        json={"name": "New user", "email": "new@example.com", "password": "correct-horse"},
    )
    logged_in = request_api(
        "POST", "/auth/login",
        json={"name": "New user", "password": "correct-horse"},
    )

    assert created.status_code == 201
    assert logged_in.status_code == 200
    assert logged_in.json() == created.json()
    assert created.json()["balance_cents"] == 2_000_000
    history = request_api("GET", f"/accounts/{created.json()['account_id']}/transfers")
    assert history.status_code == 200
    assert history.json()[0]["kind"] == "deposit"
    assert history.json()[0]["amount_cents"] == 2_000_000


def test_transfer_can_target_recipient_by_email_and_history_shows_counterparty(api_db):
    response = request_api(
        "POST", "/accounts/1/transfers",
        headers={"Idempotency-Key": "email-transfer-001"},
        json={"receiver_email": "filipe@example.test", "amount_cents": 1_000},
    )

    assert response.status_code == 200
    assert response.json()["receiver_id"] == 2
    history = request_api("GET", "/accounts/1/transfers")
    assert history.json()[0]["counterparty_name"] == "Filipe"
    assert history.json()[0]["counterparty_email"] == "filipe@example.test"


def test_post_transfer_rejects_insufficient_funds_without_database_changes(api_db):
    response = request_api(
        "POST",
        "/accounts/1/transfers",
        json={"receiver_id": 2, "amount_cents": 50_001},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Insufficient funds"}
    assert database.get_balance(1) == 50_000
    assert database.get_balance(2) == 20_000
    assert database.get_transfers() == []


@pytest.mark.parametrize(
    ("sender_id", "receiver_id", "status_code", "detail"),
    [
        (999, 2, 404, "Sender account does not exist"),
        (1, 999, 404, "Receiver account does not exist"),
    ],
)
def test_post_transfer_reports_missing_accounts_without_database_changes(
    api_db, sender_id, receiver_id, status_code, detail
):
    response = request_api(
        "POST",
        f"/accounts/{sender_id}/transfers",
        json={"receiver_id": receiver_id, "amount_cents": 1_000},
    )

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    assert database.get_balance(1) == 50_000
    assert database.get_balance(2) == 20_000
    assert database.get_transfers() == []


@pytest.mark.parametrize("amount_cents", [0, -1])
def test_post_transfer_rejects_invalid_amounts_without_database_changes(
    api_db, amount_cents
):
    response = request_api(
        "POST",
        "/accounts/1/transfers",
        json={"receiver_id": 2, "amount_cents": amount_cents},
    )

    assert response.status_code == 422
    assert "detail" in response.json()
    assert database.get_balance(1) == 50_000
    assert database.get_balance(2) == 20_000
    assert database.get_transfers() == []


def test_get_transfer_history_returns_account_transfers(api_db):
    database.transfer(1, 2, 1_000)
    database.transfer(2, 1, 2_000)

    response = request_api("GET", "/accounts/1/transfers")

    assert response.status_code == 200
    transfers = response.json()
    assert [transfer["transfer_id"] for transfer in transfers] == [2, 1]
    assert [
        (transfer["sender_id"], transfer["receiver_id"], transfer["amount_cents"])
        for transfer in transfers
    ] == [(2, 1, 2_000), (1, 2, 1_000)]
    assert all(transfer["created_at"] for transfer in transfers)
