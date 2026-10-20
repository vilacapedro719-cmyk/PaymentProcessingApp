import sqlite3
import hashlib
import secrets


class TransferError(ValueError):
    """Base class for transfer failures. Inherits ValueError so old callers/tests still work."""


class AccountNotFound(TransferError):
    pass


class InsufficientFunds(TransferError):
    pass


class InvalidTransfer(TransferError):
    pass


class IdempotencyKeyConflict(TransferError):
    pass


def get_connection():
    con = sqlite3.connect("payments.db")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db():
    con = get_connection()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            balance_cents INTEGER NOT NULL CHECK (balance_cents >= 0),
            password_hash TEXT,
            email TEXT UNIQUE
        )
    """)

    # Keep existing databases usable when adding account credentials.
    columns = {row[1] for row in cur.execute("PRAGMA table_info(accounts)")}
    if "password_hash" not in columns:
        cur.execute("ALTER TABLE accounts ADD COLUMN password_hash TEXT")
    if "email" not in columns:
        cur.execute("ALTER TABLE accounts ADD COLUMN email TEXT")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_email ON accounts(email COLLATE NOCASE)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS transfers (
            id INTEGER PRIMARY KEY,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            idempotency_key TEXT UNIQUE,
            kind TEXT NOT NULL DEFAULT 'transfer',
            FOREIGN KEY (sender_id) REFERENCES accounts(id),
            FOREIGN KEY (receiver_id) REFERENCES accounts(id)
        )
    """)

    transfer_columns = {row[1] for row in cur.execute("PRAGMA table_info(transfers)")}
    if "kind" not in transfer_columns:
        cur.execute("ALTER TABLE transfers ADD COLUMN kind TEXT NOT NULL DEFAULT 'transfer'")

    con.commit()
    con.close()


def _password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000)
    return f"{salt}${digest.hex()}"


def create_account(name, email, password):
    con = get_connection()
    try:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO accounts (name, balance_cents, password_hash, email) VALUES (?, 2000000, ?, ?)",
            (name, _password_hash(password), email),
        )
        account_id = cur.lastrowid
        cur.execute(
            "INSERT INTO transfers (sender_id, receiver_id, amount_cents, kind) VALUES (?, ?, ?, 'deposit')",
            (account_id, account_id, 2_000_000),
        )
        con.commit()
        return account_id
    except sqlite3.IntegrityError as error:
        con.rollback()
        raise ValueError("An account with that name or email already exists") from error
    finally:
        con.close()


def authenticate_account(name, password):
    con = get_connection()
    try:
        row = con.execute(
            "SELECT id, name, balance_cents, password_hash, email FROM accounts WHERE name = ?",
            (name,),
        ).fetchone()
    finally:
        con.close()
    if row is None or row[3] is None:
        return None
    salt, expected = row[3].split("$", 1)
    actual = _password_hash(password, salt).split("$", 1)[1]
    if not secrets.compare_digest(actual, expected):
        return None
    return (row[0], row[1], row[2], row[4])


def seed_accounts():
    con = get_connection()
    cur = con.cursor()

    cur.execute(
        "INSERT OR IGNORE INTO accounts (id, name, balance_cents) VALUES (?, ?, ?)",
        (1, "Vasquinho", 50000)
    )

    cur.execute(
        "INSERT OR IGNORE INTO accounts (id, name, balance_cents) VALUES (?, ?, ?)",
        (2, "Filipe", 20000)
    )

    cur.execute("UPDATE accounts SET email = 'vasquinho@example.test' WHERE id = 1 AND email IS NULL")
    cur.execute("UPDATE accounts SET email = 'filipe@example.test' WHERE id = 2 AND email IS NULL")

    # Local demo credentials for the two seeded sample accounts.
    demo_hash = _password_hash("demo1234")
    cur.execute("UPDATE accounts SET password_hash = ? WHERE id IN (1, 2) AND password_hash IS NULL", (demo_hash,))

    con.commit()
    con.close()


def get_account(account_id):
    con = get_connection()
    cur = con.cursor()

    cur.execute(
        "SELECT * FROM accounts WHERE id = ?",
        (account_id,)
    )

    account = cur.fetchone()
    con.close()

    return account


