"""Transactional Managed accounting. All balances are integer micro-USD.

One SQLite database on a persistent local volume; BEGIN IMMEDIATE serializes
reservations across threads/processes. Never put this database on a network FS.
"""

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt TEXT NOT NULL,
                    password TEXT NOT NULL, recovery TEXT NOT NULL, created REAL NOT NULL,
                    disabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY, account TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS entries (
                    id TEXT PRIMARY KEY, account TEXT NOT NULL, amount INTEGER NOT NULL,
                    kind TEXT NOT NULL, reference TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS entries_account ON entries(account);
                CREATE TABLE IF NOT EXISTS requests (
                    account TEXT NOT NULL, id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    study TEXT NOT NULL, state TEXT NOT NULL, reserved INTEGER NOT NULL,
                    cost INTEGER NOT NULL, tariff TEXT NOT NULL, created REAL NOT NULL,
                    provider_id TEXT, usage TEXT, response TEXT, resolved TEXT,
                    PRIMARY KEY(account,id));
                CREATE TABLE IF NOT EXISTS limits (
                    account TEXT NOT NULL, study TEXT NOT NULL, maximum INTEGER NOT NULL,
                    PRIMARY KEY(account,study));
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY, account TEXT NOT NULL, request_id TEXT NOT NULL,
                    cents INTEGER NOT NULL, created REAL NOT NULL, state TEXT NOT NULL,
                    session TEXT UNIQUE, url TEXT, payment_intent TEXT UNIQUE,
                    refunded_cents INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(account,request_id));
                CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS throttles (
                    key TEXT PRIMARY KEY, started REAL NOT NULL, attempts INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS audits (
                    id TEXT PRIMARY KEY, account TEXT, action TEXT NOT NULL,
                    detail TEXT NOT NULL, created REAL NOT NULL);
            """)
        path.chmod(0o600)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def audit(db, account, action, detail):
        db.execute(
            "INSERT INTO audits VALUES (?,?,?,?,?)",
            (uuid.uuid4().hex, account, action, json.dumps(detail), time.time()),
        )

    @staticmethod
    def entry(db, account, amount, kind, reference):
        db.execute(
            "INSERT INTO entries VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, account, amount, kind, reference, time.time()),
        )

    @staticmethod
    def balance(db, account):
        return db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM entries WHERE account=?", (account,)
        ).fetchone()[0]

    def throttle(self, key, maximum=10, window=900):
        key = digest(key)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM throttles WHERE key=?", (key,)).fetchone()
            if row and row["started"] > time.time() - window:
                if row["attempts"] >= maximum:
                    raise ValueError("Too many attempts. Try again later.")
                db.execute("UPDATE throttles SET attempts=attempts+1 WHERE key=?", (key,))
            else:
                db.execute("INSERT OR REPLACE INTO throttles VALUES (?,?,1)", (key, time.time()))

    def register(self, username, password):
        account, salt, recovery = uuid.uuid4().hex, secrets.token_hex(16), secrets.token_urlsafe(32)
        encoded = password_hash(password, salt)
        try:
            with self.transaction() as db:
                db.execute(
                    "INSERT INTO accounts VALUES (?,?,?,?,?,?,0)",
                    (account, username.lower(), salt, encoded, digest(recovery), time.time()),
                )
                self.audit(db, account, "registered", {})
        except sqlite3.IntegrityError as exc:
            raise ValueError("That account name is unavailable.") from exc
        return {"account_id": account, "recovery_code": recovery}

    def login(self, username, password):
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM accounts WHERE username=?", (username.lower(),)
            ).fetchone()
        # Spend the same hashing work on an unknown user.
        computed = password_hash(password, row["salt"] if row else "00" * 16)
        if not row or not hmac.compare_digest(computed, row["password"]) or row["disabled"]:
            raise ValueError("Account name or password is incorrect.")
        token = secrets.token_urlsafe(48)
        with self.transaction() as db:
            # Password hashing happens outside the write lock. Recovery or an
            # account freeze may have completed while that work was in flight.
            current = db.execute(
                "SELECT password,disabled FROM accounts WHERE id=?", (row["id"],)
            ).fetchone()
            if (
                not current
                or current["disabled"]
                or not hmac.compare_digest(current["password"], computed)
            ):
                raise ValueError("Account name or password is incorrect.")
            db.execute(
                "INSERT INTO sessions VALUES (?,?,?)",
                (digest(token), row["id"], time.time() + 86400 * 7),
            )
        return {"token": token, "account_id": row["id"], "expires_in": 86400 * 7}

    def recover(self, username, recovery, password):
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM accounts WHERE username=?", (username.lower(),)
            ).fetchone()
            if (
                not row
                or row["disabled"]
                or not hmac.compare_digest(row["recovery"], digest(recovery))
            ):
                raise ValueError("Account recovery failed.")
            salt, replacement = secrets.token_hex(16), secrets.token_urlsafe(32)
            db.execute(
                "UPDATE accounts SET salt=?,password=?,recovery=? WHERE id=?",
                (salt, password_hash(password, salt), digest(replacement), row["id"]),
            )
            db.execute("DELETE FROM sessions WHERE account=?", (row["id"],))
            self.audit(db, row["id"], "password_recovered", {})
        return {"recovery_code": replacement}

    def authenticate(self, token):
        with self.transaction() as db:
            row = db.execute(
                """SELECT a.id FROM sessions s JOIN accounts a ON a.id=s.account
                WHERE s.token=? AND s.expires>? AND a.disabled=0""",
                (digest(token), time.time()),
            ).fetchone()
        if not row:
            raise ValueError("Session expired or revoked. Sign in again.")
        return row["id"]

    def logout(self, token):
        with self.transaction() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (digest(token),))

    def set_limit(self, account, study, maximum):
        with self.transaction() as db:
            used = db.execute(
                "SELECT COALESCE(SUM(cost),0) FROM requests WHERE account=? AND study=?",
                (account, study),
            ).fetchone()[0]
            if maximum < used:
                raise ValueError("Limit cannot be below existing charges and reservations.")
            db.execute("INSERT OR REPLACE INTO limits VALUES (?,?,?)", (account, study, maximum))
            self.audit(db, account, "study_limit", {"study": study, "maximum": maximum})

    def reserve(
        self,
        account,
        request_id,
        fingerprint,
        study,
        amount,
        maximum,
        tariff,
        account_daily,
        global_daily,
    ):
        with self.transaction() as db:
            old = db.execute(
                "SELECT * FROM requests WHERE account=? AND id=?", (account, request_id)
            ).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise ValueError("Request ID already belongs to different inputs.")
                return dict(old), False
            db.execute("INSERT OR IGNORE INTO limits VALUES (?,?,?)", (account, study, maximum))
            limit = db.execute(
                "SELECT maximum FROM limits WHERE account=? AND study=?", (account, study)
            ).fetchone()[0]
            spent = db.execute(
                "SELECT COALESCE(SUM(cost),0) FROM requests WHERE account=? AND study=?",
                (account, study),
            ).fetchone()[0]
            if spent + amount > min(limit, maximum):
                raise ValueError("Study spending limit reached. Approve a larger limit explicitly.")
            if self.balance(db, account) < amount:
                raise ValueError("Insufficient wallet balance for the maximum reservation.")
            since = time.time() - 86400
            for sql, params, cap in [
                (
                    "SELECT COALESCE(SUM(cost),0) FROM requests WHERE created>? AND account=?",
                    (since, account),
                    account_daily,
                ),
                (
                    "SELECT COALESCE(SUM(cost),0) FROM requests WHERE created>?",
                    (since,),
                    global_daily,
                ),
            ]:
                if db.execute(sql, params).fetchone()[0] + amount > cap:
                    raise ValueError("Daily service spending limit reached.")
            db.execute(
                """INSERT INTO requests
                (account,id,fingerprint,study,state,reserved,cost,tariff,created)
                VALUES (?,?,?,?,'reserved',?,?,?,?)""",
                (
                    account,
                    request_id,
                    fingerprint,
                    study,
                    amount,
                    amount,
                    json.dumps(tariff),
                    time.time(),
                ),
            )
            self.entry(db, account, -amount, "reservation", request_id)
            return dict(
                db.execute(
                    "SELECT * FROM requests WHERE account=? AND id=?", (account, request_id)
                ).fetchone()
            ), True

    def settle(self, account, request_id, cost, state, usage=None, provider_id=None, response=None):
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM requests WHERE account=? AND id=?", (account, request_id)
            ).fetchone()
            if row["state"] not in {"reserved", "ambiguous"}:
                return dict(row)
            if cost < 0 or cost > row["reserved"]:
                raise ValueError(
                    "Charge exceeds its reservation; operator reconciliation required."
                )
            self.entry(db, account, row["cost"] - cost, "settlement", request_id)
            db.execute(
                """UPDATE requests SET cost=?,state=?,usage=?,provider_id=?,response=?
                WHERE account=? AND id=?""",
                (
                    cost,
                    state,
                    json.dumps(usage) if usage else None,
                    provider_id,
                    json.dumps(response) if response else None,
                    account,
                    request_id,
                ),
            )
            return dict(
                db.execute(
                    "SELECT * FROM requests WHERE account=? AND id=?", (account, request_id)
                ).fetchone()
            )

    def ambiguous(self, account, request_id):
        with self.transaction() as db:
            db.execute(
                "UPDATE requests SET state='ambiguous' WHERE account=? AND id=? AND state='reserved'",
                (account, request_id),
            )
            return dict(
                db.execute(
                    "SELECT * FROM requests WHERE account=? AND id=?", (account, request_id)
                ).fetchone()
            )

    def reconcile(self, account, request_id, cost, evidence):
        if not evidence.strip():
            raise ValueError("A provider invoice/reference or credit reason is required.")
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM requests WHERE account=? AND id=?", (account, request_id)
            ).fetchone()
            if not row or row["state"] not in {"ambiguous", "reserved"}:
                raise ValueError("Only unresolved reservations can be reconciled.")
            if time.time() - row["created"] < 180:
                raise ValueError("Wait for the provider request deadline before reconciling.")
            if not 0 <= cost <= row["reserved"]:
                raise ValueError("Reconciled cost must be within the authorized reservation.")
            self.entry(db, account, row["cost"] - cost, "reconciliation", request_id)
            db.execute(
                "UPDATE requests SET cost=?,state='reconciled',resolved=? WHERE account=? AND id=?",
                (cost, evidence, account, request_id),
            )
            self.audit(
                db,
                account,
                "reconciled",
                {"request": request_id, "cost": cost, "evidence": evidence},
            )

    def wallet(self, account):
        with self.transaction() as db:
            # Model output is kept only briefly for transport retry recovery.
            db.execute("UPDATE requests SET response=NULL WHERE created<?", (time.time() - 86400,))
            return {
                "account_id": account,
                "available_microusd": self.balance(db, account),
                "requests": [
                    dict(r)
                    for r in db.execute(
                        "SELECT id,study,state,reserved,cost,created,provider_id,usage FROM requests WHERE account=? ORDER BY created DESC LIMIT 200",
                        (account,),
                    )
                ],
                "entries": [
                    dict(r)
                    for r in db.execute(
                        "SELECT amount,kind,reference,created FROM entries WHERE account=? ORDER BY created DESC LIMIT 200",
                        (account,),
                    )
                ],
                "limits": [
                    dict(r)
                    for r in db.execute(
                        "SELECT study,maximum FROM limits WHERE account=?", (account,)
                    )
                ],
            }

    def order(self, account, request_id, cents):
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM orders WHERE account=? AND request_id=?", (account, request_id)
            ).fetchone()
            if row:
                if row["cents"] != cents:
                    raise ValueError("Checkout ID already belongs to a different amount.")
                return dict(row), False
            order_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO orders (id,account,request_id,cents,created,state) VALUES (?,?,?,?,?,'pending')",
                (order_id, account, request_id, cents, time.time()),
            )
            return dict(db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()), True

    def complete_order(self, order_id, session, url):
        with self.transaction() as db:
            db.execute(
                "UPDATE orders SET session=?,url=? WHERE id=? AND (session IS NULL OR session=?)",
                (session, url, order_id, session),
            )

    def payment(self, event_id, obj):
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone():
                return
            order_id = obj.get("metadata", {}).get("venturi_order")
            order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if (
                not order
                or obj.get("currency") != "usd"
                or obj.get("amount_total") != order["cents"]
                or obj.get("mode") != "payment"
            ):
                raise ValueError("Payment does not match a recorded order.")
            if obj.get("payment_status") != "paid":
                return  # An asynchronous payment event may settle this later.
            if order["session"] and order["session"] != obj.get("id"):
                raise ValueError("Payment session mismatch.")
            if not obj.get("payment_intent"):
                raise ValueError("Payment intent is missing.")
            if order["state"] != "paid":
                self.entry(db, order["account"], order["cents"] * 10000, "top_up", order_id)
                db.execute(
                    "UPDATE orders SET state='paid',session=?,payment_intent=? WHERE id=?",
                    (obj["id"], obj["payment_intent"], order_id),
                )
            db.execute("INSERT INTO events VALUES (?,?)", (event_id, time.time()))

    def refund(self, event_id, obj):
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone():
                return
            order = db.execute(
                "SELECT * FROM orders WHERE payment_intent=?", (obj.get("payment_intent"),)
            ).fetchone()
            if not order:
                raise ValueError("Unknown refunded payment; retry after fulfillment.")
            amount = obj.get("amount_refunded")
            if (
                type(amount) is not int
                or not 0 <= amount <= order["cents"]
                or obj.get("currency") != "usd"
            ):
                raise ValueError("Invalid refund amount.")
            delta = max(0, amount - order["refunded_cents"])
            if delta:
                self.entry(db, order["account"], -delta * 10000, "refund", order["id"])
                db.execute("UPDATE orders SET refunded_cents=? WHERE id=?", (amount, order["id"]))
            db.execute("INSERT INTO events VALUES (?,?)", (event_id, time.time()))

    def purge_outputs(self, account):
        with self.transaction() as db:
            db.execute("UPDATE requests SET response=NULL WHERE account=?", (account,))
            self.audit(db, account, "outputs_deleted", {})

    def metrics(self):
        with self.transaction() as db:
            db.execute("UPDATE requests SET response=NULL WHERE created<?", (time.time() - 86400,))
            requests = [
                dict(r) for r in db.execute("SELECT state,cost,study,account FROM requests")
            ]
            totals = {}
            for row in requests:
                key = (row["account"], row["study"])
                totals[key] = totals.get(key, 0) + row["cost"]
            costs = sorted(totals.values())
            return {
                "accounts": db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0],
                "requests": len(requests),
                "studies": len(costs),
                "unresolved": sum(r["state"] in {"reserved", "ambiguous"} for r in requests),
                "failed_cost_microusd": sum(
                    r["cost"] for r in requests if r["state"] != "completed"
                ),
                "study_p50_microusd": costs[int((len(costs) - 1) * 0.5)] if costs else None,
                "study_p95_microusd": costs[int((len(costs) - 1) * 0.95)] if costs else None,
            }
