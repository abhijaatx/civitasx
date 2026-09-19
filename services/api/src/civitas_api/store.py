"""Persistence layer for CivitasX cases and usage.

SQLite is the default for local development and tests. The schema is designed
around owner-scoped records so a future DynamoDB adapter can use the same
contracts without changing the API. Cloud selection is deliberately explicit;
the service never silently falls back from a configured cloud store.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import ConflictError, NotFoundError, RateLimitError
from .models import (
    Artifact,
    CivicCase,
    TaskEvent,
    UsageItem,
    UsageSummary,
    User,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);
CREATE TABLE IF NOT EXISTS cases (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  goal TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'saved',
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cases_owner_updated_idx ON cases(owner_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  content TEXT NOT NULL,
  kind TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS artifacts_case_idx ON artifacts(case_id, created_at DESC);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_case_idx ON events(case_id, created_at ASC);
CREATE TABLE IF NOT EXISTS usage_cases (
  case_id TEXT PRIMARY KEY REFERENCES cases(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  estimated_cost_usd REAL NOT NULL DEFAULT 0,
  reserved_cost_usd REAL NOT NULL DEFAULT 0,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  browser_seconds INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS usage_items (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  estimated_cost_usd REAL NOT NULL DEFAULT 0,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  browser_seconds INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_items_case_idx ON usage_items(case_id, created_at DESC);
CREATE TABLE IF NOT EXISTS usage_reservations (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  amount_usd REAL NOT NULL,
  browser_active INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'reserved',
  created_at TEXT NOT NULL,
  settled_at TEXT
);
CREATE INDEX IF NOT EXISTS usage_reservations_case_idx ON usage_reservations(case_id, state);
CREATE TABLE IF NOT EXISTS usage_global (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  estimated_cost_usd REAL NOT NULL DEFAULT 0,
  reserved_cost_usd REAL NOT NULL DEFAULT 0,
  active_browser_sessions INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO usage_global(id) VALUES (1);
"""


class SQLiteStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,
            timeout=10,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(SCHEMA)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- users and sessions -------------------------------------------------
    def create_user(self, name: str, email: str, password_hash: str) -> User:
        user_id = str(uuid.uuid4())
        created_at = iso_now()
        try:
            with self._transaction() as conn:
                conn.execute(
                    "INSERT INTO users(id,name,email,password_hash,created_at) VALUES (?,?,?,?,?)",
                    (user_id, name, email, password_hash, created_at),
                )
        except sqlite3.IntegrityError as exc:
            if "users.email" in str(exc).lower() or "unique" in str(exc).lower():
                raise ConflictError("An account with that email already exists") from exc
            raise
        return User(id=user_id, name=name, email=email, created_at=parse_datetime(created_at))

    def get_user_by_email(self, email: str) -> tuple[User, str] | None:
        with self._lock:
            row = self._conn.execute(
                (
                    "SELECT id,name,email,password_hash,created_at FROM users "
                    "WHERE email = ? COLLATE NOCASE"
                ),
                (email,),
            ).fetchone()
        if row is None:
            return None
        return self._user_from_row(row), str(row["password_hash"])

    def get_user(self, user_id: str) -> User | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id,name,email,created_at FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return self._user_from_row(row) if row else None

    def create_session(self, token_hash: str, user_id: str, expires_at: datetime) -> None:
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO sessions(token_hash,user_id,expires_at) VALUES (?,?,?)",
                (token_hash, user_id, expires_at.isoformat()),
            )

    def get_user_by_session(self, token_hash: str) -> User | None:
        now = iso_now()
        with self._lock:
            row = self._conn.execute(
                """SELECT u.id,u.name,u.email,u.created_at
                   FROM sessions s JOIN users u ON u.id=s.user_id
                   WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at > ?""",
                (token_hash, now),
            ).fetchone()
        return self._user_from_row(row) if row else None

    def revoke_session(self, token_hash: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE sessions SET revoked_at=? WHERE token_hash=?", (iso_now(), token_hash)
            )

    def update_password(self, user_id: str, password_hash: str) -> None:
        with self._transaction() as conn:
            result = conn.execute(
                "UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id)
            )
            if result.rowcount != 1:
                raise KeyError(f"Unknown user: {user_id}")

    def revoke_user_sessions(self, user_id: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (iso_now(), user_id),
            )

    # ---- cases --------------------------------------------------------------
    def create_case(self, owner_id: str, goal: str, title: str | None = None) -> CivicCase:
        case_id = str(uuid.uuid4())
        now = iso_now()
        clean_title = (title or self._title_from_goal(goal)).strip()[:160]
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO cases(
                       id,owner_id,title,goal,notes,status,version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,'saved',1,?,?)""",
                (case_id, owner_id, clean_title, goal.strip(), "", now, now),
            )
            conn.execute(
                "INSERT INTO usage_cases(case_id,owner_id) VALUES (?,?)", (case_id, owner_id)
            )
            self._add_event_conn(conn, owner_id, case_id, "case.created", "Case saved")
        return self.get_case(owner_id, case_id)

    def list_cases(self, owner_id: str, limit: int = 50) -> list[CivicCase]:
        limit = max(1, min(limit, 100))
        with self._lock:
            rows = self._conn.execute(
                """SELECT id,title,goal,notes,status,version,created_at,updated_at
                   FROM cases WHERE owner_id=? ORDER BY updated_at DESC LIMIT ?""",
                (owner_id, limit),
            ).fetchall()
        return [self._case_from_row(row) for row in rows]

    def get_case(self, owner_id: str, case_id: str) -> CivicCase:
        with self._lock:
            row = self._conn.execute(
                """SELECT id,title,goal,notes,status,version,created_at,updated_at
                   FROM cases WHERE id=? AND owner_id=?""",
                (case_id, owner_id),
            ).fetchone()
        if row is None:
            raise NotFoundError("Case not found")
        return self._case_from_row(row)

    def update_case(
        self,
        owner_id: str,
        case_id: str,
        *,
        version: int,
        goal: str | None = None,
        title: str | None = None,
        notes: str | None = None,
    ) -> CivicCase:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT goal,title,notes,version FROM cases WHERE id=? AND owner_id=?",
                (case_id, owner_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("Case not found")
            if int(row["version"]) != version:
                raise ConflictError("This case changed elsewhere. Reload before saving.")
            values = {
                "goal": row["goal"] if goal is None else goal.strip(),
                "title": row["title"] if title is None else title.strip()[:160],
                "notes": row["notes"] if notes is None else notes,
            }
            new_version = version + 1
            now = iso_now()
            conn.execute(
                """UPDATE cases SET goal=?,title=?,notes=?,version=?,updated_at=?
                   WHERE id=? AND owner_id=? AND version=?""",
                (
                    values["goal"],
                    values["title"],
                    values["notes"],
                    new_version,
                    now,
                    case_id,
                    owner_id,
                    version,
                ),
            )
            self._add_event_conn(conn, owner_id, case_id, "case.updated", "Case details updated")
        return self.get_case(owner_id, case_id)

    # ---- artifacts and events ----------------------------------------------
    def create_artifact(
        self, owner_id: str, case_id: str, name: str, content: str, kind: str
    ) -> Artifact:
        self.get_case(owner_id, case_id)
        artifact_id = str(uuid.uuid4())
        created_at = iso_now()
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO artifacts(id,case_id,owner_id,name,content,kind,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (artifact_id, case_id, owner_id, name.strip(), content, kind, created_at),
            )
            self._add_event_conn(
                conn, owner_id, case_id, "artifact.created", f"Saved {name.strip()}"
            )
        return Artifact(
            id=artifact_id,
            case_id=case_id,
            name=name.strip(),
            content=content,
            kind=kind,  # type: ignore[arg-type]
            created_at=parse_datetime(created_at),
        )

    def list_artifacts(self, owner_id: str, case_id: str) -> list[Artifact]:
        self.get_case(owner_id, case_id)
        with self._lock:
            rows = self._conn.execute(
                """SELECT id,case_id,name,content,kind,created_at
                   FROM artifacts WHERE case_id=? AND owner_id=? ORDER BY created_at DESC""",
                (case_id, owner_id),
            ).fetchall()
        return [self._artifact_from_row(row) for row in rows]

    def get_artifact(self, owner_id: str, case_id: str, artifact_id: str) -> Artifact:
        self.get_case(owner_id, case_id)
        with self._lock:
            row = self._conn.execute(
                """SELECT id,case_id,name,content,kind,created_at
                   FROM artifacts WHERE id=? AND case_id=? AND owner_id=?""",
                (artifact_id, case_id, owner_id),
            ).fetchone()
        if row is None:
            raise NotFoundError("Artifact not found")
        return self._artifact_from_row(row)

    def add_event(self, owner_id: str, case_id: str, event_type: str, message: str) -> TaskEvent:
        self.get_case(owner_id, case_id)
        with self._transaction() as conn:
            event_id = self._add_event_conn(conn, owner_id, case_id, event_type, message)
            row = conn.execute(
                "SELECT id,case_id,type,message,created_at FROM events WHERE id=?", (event_id,)
            ).fetchone()
        return self._event_from_row(row)

    def list_events(self, owner_id: str, case_id: str, limit: int = 100) -> list[TaskEvent]:
        self.get_case(owner_id, case_id)
        limit = max(1, min(limit, 250))
        with self._lock:
            rows = self._conn.execute(
                """SELECT id,case_id,type,message,created_at FROM events
                   WHERE case_id=? AND owner_id=? ORDER BY created_at ASC LIMIT ?""",
                (case_id, owner_id, limit),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    # ---- usage admission and accounting ------------------------------------
    def reserve_usage(
        self,
        *,
        owner_id: str,
        case_id: str,
        kind: str,
        amount_usd: float,
        global_budget_usd: float,
        browser_concurrency: int,
    ) -> str:
        if amount_usd < 0:
            raise ValueError("amount_usd cannot be negative")
        self.get_case(owner_id, case_id)
        reservation_id = str(uuid.uuid4())
        browser_active = 1 if kind == "browser" else 0
        now = iso_now()
        with self._transaction() as conn:
            global_row = conn.execute(
                "SELECT reserved_cost_usd,estimated_cost_usd,active_browser_sessions "
                "FROM usage_global WHERE id=1"
            ).fetchone()
            if global_row is None:
                raise RateLimitError("Usage controls are unavailable; paid work is paused")
            projected = (
                float(global_row["reserved_cost_usd"])
                + float(global_row["estimated_cost_usd"])
                + amount_usd
            )
            if projected > global_budget_usd + 1e-9:
                raise RateLimitError("This operation would exceed the shared usage budget")
            if browser_active and int(global_row["active_browser_sessions"]) >= browser_concurrency:
                raise RateLimitError("All browser slots are busy; try again shortly")
            conn.execute(
                """INSERT INTO usage_reservations
                   (id,case_id,owner_id,kind,amount_usd,browser_active,state,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    reservation_id,
                    case_id,
                    owner_id,
                    kind,
                    amount_usd,
                    browser_active,
                    "reserved",
                    now,
                ),
            )
            conn.execute(
                """UPDATE usage_global SET reserved_cost_usd=reserved_cost_usd+?,
                   active_browser_sessions=active_browser_sessions+? WHERE id=1""",
                (amount_usd, browser_active),
            )
            conn.execute(
                """UPDATE usage_cases SET reserved_cost_usd=reserved_cost_usd+?
                   WHERE case_id=? AND owner_id=?""",
                (amount_usd, case_id, owner_id),
            )
            self._add_event_conn(
                conn, owner_id, case_id, "usage.reserved", f"Reserved ${amount_usd:.4f} for {kind}"
            )
        return reservation_id

    def settle_usage(
        self,
        *,
        owner_id: str,
        reservation_id: str,
        actual_cost_usd: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
        browser_seconds: int = 0,
        item_kind: str | None = None,
    ) -> UsageItem:
        if actual_cost_usd < 0 or input_tokens < 0 or output_tokens < 0 or browser_seconds < 0:
            raise ValueError("usage values cannot be negative")
        with self._transaction() as conn:
            reservation = conn.execute(
                """SELECT id,case_id,owner_id,kind,amount_usd,browser_active,state
                   FROM usage_reservations WHERE id=? AND owner_id=?""",
                (reservation_id, owner_id),
            ).fetchone()
            if reservation is None:
                raise NotFoundError("Usage reservation not found")
            if reservation["state"] != "reserved":
                raise ConflictError("Usage reservation has already been settled")
            case_id = str(reservation["case_id"])
            amount_reserved = float(reservation["amount_usd"])
            browser_active = int(reservation["browser_active"])
            now = iso_now()
            item_id = str(uuid.uuid4())
            kind = item_kind or str(reservation["kind"])
            conn.execute(
                """UPDATE usage_reservations SET state='settled',settled_at=? WHERE id=?""",
                (now, reservation_id),
            )
            conn.execute(
                """UPDATE usage_global SET reserved_cost_usd=MAX(0,reserved_cost_usd-?),
                   estimated_cost_usd=estimated_cost_usd+?,
                   active_browser_sessions=MAX(0,active_browser_sessions-?) WHERE id=1""",
                (amount_reserved, actual_cost_usd, browser_active),
            )
            conn.execute(
                """UPDATE usage_cases SET reserved_cost_usd=MAX(0,reserved_cost_usd-?),
                   estimated_cost_usd=estimated_cost_usd+?,input_tokens=input_tokens+?,
                   output_tokens=output_tokens+?,browser_seconds=browser_seconds+?
                   WHERE case_id=? AND owner_id=?""",
                (
                    amount_reserved,
                    actual_cost_usd,
                    input_tokens,
                    output_tokens,
                    browser_seconds,
                    case_id,
                    owner_id,
                ),
            )
            conn.execute(
                """INSERT INTO usage_items
                   (id,case_id,owner_id,kind,estimated_cost_usd,input_tokens,output_tokens,browser_seconds,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    item_id,
                    case_id,
                    owner_id,
                    kind,
                    actual_cost_usd,
                    input_tokens,
                    output_tokens,
                    browser_seconds,
                    now,
                ),
            )
            self._add_event_conn(
                conn,
                owner_id,
                case_id,
                "usage.settled",
                f"Recorded {kind} usage (${actual_cost_usd:.4f})",
            )
            row = conn.execute(
                """SELECT id,case_id,owner_id,kind,estimated_cost_usd,input_tokens,
                   output_tokens,browser_seconds,created_at FROM usage_items WHERE id=?""",
                (item_id,),
            ).fetchone()
        return UsageItem(
            id=str(row["id"]),
            kind=str(row["kind"]),  # type: ignore[arg-type]
            estimated_cost_usd=float(row["estimated_cost_usd"]),
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
            browser_seconds=int(row["browser_seconds"]),
            created_at=parse_datetime(row["created_at"]),
        )

    def release_usage(self, *, owner_id: str, reservation_id: str) -> None:
        with self._transaction() as conn:
            reservation = conn.execute(
                """SELECT case_id,amount_usd,browser_active,state FROM usage_reservations
                   WHERE id=? AND owner_id=?""",
                (reservation_id, owner_id),
            ).fetchone()
            if reservation is None:
                raise NotFoundError("Usage reservation not found")
            if reservation["state"] != "reserved":
                return
            amount = float(reservation["amount_usd"])
            active = int(reservation["browser_active"])
            conn.execute(
                "UPDATE usage_reservations SET state='released',settled_at=? WHERE id=?",
                (iso_now(), reservation_id),
            )
            conn.execute(
                """UPDATE usage_global SET reserved_cost_usd=MAX(0,reserved_cost_usd-?),
                   active_browser_sessions=MAX(0,active_browser_sessions-?) WHERE id=1""",
                (amount, active),
            )
            conn.execute(
                """UPDATE usage_cases SET reserved_cost_usd=MAX(0,reserved_cost_usd-?)
                   WHERE case_id=? AND owner_id=?""",
                (amount, reservation["case_id"], owner_id),
            )

    def get_usage(self, owner_id: str, case_id: str) -> UsageSummary:
        self.get_case(owner_id, case_id)
        with self._lock:
            row = self._conn.execute(
                """SELECT case_id,estimated_cost_usd,reserved_cost_usd,input_tokens,
                   output_tokens,browser_seconds FROM usage_cases
                   WHERE case_id=? AND owner_id=?""",
                (case_id, owner_id),
            ).fetchone()
            item_rows = self._conn.execute(
                """SELECT id,kind,estimated_cost_usd,input_tokens,output_tokens,
                   browser_seconds,created_at
                   FROM usage_items WHERE case_id=? AND owner_id=? ORDER BY created_at DESC""",
                (case_id, owner_id),
            ).fetchall()
        if row is None:
            raise NotFoundError("Usage record not found")
        return UsageSummary(
            case_id=str(row["case_id"]),
            estimated_cost_usd=float(row["estimated_cost_usd"]),
            reserved_cost_usd=float(row["reserved_cost_usd"]),
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
            browser_seconds=int(row["browser_seconds"]),
            items=[
                UsageItem(
                    id=str(item["id"]),
                    kind=str(item["kind"]),  # type: ignore[arg-type]
                    estimated_cost_usd=float(item["estimated_cost_usd"]),
                    input_tokens=int(item["input_tokens"]),
                    output_tokens=int(item["output_tokens"]),
                    browser_seconds=int(item["browser_seconds"]),
                    created_at=parse_datetime(item["created_at"]),
                )
                for item in item_rows
            ],
        )

    def get_global_usage(self) -> dict[str, float | int]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM usage_global WHERE id=1").fetchone()
        if row is None:
            raise RateLimitError("Usage controls are unavailable")
        return {
            "estimated_cost_usd": float(row["estimated_cost_usd"]),
            "reserved_cost_usd": float(row["reserved_cost_usd"]),
            "active_browser_sessions": int(row["active_browser_sessions"]),
        }

    # ---- helpers ------------------------------------------------------------
    @staticmethod
    def _title_from_goal(goal: str) -> str:
        words = " ".join(goal.split()).strip()
        if not words:
            return "Untitled civic request"
        return words[:157] + "…" if len(words) > 160 else words

    @staticmethod
    def _user_from_row(row: sqlite3.Row) -> User:
        return User(
            id=str(row["id"]),
            name=str(row["name"]),
            email=str(row["email"]),
            created_at=parse_datetime(row["created_at"]),
        )

    @staticmethod
    def _case_from_row(row: sqlite3.Row) -> CivicCase:
        return CivicCase(
            id=str(row["id"]),
            title=str(row["title"]),
            goal=str(row["goal"]),
            notes=str(row["notes"] or ""),
            status=str(row["status"]),  # type: ignore[arg-type]
            created_at=parse_datetime(row["created_at"]),
            updated_at=parse_datetime(row["updated_at"]),
            version=int(row["version"]),
        )

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> Artifact:
        return Artifact(
            id=str(row["id"]),
            case_id=str(row["case_id"]),
            name=str(row["name"]),
            content=str(row["content"]),
            kind=str(row["kind"]),  # type: ignore[arg-type]
            created_at=parse_datetime(row["created_at"]),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> TaskEvent:
        return TaskEvent(
            id=str(row["id"]),
            case_id=str(row["case_id"]),
            type=str(row["type"]),
            message=str(row["message"]),
            created_at=parse_datetime(row["created_at"]),
        )

    @staticmethod
    def _add_event_conn(
        conn: sqlite3.Connection, owner_id: str, case_id: str, event_type: str, message: str
    ) -> str:
        event_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO events(id,case_id,owner_id,type,message,created_at) VALUES (?,?,?,?,?,?)",
            (event_id, case_id, owner_id, event_type, message, iso_now()),
        )
        return event_id


class DynamoStore:
    """Owner-scoped single-table DynamoDB adapter for the cloud deployment.

    The table uses only ``pk`` and ``sk`` keys. Public cases are deliberately
    not in this initial cloud adapter; every record is private to a Cognito subject.
    Usage admission uses a DynamoDB transaction so a concurrent request cannot
    spend the same budget or browser slot twice.
    """

    def __init__(
        self,
        table_name: str,
        region: str,
        budget_limit_usd: float = 80.0,
        browser_concurrency: int = 2,
    ):
        try:
            import boto3
            from boto3.dynamodb.conditions import Key
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("boto3 is required for DynamoDB storage") from exc
        self._key = Key
        self._budget_limit_usd = budget_limit_usd
        self._browser_concurrency = browser_concurrency
        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        self._client = self._table.meta.client
        try:
            self._table.put_item(
                Item={
                    "pk": "GLOBAL",
                    "sk": "COUNTERS",
                    "entity": "global_usage",
                    "estimated_cost_usd": 0,
                    "reserved_cost_usd": 0,
                    "budget_remaining": self._decimal(budget_limit_usd),
                    "active_browser_sessions": 0,
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            # The shared counter already exists on a warm or subsequent cold
            # start. The conditional write is safe and idempotent.
            pass

    def close(self) -> None:
        return None

    def _query(
        self, partition: str, prefix: str | None = None, *, ascending: bool = True
    ) -> list[dict[str, Any]]:
        condition = self._key("pk").eq(partition)
        if prefix:
            condition &= self._key("sk").begins_with(prefix)
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": condition,
            "ScanIndexForward": ascending,
        }
        items: list[dict[str, Any]] = []
        while True:
            response = self._table.query(**kwargs)
            items.extend(response.get("Items", []))
            if not response.get("LastEvaluatedKey"):
                return items
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    # ---- users and sessions -------------------------------------------------
    def create_user(self, name: str, email: str, password_hash: str) -> User:
        # Cognito is required for cloud mode; retaining this method keeps the
        # storage protocol explicit and prevents accidental local-password use.
        raise RuntimeError("Cloud mode creates users through Cognito")

    def get_user_by_email(self, email: str) -> tuple[User, str] | None:
        return None

    def get_user(self, user_id: str) -> User | None:
        return None

    def create_session(self, token_hash: str, user_id: str, expires_at: datetime) -> None:
        raise RuntimeError("Cloud mode uses Cognito sessions")

    def get_user_by_session(self, token_hash: str) -> User | None:
        return None

    def revoke_session(self, token_hash: str) -> None:
        return None

    def update_password(self, user_id: str, password_hash: str) -> None:
        raise RuntimeError("Cloud mode uses Cognito password recovery")

    def revoke_user_sessions(self, user_id: str) -> None:
        raise RuntimeError("Cloud mode uses Cognito sessions")

    # ---- cases --------------------------------------------------------------
    def create_case(self, owner_id: str, goal: str, title: str | None = None) -> CivicCase:
        case_id = str(uuid.uuid4())
        now = iso_now()
        clean_title = (title or SQLiteStore._title_from_goal(goal)).strip()[:160]
        item = {
            "pk": f"USER#{owner_id}",
            "sk": f"CASE#{case_id}",
            "entity": "case",
            "owner_id": owner_id,
            "id": case_id,
            "title": clean_title,
            "goal": goal.strip(),
            "notes": "",
            "status": "saved",
            "version": 1,
            "created_at": now,
            "updated_at": now,
        }
        self._table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
        )
        self._table.put_item(
            Item={
                "pk": f"USER#{owner_id}",
                "sk": f"USAGE#{case_id}",
                "entity": "usage_case",
                "owner_id": owner_id,
                "case_id": case_id,
                "estimated_cost_usd": 0,
                "reserved_cost_usd": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "browser_seconds": 0,
            }
        )
        self.add_event(owner_id, case_id, "case.created", "Case saved")
        return self._case_from_item(item)

    def list_cases(self, owner_id: str, limit: int = 50) -> list[CivicCase]:
        items = self._query(f"USER#{owner_id}", "CASE#", ascending=False)
        cases = [self._case_from_item(item) for item in items]
        return sorted(cases, key=lambda case: case.updated_at, reverse=True)[
            : max(1, min(limit, 100))
        ]

    def get_case(self, owner_id: str, case_id: str) -> CivicCase:
        item = self._table.get_item(Key={"pk": f"USER#{owner_id}", "sk": f"CASE#{case_id}"}).get(
            "Item"
        )
        if not item:
            raise NotFoundError("Case not found")
        return self._case_from_item(item)

    def update_case(
        self,
        owner_id: str,
        case_id: str,
        *,
        version: int,
        goal: str | None = None,
        title: str | None = None,
        notes: str | None = None,
    ) -> CivicCase:
        existing = self._table.get_item(
            Key={"pk": f"USER#{owner_id}", "sk": f"CASE#{case_id}"}
        ).get("Item")
        if not existing:
            raise NotFoundError("Case not found")
        names = {"#version": "version"}
        values: dict[str, Any] = {":expected": version, ":one": 1, ":now": iso_now()}
        sets = ["#version = #version + :one", "updated_at = :now"]
        if goal is not None:
            sets.append("goal = :goal")
            values[":goal"] = goal.strip()
        if title is not None:
            sets.append("title = :title")
            values[":title"] = title.strip()[:160]
        if notes is not None:
            sets.append("notes = :notes")
            values[":notes"] = notes
        try:
            response = self._table.update_item(
                Key={"pk": f"USER#{owner_id}", "sk": f"CASE#{case_id}"},
                UpdateExpression="SET " + ", ".join(sets),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ConditionExpression="#version = :expected",
                ReturnValues="ALL_NEW",
            )
        except self._client.exceptions.ConditionalCheckFailedException as exc:
            raise ConflictError("This case changed elsewhere. Reload before saving.") from exc
        self.add_event(owner_id, case_id, "case.updated", "Case details updated")
        return self._case_from_item(response["Attributes"])

    # ---- artifacts and events ----------------------------------------------
    def create_artifact(
        self, owner_id: str, case_id: str, name: str, content: str, kind: str
    ) -> Artifact:
        self.get_case(owner_id, case_id)
        item_id = str(uuid.uuid4())
        now = iso_now()
        item = {
            "pk": f"CASE#{case_id}",
            "sk": f"ARTIFACT#{now}#{item_id}",
            "entity": "artifact",
            "owner_id": owner_id,
            "case_id": case_id,
            "id": item_id,
            "name": name.strip(),
            "content": content,
            "kind": kind,
            "created_at": now,
        }
        self._table.put_item(Item=item)
        self.add_event(owner_id, case_id, "artifact.created", f"Saved {name.strip()}")
        return self._artifact_from_item(item)

    def list_artifacts(self, owner_id: str, case_id: str) -> list[Artifact]:
        self.get_case(owner_id, case_id)
        response = self._table.query(
            KeyConditionExpression=self._key("pk").eq(f"CASE#{case_id}")
            & self._key("sk").begins_with("ARTIFACT#"),
            ScanIndexForward=False,
        )
        return [
            self._artifact_from_item(item)
            for item in response.get("Items", [])
            if item.get("owner_id") == owner_id
        ]

    def get_artifact(self, owner_id: str, case_id: str, artifact_id: str) -> Artifact:
        self.get_case(owner_id, case_id)
        response = self._table.query(
            KeyConditionExpression=self._key("pk").eq(f"CASE#{case_id}")
            & self._key("sk").begins_with("ARTIFACT#"),
        )
        for item in response.get("Items", []):
            if item.get("id") == artifact_id and item.get("owner_id") == owner_id:
                return self._artifact_from_item(item)
        raise NotFoundError("Artifact not found")

    def add_event(self, owner_id: str, case_id: str, event_type: str, message: str) -> TaskEvent:
        self.get_case(owner_id, case_id)
        event_id = str(uuid.uuid4())
        now = iso_now()
        item = {
            "pk": f"CASE#{case_id}",
            "sk": f"EVENT#{now}#{event_id}",
            "entity": "event",
            "owner_id": owner_id,
            "case_id": case_id,
            "id": event_id,
            "type": event_type,
            "message": message,
            "created_at": now,
        }
        self._table.put_item(Item=item)
        return self._event_from_item(item)

    def list_events(self, owner_id: str, case_id: str, limit: int = 100) -> list[TaskEvent]:
        self.get_case(owner_id, case_id)
        response = self._table.query(
            KeyConditionExpression=self._key("pk").eq(f"CASE#{case_id}")
            & self._key("sk").begins_with("EVENT#"),
            ScanIndexForward=True,
            Limit=max(1, min(limit, 250)),
        )
        return [
            self._event_from_item(item)
            for item in response.get("Items", [])
            if item.get("owner_id") == owner_id
        ]

    # ---- usage --------------------------------------------------------------
    def reserve_usage(
        self,
        *,
        owner_id: str,
        case_id: str,
        kind: str,
        amount_usd: float,
        global_budget_usd: float,
        browser_concurrency: int,
    ) -> str:
        self.get_case(owner_id, case_id)
        reservation_id = str(uuid.uuid4())
        browser_active = 1 if kind == "browser" else 0
        amount = self._decimal(amount_usd)
        condition = "budget_remaining >= :amount"
        expression_values: dict[str, Any] = {":amount": amount}
        if browser_active:
            condition += " AND active_browser_sessions < :concurrency"
            expression_values[":concurrency"] = browser_concurrency
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._table.name,
                            "Item": {
                                "pk": f"RESERVATION#{reservation_id}",
                                "sk": "RECORD",
                                "entity": "usage_reservation",
                                "id": reservation_id,
                                "case_id": case_id,
                                "owner_id": owner_id,
                                "kind": kind,
                                "amount_usd": amount,
                                "browser_active": browser_active,
                                "state": "reserved",
                                "created_at": iso_now(),
                            },
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    {
                        "Update": {
                            "TableName": self._table.name,
                            "Key": {"pk": "GLOBAL", "sk": "COUNTERS"},
                            "UpdateExpression": "SET reserved_cost_usd = reserved_cost_usd + :amount, budget_remaining = budget_remaining - :amount, active_browser_sessions = active_browser_sessions + :browser",
                            "ConditionExpression": condition,
                            "ExpressionAttributeValues": {
                                **expression_values,
                                ":browser": browser_active,
                            },
                        }
                    },
                    {
                        "Update": {
                            "TableName": self._table.name,
                            "Key": {"pk": f"USER#{owner_id}", "sk": f"USAGE#{case_id}"},
                            "UpdateExpression": "SET reserved_cost_usd = reserved_cost_usd + :amount",
                            "ExpressionAttributeValues": {":amount": amount},
                        }
                    },
                ]
            )
        except self._client.exceptions.TransactionCanceledException as exc:
            raise RateLimitError(
                "This operation would exceed the shared usage budget or available browser slots"
            ) from exc
        self.add_event(
            owner_id, case_id, "usage.reserved", f"Reserved ${amount_usd:.4f} for {kind}"
        )
        return reservation_id

    def settle_usage(
        self,
        *,
        owner_id: str,
        reservation_id: str,
        actual_cost_usd: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
        browser_seconds: int = 0,
        item_kind: str | None = None,
    ) -> UsageItem:
        reservation = self._table.get_item(
            Key={"pk": f"RESERVATION#{reservation_id}", "sk": "RECORD"}
        ).get("Item")
        if not reservation or reservation.get("owner_id") != owner_id:
            raise NotFoundError("Usage reservation not found")
        if reservation.get("state") != "reserved":
            raise ConflictError("Usage reservation has already been settled")
        case_id = str(reservation["case_id"])
        amount_reserved = self._decimal(reservation["amount_usd"])
        actual = self._decimal(actual_cost_usd)
        browser_active = int(reservation.get("browser_active", 0))
        item_id = str(uuid.uuid4())
        now = iso_now()
        kind = item_kind or str(reservation["kind"])
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self._table.name,
                            "Key": {"pk": f"RESERVATION#{reservation_id}", "sk": "RECORD"},
                            "UpdateExpression": "SET #state = :settled, settled_at = :now",
                            "ConditionExpression": "#state = :reserved",
                            "ExpressionAttributeNames": {"#state": "state"},
                            "ExpressionAttributeValues": {
                                ":settled": "settled",
                                ":reserved": "reserved",
                                ":now": now,
                            },
                        }
                    },
                    {
                        "Update": {
                            "TableName": self._table.name,
                            "Key": {"pk": "GLOBAL", "sk": "COUNTERS"},
                            "UpdateExpression": "SET reserved_cost_usd = reserved_cost_usd - :reserved, estimated_cost_usd = estimated_cost_usd + :actual, budget_remaining = budget_remaining + :refund, active_browser_sessions = active_browser_sessions - :browser",
                            "ExpressionAttributeValues": {
                                ":reserved": amount_reserved,
                                ":actual": actual,
                                ":refund": amount_reserved - actual,
                                ":browser": browser_active,
                            },
                        }
                    },
                    {
                        "Update": {
                            "TableName": self._table.name,
                            "Key": {"pk": f"USER#{owner_id}", "sk": f"USAGE#{case_id}"},
                            "UpdateExpression": "SET reserved_cost_usd = reserved_cost_usd - :reserved, estimated_cost_usd = estimated_cost_usd + :actual, input_tokens = input_tokens + :input, output_tokens = output_tokens + :output, browser_seconds = browser_seconds + :seconds",
                            "ExpressionAttributeValues": {
                                ":reserved": amount_reserved,
                                ":actual": actual,
                                ":input": input_tokens,
                                ":output": output_tokens,
                                ":seconds": browser_seconds,
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table.name,
                            "Item": {
                                "pk": f"CASE#{case_id}",
                                "sk": f"USAGEITEM#{now}#{item_id}",
                                "entity": "usage_item",
                                "id": item_id,
                                "case_id": case_id,
                                "owner_id": owner_id,
                                "kind": kind,
                                "estimated_cost_usd": actual,
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "browser_seconds": browser_seconds,
                                "created_at": now,
                            },
                        }
                    },
                ]
            )
        except self._client.exceptions.TransactionCanceledException as exc:
            raise ConflictError("Usage reservation has already been settled") from exc
        self.add_event(
            owner_id, case_id, "usage.settled", f"Recorded {kind} usage (${actual_cost_usd:.4f})"
        )
        return UsageItem(
            id=item_id,
            kind=kind,
            estimated_cost_usd=actual_cost_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            browser_seconds=browser_seconds,
            created_at=parse_datetime(now),
        )

    def release_usage(self, *, owner_id: str, reservation_id: str) -> None:
        reservation = self._table.get_item(
            Key={"pk": f"RESERVATION#{reservation_id}", "sk": "RECORD"}
        ).get("Item")
        if not reservation or reservation.get("owner_id") != owner_id:
            raise NotFoundError("Usage reservation not found")
        if reservation.get("state") != "reserved":
            return
        amount = self._decimal(reservation["amount_usd"])
        browser_active = int(reservation.get("browser_active", 0))
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self._table.name,
                            "Key": {"pk": f"RESERVATION#{reservation_id}", "sk": "RECORD"},
                            "UpdateExpression": "SET #state = :released, settled_at = :now",
                            "ConditionExpression": "#state = :reserved",
                            "ExpressionAttributeNames": {"#state": "state"},
                            "ExpressionAttributeValues": {
                                ":released": "released",
                                ":reserved": "reserved",
                                ":now": iso_now(),
                            },
                        }
                    },
                    {
                        "Update": {
                            "TableName": self._table.name,
                            "Key": {"pk": "GLOBAL", "sk": "COUNTERS"},
                            "UpdateExpression": "SET reserved_cost_usd = reserved_cost_usd - :amount, budget_remaining = budget_remaining + :amount, active_browser_sessions = active_browser_sessions - :browser",
                            "ExpressionAttributeValues": {
                                ":amount": amount,
                                ":browser": browser_active,
                            },
                        }
                    },
                    {
                        "Update": {
                            "TableName": self._table.name,
                            "Key": {
                                "pk": f"USER#{owner_id}",
                                "sk": f"USAGE#{reservation['case_id']}",
                            },
                            "UpdateExpression": "SET reserved_cost_usd = reserved_cost_usd - :amount",
                            "ExpressionAttributeValues": {":amount": amount},
                        }
                    },
                ]
            )
        except self._client.exceptions.TransactionCanceledException as exc:
            raise ConflictError("Usage reservation has already been released") from exc

    def get_usage(self, owner_id: str, case_id: str) -> UsageSummary:
        self.get_case(owner_id, case_id)
        item = self._table.get_item(Key={"pk": f"USER#{owner_id}", "sk": f"USAGE#{case_id}"}).get(
            "Item"
        )
        if not item:
            raise NotFoundError("Usage record not found")
        response = self._table.query(
            KeyConditionExpression=self._key("pk").eq(f"CASE#{case_id}")
            & self._key("sk").begins_with("USAGEITEM#"),
            ScanIndexForward=False,
        )
        return UsageSummary(
            case_id=case_id,
            estimated_cost_usd=self._number(item.get("estimated_cost_usd", 0)),
            reserved_cost_usd=self._number(item.get("reserved_cost_usd", 0)),
            input_tokens=int(item.get("input_tokens", 0)),
            output_tokens=int(item.get("output_tokens", 0)),
            browser_seconds=int(item.get("browser_seconds", 0)),
            items=[
                self._usage_from_item(row)
                for row in response.get("Items", [])
                if row.get("owner_id") == owner_id
            ],
        )

    def get_global_usage(self) -> dict[str, float | int]:
        item = self._table.get_item(Key={"pk": "GLOBAL", "sk": "COUNTERS"}).get("Item")
        if not item:
            raise RateLimitError("Usage controls are unavailable")
        return {
            "estimated_cost_usd": self._number(item.get("estimated_cost_usd", 0)),
            "reserved_cost_usd": self._number(item.get("reserved_cost_usd", 0)),
            "active_browser_sessions": int(item.get("active_browser_sessions", 0)),
        }

    # ---- conversion helpers ------------------------------------------------
    @staticmethod
    def _decimal(value: Any):
        from decimal import Decimal

        return Decimal(str(value))

    @staticmethod
    def _number(value: Any) -> float:
        return float(value)

    @staticmethod
    def _case_from_item(item: dict[str, Any]) -> CivicCase:
        return CivicCase(
            id=str(item["id"]),
            title=str(item["title"]),
            goal=str(item["goal"]),
            notes=str(item.get("notes") or ""),
            status=str(item.get("status", "saved")),  # type: ignore[arg-type]
            created_at=parse_datetime(str(item["created_at"])),
            updated_at=parse_datetime(str(item["updated_at"])),
            version=int(item.get("version", 1)),
        )

    @staticmethod
    def _artifact_from_item(item: dict[str, Any]) -> Artifact:
        return Artifact(
            id=str(item["id"]),
            case_id=str(item["case_id"]),
            name=str(item["name"]),
            content=str(item["content"]),
            kind=str(item["kind"]),
            created_at=parse_datetime(str(item["created_at"])),
        )  # type: ignore[arg-type]

    @staticmethod
    def _event_from_item(item: dict[str, Any]) -> TaskEvent:
        return TaskEvent(
            id=str(item["id"]),
            case_id=str(item["case_id"]),
            type=str(item["type"]),
            message=str(item["message"]),
            created_at=parse_datetime(str(item["created_at"])),
        )

    @classmethod
    def _usage_from_item(cls, item: dict[str, Any]) -> UsageItem:
        return UsageItem(
            id=str(item["id"]),
            kind=str(item["kind"]),
            estimated_cost_usd=cls._number(item.get("estimated_cost_usd", 0)),
            input_tokens=int(item.get("input_tokens", 0)),
            output_tokens=int(item.get("output_tokens", 0)),
            browser_seconds=int(item.get("browser_seconds", 0)),
            created_at=parse_datetime(str(item["created_at"])),
        )  # type: ignore[arg-type]