def get_account_by_email(email):
    con = get_connection()
    try:
        return con.execute("SELECT id, name, balance_cents, email FROM accounts WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
    finally:
        con.close()


def get_balance(account_id):
    con = get_connection()
    cur = con.cursor()

    cur.execute(
        "SELECT balance_cents FROM accounts WHERE id = ?",
        (account_id,)
    )

    result = cur.fetchone()
    con.close()

    if result is None:
        return None

    return result[0]


def update_balance(account_id, new_balance_cents):
    con = get_connection()
    cur = con.cursor()

    cur.execute(
        """UPDATE accounts
        SET balance_cents = ?
        WHERE id = ?""",
        (new_balance_cents, account_id)
    )

    con.commit()  # Save the change
    con.close()   # Close the connection


def transfer(sender_id, receiver_id, amount_cents, idempotency_key=None):
    if amount_cents <= 0:
        raise InvalidTransfer("Amount must be greater than 0")

    if sender_id == receiver_id:
        raise InvalidTransfer("Cannot transfer to the same account")

    con = get_connection()

    try:
        cur = con.cursor()

        # Acquire the write lock before reading anything, so concurrent
        # requests (including retries with the same key) are serialized.
        cur.execute("BEGIN IMMEDIATE")

        # Idempotency check: must happen AFTER the lock is taken.
        if idempotency_key is not None:
            cur.execute(
                """SELECT id, sender_id, receiver_id, amount_cents
                   FROM transfers
                   WHERE idempotency_key = ?""",
                (idempotency_key,)
            )
            existing = cur.fetchone()

            if existing is not None:
                if existing[1:] != (sender_id, receiver_id, amount_cents):
                    raise IdempotencyKeyConflict(
                        "Idempotency key was already used for a different request"
                    )
                # Genuine retry: nothing was written, so release the lock
                # and return the original result.
                con.rollback()
                return existing[0]

        # Retrieve the sender's balance.
        cur.execute(
            "SELECT balance_cents FROM accounts WHERE id = ?",
            (sender_id,)
        )
        sender = cur.fetchone()

        if sender is None:
            raise AccountNotFound("Sender account does not exist")

        # Retrieve the receiver's balance.
        cur.execute(
            "SELECT balance_cents FROM accounts WHERE id = ?",
            (receiver_id,)
        )
        receiver = cur.fetchone()

        if receiver is None:
            raise AccountNotFound("Receiver account does not exist")

        sender_balance = sender[0]
        receiver_balance = receiver[0]

        # Check that the sender has enough money.
        if sender_balance < amount_cents:
            raise InsufficientFunds("Insufficient funds")

        # Deduct from the sender.
        cur.execute(
            """UPDATE accounts
               SET balance_cents = ?
               WHERE id = ?""",
            (sender_balance - amount_cents, sender_id)
        )

        # Credit the receiver.
        cur.execute(
            """UPDATE accounts
               SET balance_cents = ?
               WHERE id = ?""",
            (receiver_balance + amount_cents, receiver_id)
        )

        cur.execute(
            """INSERT INTO transfers
               (sender_id, receiver_id, amount_cents, idempotency_key)
               VALUES (?, ?, ?, ?)""",
            (sender_id, receiver_id, amount_cents, idempotency_key)
        )
        transfer_id = cur.lastrowid
        con.commit()
        return transfer_id

    except Exception:
        con.rollback()
        raise

    finally:
        con.close()


def get_transfers(account_id=None):
    con = get_connection()

    try:
        cur = con.cursor()
        if account_id is None:
            cur.execute("""
                SELECT id, sender_id, receiver_id, amount_cents, created_at, kind
                FROM transfers
                ORDER BY id DESC
            """)
        else:
            cur.execute("""
                SELECT id, sender_id, receiver_id, amount_cents, created_at, kind
                FROM transfers
                WHERE sender_id = ? OR receiver_id = ?
                ORDER BY id DESC
            """, (account_id, account_id))
        return cur.fetchall()
    finally:
        con.close()


def get_account_transfer_history(account_id):
    con = get_connection()
    try:
        return con.execute("""
            SELECT t.id, t.sender_id, t.receiver_id, t.amount_cents, t.created_at, t.kind,
                   CASE WHEN t.kind = 'deposit' THEN NULL
                        WHEN t.sender_id = ? THEN receiver.name ELSE sender.name END,
                   CASE WHEN t.kind = 'deposit' THEN NULL
                        WHEN t.sender_id = ? THEN receiver.email ELSE sender.email END
            FROM transfers t
            JOIN accounts sender ON sender.id = t.sender_id
            JOIN accounts receiver ON receiver.id = t.receiver_id
            WHERE t.sender_id = ? OR t.receiver_id = ?
            ORDER BY t.id DESC
        """, (account_id, account_id, account_id, account_id)).fetchall()
    finally:
        con.close()


if __name__ == "__main__":
    init_db()
    seed_accounts()

    transfer(2, 1, 10000)

    print("Accounts:")
    print(get_account(1))
    print(get_account(2))
