"""Local persistence for the CivitasX Agent, tickets, and civic Feed.

This adapter deliberately uses SQLite and the local filesystem during the
prototype.  Its methods are the contract that a DynamoDB/S3 adapter can
implement when the product moves to AWS.  Public posts never contain a link to
the private conversation; they are redacted snapshots with their own IDs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import ConflictError, NotFoundError, RateLimitError
from .models import (
    AgentMessage,
    AgentMessageRole,
    AgentThread,
    AgentThreadDetail,
    AgentThreadStatus,
    Attachment,
    Checkpoint,
    CivicPost,
    Comment,
    ComplaintTicket,
    FollowSubject,
    PreparationApproval,
    Report,
    ShareSnapshot,
    SourceEvidence,
    TicketDetail,
    TicketPreparation,
    TicketStatus,
    TicketStatusEvent,
    TicketVisibility,
    VoteResponse,
)

MESSAGE_PART_TYPES = {"text", "attachment", "citation", "action", "tool", "command", "status"}


def normalize_message_parts(raw_parts: Any) -> list[dict[str, Any]]:
    """Keep old persisted message parts readable after schema additions."""

    if not isinstance(raw_parts, list):
        return []
    normalized: list[dict[str, Any]] = []
    for raw in raw_parts:
        if not isinstance(raw, dict):
            continue
        part = dict(raw)
        part_type = str(part.get("type", "text"))
        if part_type == "metadata":
            part["type"] = "status"
            part.setdefault("text", "client message marker")
        elif part_type not in MESSAGE_PART_TYPES:
            part["type"] = "text"
        normalized.append(part)
    return normalized


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_datetime(value: str | None) -> datetime:
    return datetime.fromisoformat(value) if value else utc_now()


def redact_public_text(value: str) -> str:
    """Mask common contact details before a ticket becomes public."""

    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted email]", value)
    value = re.sub(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)", "[redacted phone]", value)
    value = re.sub(
        r"\b(?:aadhaar|passport|pan)\s*[:#-]?\s*[A-Za-z0-9 -]{6,20}\b",
        "[redacted ID]",
        value,
        flags=re.IGNORECASE,
    )
    return value


SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_threads (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  case_id TEXT,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_threads_owner_updated_idx
  ON agent_threads(owner_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_messages (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  parts_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_messages_thread_created_idx
  ON agent_messages(thread_id, created_at ASC);

CREATE TABLE IF NOT EXISTS community_attachments (
  id TEXT PRIMARY KEY,
  thread_id TEXT,
  case_id TEXT,
  owner_id TEXT NOT NULL,
  filename TEXT NOT NULL,
  content_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  storage_key TEXT NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'private',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS community_attachments_thread_idx
  ON community_attachments(owner_id, thread_id, created_at DESC);

CREATE TABLE IF NOT EXISTS complaint_tickets (
  id TEXT PRIMARY KEY,
  civitas_ticket_id TEXT NOT NULL UNIQUE,
  thread_id TEXT,
  case_id TEXT,
  owner_id TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  authority_id TEXT,
  locality TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  visibility TEXT NOT NULL DEFAULT 'private',
  external_reference_id TEXT,
  acknowledgement TEXT,
  tracking_url TEXT,
  submitted_content_hash TEXT,
  last_checkpoint_id TEXT,
  public_post_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS complaint_tickets_owner_updated_idx
  ON complaint_tickets(owner_id, updated_at DESC);

-- Ticket numbers are allocated from a monotonic per-year sequence.  Keeping
-- the allocator separate from complaint_tickets means deletes never recycle
-- an ID and BEGIN IMMEDIATE serializes concurrent allocators.
CREATE TABLE IF NOT EXISTS ticket_id_sequences (
  year INTEGER PRIMARY KEY,
  last_value INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ticket_status_history (
  id TEXT PRIMARY KEY,
  ticket_id TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  status TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ticket_status_history_ticket_idx
  ON ticket_status_history(ticket_id, created_at ASC);

CREATE TABLE IF NOT EXISTS public_posts (
  id TEXT PRIMARY KEY,
  ticket_id TEXT NOT NULL,
  civitas_ticket_id TEXT NOT NULL,
  owner_id TEXT,
  author_name TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  locality TEXT,
  visibility TEXT NOT NULL,
  status TEXT NOT NULL,
  authority_id TEXT,
  vote_score INTEGER NOT NULL DEFAULT 0,
  upvotes INTEGER NOT NULL DEFAULT 0,
  downvotes INTEGER NOT NULL DEFAULT 0,
  comment_count INTEGER NOT NULL DEFAULT 0,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  is_demo INTEGER NOT NULL DEFAULT 0,
  moderation_state TEXT NOT NULL DEFAULT 'visible',
  locked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS public_posts_ticket_idx ON public_posts(ticket_id);
CREATE INDEX IF NOT EXISTS public_posts_feed_idx
  ON public_posts(visibility, status, created_at DESC);

-- A public post owns an immutable evidence snapshot.  Never reach back into
-- the owner's private thread when a public viewer asks for evidence.
CREATE TABLE IF NOT EXISTS public_post_evidence (
  post_id TEXT NOT NULL,
  evidence_id TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(post_id, evidence_id)
);
CREATE INDEX IF NOT EXISTS public_post_evidence_post_idx
  ON public_post_evidence(post_id, created_at ASC);

CREATE TABLE IF NOT EXISTS public_post_attachments (
  post_id TEXT NOT NULL,
  attachment_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(post_id, attachment_id)
);

CREATE TABLE IF NOT EXISTS post_votes (
  post_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  value INTEGER NOT NULL CHECK(value IN (-1, 1)),
  created_at TEXT NOT NULL,
  PRIMARY KEY(post_id, user_id)
);

CREATE TABLE IF NOT EXISTS post_follows (
  post_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(post_id, user_id)
);

CREATE TABLE IF NOT EXISTS post_comments (
  id TEXT PRIMARY KEY,
  post_id TEXT NOT NULL,
  owner_id TEXT,
  author_name TEXT NOT NULL,
  body TEXT NOT NULL,
  parent_id TEXT,
  is_demo INTEGER NOT NULL DEFAULT 0,
  moderation_state TEXT NOT NULL DEFAULT 'visible',
  deleted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS post_comments_post_created_idx
  ON post_comments(post_id, created_at ASC);

CREATE TABLE IF NOT EXISTS community_notifications (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  post_id TEXT,
  ticket_id TEXT,
  read INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS community_notifications_owner_created_idx
  ON community_notifications(owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS post_saves (
  post_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(post_id, user_id)
);

CREATE TABLE IF NOT EXISTS post_mutes (
  post_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(post_id, user_id)
);

CREATE TABLE IF NOT EXISTS subject_follows (
  subject_type TEXT NOT NULL CHECK(subject_type IN ('topic','authority','locality')),
  subject_value TEXT NOT NULL,
  user_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(subject_type, subject_value, user_id)
);
CREATE INDEX IF NOT EXISTS subject_follows_user_idx
  ON subject_follows(user_id, subject_type, created_at DESC);

CREATE TABLE IF NOT EXISTS civic_reports (
  id TEXT PRIMARY KEY,
  target_type TEXT NOT NULL CHECK(target_type IN ('post','comment')),
  target_id TEXT NOT NULL,
  reporter_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  details TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(target_type, target_id, reporter_id)
);
CREATE INDEX IF NOT EXISTS civic_reports_status_idx
  ON civic_reports(status, created_at DESC);

CREATE TABLE IF NOT EXISTS moderation_actions (
  id TEXT PRIMARY KEY,
  moderator_id TEXT NOT NULL,
  target_type TEXT NOT NULL CHECK(target_type IN ('post','comment','report')),
  target_id TEXT NOT NULL,
  action TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS moderation_actions_target_idx
  ON moderation_actions(target_type, target_id, created_at DESC);

CREATE TABLE IF NOT EXISTS community_action_events (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS community_action_events_user_idx
  ON community_action_events(user_id, action, created_at DESC);

CREATE TABLE IF NOT EXISTS share_snapshots (
  token TEXT PRIMARY KEY,
  post_id TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  locality TEXT,
  visibility TEXT NOT NULL,
  civitas_ticket_id TEXT NOT NULL,
  status TEXT NOT NULL,
  authority_id TEXT,
  expires_at TEXT NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS share_snapshots_expiry_idx
  ON share_snapshots(expires_at, revoked);

CREATE TABLE IF NOT EXISTS ticket_preparations (
  id TEXT PRIMARY KEY,
  ticket_id TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  authority_id TEXT,
  fields_json TEXT NOT NULL,
  required_fields_json TEXT NOT NULL,
  missing_fields_json TEXT NOT NULL,
  attachment_ids_json TEXT NOT NULL,
  destination TEXT,
  content_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  approved_at TEXT,
  approval_expires_at TEXT
);
CREATE INDEX IF NOT EXISTS ticket_preparations_ticket_idx
  ON ticket_preparations(ticket_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ticket_checkpoints (
  id TEXT PRIMARY KEY,
  ticket_id TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  preparation_id TEXT,
  phase TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ticket_checkpoints_ticket_idx
  ON ticket_checkpoints(ticket_id, created_at ASC);
"""


class LocalCommunityStore:
    def __init__(self, database_path: str | Path, attachment_root: str | Path | None = None):
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.attachment_root = Path(attachment_root or self.database_path.parent / "attachments")
        self.attachment_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.database_path), check_same_thread=False, isolation_level=None, timeout=10
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(SCHEMA)
            self._ensure_schema_columns()
            self._seed_demo_feed()
            self._repair_demo_evidence_counts()
            self._migrate_generic_thread_titles()

    def _ensure_schema_columns(self) -> None:
        """Apply additive schema changes to a preview database created earlier.

        The local database is intentionally disposable, but keeping additive
        migrations here makes a running demo survive a hot reload or a code
        update without asking a tester to delete their tickets.
        """

        additions = {
            "complaint_tickets": {
                "acknowledgement": "TEXT",
                "tracking_url": "TEXT",
                "submitted_content_hash": "TEXT",
                "last_checkpoint_id": "TEXT",
            },
            "public_posts": {
                "moderation_state": "TEXT NOT NULL DEFAULT 'visible'",
                "locked": "INTEGER NOT NULL DEFAULT 0",
            },
            "post_comments": {
                "moderation_state": "TEXT NOT NULL DEFAULT 'visible'",
                "deleted_at": "TEXT",
            },
        }
        for table, columns in additions.items():
            existing = {
                str(row["name"])
                for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for column, definition in columns.items():
                if column not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        self._repair_comment_counts()
        self._backfill_ticket_id_sequences()

    def _repair_comment_counts(self) -> None:
        """Repair counters left behind by older versions of the preview."""

        self._conn.execute(
            """UPDATE public_posts
               SET comment_count=(
                   SELECT COUNT(*) FROM post_comments c
                   WHERE c.post_id=public_posts.id
                     AND c.moderation_state='visible'
                     AND c.deleted_at IS NULL
               )"""
        )

    def _repair_demo_evidence_counts(self) -> None:
        """Keep seeded evidence badges aligned with stored evidence snapshots."""

        self._conn.execute(
            """UPDATE public_posts
               SET evidence_count=(
                   SELECT COUNT(*) FROM public_post_evidence e
                   WHERE e.post_id=public_posts.id
               )
               WHERE is_demo=1"""
        )

    def _backfill_ticket_id_sequences(self) -> None:
        """Seed monotonic ticket allocators from IDs already in a database."""

        rows = self._conn.execute("SELECT civitas_ticket_id FROM complaint_tickets").fetchall()
        maximums: dict[int, int] = {}
        pattern = re.compile(r"^CX-BLR-(\d{4})-(\d+)$")
        for row in rows:
            match = pattern.match(str(row["civitas_ticket_id"]))
            if not match:
                continue
            year, value = int(match.group(1)), int(match.group(2))
            maximums[year] = max(maximums.get(year, 0), value)
        for year, value in maximums.items():
            self._conn.execute(
                """INSERT INTO ticket_id_sequences(year,last_value) VALUES (?,?)
                   ON CONFLICT(year) DO UPDATE SET last_value=MAX(last_value, excluded.last_value)""",
                (year, value),
            )

    def _migrate_generic_thread_titles(self) -> None:
        """Give older preview conversations useful titles once their first message exists."""

        rows = self._conn.execute(
            """SELECT t.id,
               (SELECT m.content FROM agent_messages m
                WHERE m.thread_id=t.id AND m.role='user'
                ORDER BY m.created_at ASC LIMIT 1) AS first_message
               FROM agent_threads t
               WHERE LOWER(t.title)='explore a bengaluru civic issue'"""
        ).fetchall()
        for row in rows:
            first = " ".join(str(row["first_message"] or "").split())[:70]
            if first:
                self._conn.execute(
                    "UPDATE agent_threads SET title=? WHERE id=?",
                    (first, str(row["id"])),
                )

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

    @staticmethod
    def _enforce_action_rate(
        conn: sqlite3.Connection,
        user_id: str,
        action: str,
        *,
        limit: int,
        window_seconds: int = 60,
    ) -> None:
        now = utc_now()
        cutoff = (now - timedelta(seconds=window_seconds)).isoformat()
        conn.execute(
            "DELETE FROM community_action_events WHERE created_at<?",
            (cutoff,),
        )
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM community_action_events WHERE user_id=? AND action=? AND created_at>=?",
            (user_id, action, cutoff),
        ).fetchone()["count"]
        if int(count) >= limit:
            raise RateLimitError("Too many community actions; try again shortly")
        conn.execute(
            "INSERT INTO community_action_events(id,user_id,action,created_at) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), user_id, action, now.isoformat()),
        )

    # ---- agent threads -----------------------------------------------------
    def create_thread(self, owner_id: str, title: str, case_id: str | None = None) -> AgentThread:
        thread_id = str(uuid.uuid4())
        now = iso_now()
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO agent_threads(id,owner_id,case_id,title,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (thread_id, owner_id, case_id, title.strip()[:160], "active", now, now),
            )
        return self.get_thread(owner_id, thread_id)

    def get_thread(self, owner_id: str, thread_id: str) -> AgentThread:
        with self._lock:
            row = self._conn.execute(
                """SELECT t.id,t.case_id,t.title,t.status,t.created_at,t.updated_at,
                   (SELECT COUNT(*) FROM agent_messages m WHERE m.thread_id=t.id) AS message_count,
                   (SELECT civitas_ticket_id FROM complaint_tickets ct WHERE ct.thread_id=t.id
                    ORDER BY ct.created_at DESC LIMIT 1) AS ticket_id
                   FROM agent_threads t WHERE t.id=? AND t.owner_id=?""",
                (thread_id, owner_id),
            ).fetchone()
        if row is None:
            raise NotFoundError("Agent thread not found")
        return self._thread_from_row(row)

    def list_threads(self, owner_id: str, limit: int = 30) -> list[AgentThread]:
        limit = max(1, min(limit, 100))
        with self._lock:
            rows = self._conn.execute(
                """SELECT t.id,t.case_id,t.title,t.status,t.created_at,t.updated_at,
                   (SELECT COUNT(*) FROM agent_messages m WHERE m.thread_id=t.id) AS message_count,
                   (SELECT civitas_ticket_id FROM complaint_tickets ct WHERE ct.thread_id=t.id
                    ORDER BY ct.created_at DESC LIMIT 1) AS ticket_id
                   FROM agent_threads t WHERE t.owner_id=? AND t.status!='archived' ORDER BY t.updated_at DESC LIMIT ?""",
                (owner_id, limit),
            ).fetchall()
        return [self._thread_from_row(row) for row in rows]

    def update_thread(
        self,
        owner_id: str,
        thread_id: str,
        *,
        title: str | None = None,
        status: AgentThreadStatus | None = None,
    ) -> AgentThread:
        """Rename or archive a conversation without touching its messages."""

        self.get_thread(owner_id, thread_id)
        updates: list[str] = []
        values: list[Any] = []
        if title is not None:
            clean_title = " ".join(title.strip().split())
            if not clean_title:
                raise ValueError("Conversation title cannot be empty")
            updates.append("title=?")
            values.append(clean_title[:160])
        if status is not None:
            updates.append("status=?")
            values.append(status.value)
        if updates:
            updates.append("updated_at=?")
            values.append(iso_now())
            values.extend([thread_id, owner_id])
            with self._transaction() as conn:
                conn.execute(
                    f"UPDATE agent_threads SET {', '.join(updates)} WHERE id=? AND owner_id=?",
                    values,
                )
        return self.get_thread(owner_id, thread_id)

    def add_message(
        self,
        owner_id: str,
        thread_id: str,
        role: AgentMessageRole,
        content: str,
        parts: list[dict[str, Any]] | None = None,
    ) -> AgentMessage:
        self.get_thread(owner_id, thread_id)
        message_id = str(uuid.uuid4())
        now = iso_now()
        parts_json = json.dumps(parts or [], ensure_ascii=False)
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO agent_messages(id,thread_id,owner_id,role,content,parts_json,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (message_id, thread_id, owner_id, role.value, content, parts_json, now),
            )
            conn.execute(
                "UPDATE agent_threads SET updated_at=? WHERE id=? AND owner_id=?",
                (now, thread_id, owner_id),
            )
        return AgentMessage(
            id=message_id,
            thread_id=thread_id,
            role=role,
            content=content,
            parts=parts or [],
            created_at=parse_datetime(now),
        )

    def get_thread_detail(self, owner_id: str, thread_id: str) -> AgentThreadDetail:
        thread = self.get_thread(owner_id, thread_id)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id,thread_id,role,content,parts_json,created_at FROM agent_messages "
                "WHERE thread_id=? AND owner_id=? ORDER BY created_at ASC",
                (thread_id, owner_id),
            ).fetchall()
        messages = []
        for row in rows:
            try:
                parts = normalize_message_parts(json.loads(row["parts_json"] or "[]"))
            except json.JSONDecodeError:
                parts = []
            messages.append(
                AgentMessage(
                    id=str(row["id"]),
                    thread_id=str(row["thread_id"]),
                    role=AgentMessageRole(str(row["role"])),
                    content=str(row["content"]),
                    parts=parts,
                    created_at=parse_datetime(str(row["created_at"])),
                )
            )
        return AgentThreadDetail(thread=thread, messages=messages)

    # ---- attachments -------------------------------------------------------
    def save_attachment(
        self,
        owner_id: str,
        *,
        filename: str,
        content_type: str,
        content: bytes,
        thread_id: str | None = None,
        case_id: str | None = None,
    ) -> Attachment:
        if thread_id:
            self.get_thread(owner_id, thread_id)
        if len(content) > 15 * 1024 * 1024:
            raise ValueError("Attachments must be 15 MB or smaller")
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).name).strip("-") or "upload"
        attachment_id = str(uuid.uuid4())
        digest = hashlib.sha256(content).hexdigest()
        storage_key = f"{owner_id}/{attachment_id}-{safe_name}"
        path = self.attachment_root / storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        now = iso_now()
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO community_attachments(
                   id,thread_id,case_id,owner_id,filename,content_type,size_bytes,sha256,
                   storage_key,visibility,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    attachment_id,
                    thread_id,
                    case_id,
                    owner_id,
                    safe_name,
                    content_type or "application/octet-stream",
                    len(content),
                    digest,
                    storage_key,
                    "private",
                    now,
                ),
            )
        return Attachment(
            id=attachment_id,
            thread_id=thread_id,
            case_id=case_id,
            owner_id=owner_id,
            filename=safe_name,
            content_type=content_type or "application/octet-stream",
            size_bytes=len(content),
            sha256=digest,
            storage_key=storage_key,
            visibility="private",
            created_at=parse_datetime(now),
        )

    def list_attachments(self, owner_id: str, thread_id: str) -> list[Attachment]:
        self.get_thread(owner_id, thread_id)
        with self._lock:
            rows = self._conn.execute(
                """SELECT id,thread_id,case_id,owner_id,filename,content_type,size_bytes,sha256,
                   storage_key,visibility,created_at FROM community_attachments
                   WHERE owner_id=? AND thread_id=? ORDER BY created_at DESC""",
                (owner_id, thread_id),
            ).fetchall()
        return [self._attachment_from_row(row) for row in rows]

    def get_attachment(self, owner_id: str, attachment_id: str) -> Attachment:
        with self._lock:
            row = self._conn.execute(
                """SELECT id,thread_id,case_id,owner_id,filename,content_type,size_bytes,sha256,
                   storage_key,visibility,created_at FROM community_attachments
                   WHERE id=? AND owner_id=?""",
                (attachment_id, owner_id),
            ).fetchone()
        if row is None:
            raise NotFoundError("Attachment not found")
        attachment = self._attachment_from_row(row)
        if not (self.attachment_root / attachment.storage_key).exists():
            raise NotFoundError("Attachment content is unavailable")
        return attachment

    def delete_attachment(self, owner_id: str, attachment_id: str) -> None:
        """Delete an uploaded file only while it is still an unreferenced draft."""

        attachment = self.get_attachment(owner_id, attachment_id)
        pattern = f'%"attachment_id": "{attachment_id}"%'
        with self._transaction() as conn:
            referenced = conn.execute(
                """SELECT 1 FROM agent_messages
                   WHERE owner_id=? AND thread_id=? AND parts_json LIKE ? LIMIT 1""",
                (owner_id, attachment.thread_id, pattern),
            ).fetchone()
            if referenced is None:
                referenced = conn.execute(
                    """SELECT 1 FROM ticket_preparations
                       WHERE owner_id=? AND attachment_ids_json LIKE ? LIMIT 1""",
                    (owner_id, f'%{attachment_id}%'),
                ).fetchone()
            if referenced is None:
                referenced = conn.execute(
                    """SELECT 1 FROM public_post_attachments a
                       JOIN public_posts p ON p.id=a.post_id
                       WHERE p.owner_id=? AND a.attachment_id=? LIMIT 1""",
                    (owner_id, attachment_id),
                ).fetchone()
            if referenced is not None:
                raise ConflictError("This attachment is already part of a saved civic record")
            cursor = conn.execute(
                "DELETE FROM community_attachments WHERE id=? AND owner_id=?",
                (attachment_id, owner_id),
            )
            if cursor.rowcount == 0:
                raise NotFoundError("Attachment not found")
        try:
            (self.attachment_root / attachment.storage_key).unlink(missing_ok=True)
        except OSError:
            # The metadata is gone; a later cleanup job can remove a stray file.
            pass

    def validate_attachment_ids(
        self, owner_id: str, thread_id: str, attachment_ids: list[str]
    ) -> list[Attachment]:
        """Return attachments owned by the user and bound to this thread.

        Message payloads carry attachment IDs, so the binding is checked again
        at send time. This keeps a copied ID from another thread or account
        from entering a conversation or later public snapshot.
        """

        attachments: list[Attachment] = []
        for attachment_id in attachment_ids:
            attachment = self.get_attachment(owner_id, attachment_id)
            if attachment.thread_id != thread_id:
                raise NotFoundError("Attachment is not part of this conversation")
            attachments.append(attachment)
        return attachments

    def attachment_path(self, attachment: Attachment) -> Path:
        path = (self.attachment_root / attachment.storage_key).resolve()
        root = self.attachment_root.resolve()
        if root != path and root not in path.parents:
            raise NotFoundError("Attachment not found")
        return path

    # ---- tickets -----------------------------------------------------------
    def create_ticket(
        self,
        owner_id: str,
        *,
        title: str,
        description: str,
        authority_id: str | None = None,
        locality: str | None = None,
        visibility: TicketVisibility = TicketVisibility.PRIVATE,
        thread_id: str | None = None,
        case_id: str | None = None,
    ) -> TicketDetail:
        if thread_id:
            self.get_thread(owner_id, thread_id)
        ticket_id = str(uuid.uuid4())
        year = utc_now().year
        now = iso_now()
        with self._transaction() as conn:
            prefix = f"CX-BLR-{year}-"
            sequence = conn.execute(
                """INSERT INTO ticket_id_sequences(year,last_value) VALUES (?,1)
                   ON CONFLICT(year) DO UPDATE SET last_value=last_value+1
                   RETURNING last_value""",
                (year,),
            ).fetchone()
            if sequence is None:  # pragma: no cover - SQLite guarantees a row
                raise ConflictError("Unable to allocate a civic ticket number")
            civitas_ticket_id = f"{prefix}{int(sequence['last_value']):06d}"
            conn.execute(
                """INSERT INTO complaint_tickets(
                   id,civitas_ticket_id,thread_id,case_id,owner_id,title,description,authority_id,
                   locality,status,visibility,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ticket_id,
                    civitas_ticket_id,
                    thread_id,
                    case_id,
                    owner_id,
                    title.strip(),
                    description.strip(),
                    authority_id,
                    locality.strip() if locality else None,
                    TicketStatus.DRAFT.value,
                    visibility.value,
                    now,
                    now,
                ),
            )
            self._add_ticket_history_conn(
                conn, ticket_id, owner_id, TicketStatus.DRAFT, "Ticket created"
            )
        return self.get_ticket(owner_id, ticket_id)

    def get_ticket(self, owner_id: str, ticket_id: str) -> TicketDetail:
        with self._lock:
            row = self._conn.execute(
                """SELECT id,civitas_ticket_id,thread_id,case_id,owner_id,title,description,
                   authority_id,locality,status,visibility,external_reference_id,acknowledgement,
                   tracking_url,submitted_content_hash,last_checkpoint_id,public_post_id,
                   created_at,updated_at FROM complaint_tickets WHERE id=? AND owner_id=?""",
                (ticket_id, owner_id),
            ).fetchone()
            if row is None:
                row = self._conn.execute(
                    """SELECT id,civitas_ticket_id,thread_id,case_id,owner_id,title,description,
                       authority_id,locality,status,visibility,external_reference_id,acknowledgement,
                       tracking_url,submitted_content_hash,last_checkpoint_id,public_post_id,
                       created_at,updated_at FROM complaint_tickets
                       WHERE civitas_ticket_id=? AND owner_id=?""",
                    (ticket_id, owner_id),
                ).fetchone()
            history_rows = (
                self._conn.execute(
                    """SELECT id,ticket_id,status,note,created_at FROM ticket_status_history
                       WHERE ticket_id=? AND owner_id=? ORDER BY created_at ASC""",
                    (str(row["id"]), owner_id),
                ).fetchall()
                if row
                else []
            )
        if row is None:
            raise NotFoundError("Ticket not found")
        return TicketDetail(
            ticket=self._ticket_from_row(row),
            history=[self._status_event_from_row(item) for item in history_rows],
        )

    def list_tickets(self, owner_id: str, limit: int = 50) -> list[ComplaintTicket]:
        limit = max(1, min(limit, 100))
        with self._lock:
            rows = self._conn.execute(
                """SELECT id,civitas_ticket_id,thread_id,case_id,owner_id,title,description,
                   authority_id,locality,status,visibility,external_reference_id,acknowledgement,
                   tracking_url,submitted_content_hash,last_checkpoint_id,public_post_id,
                   created_at,updated_at FROM complaint_tickets WHERE owner_id=?
                   ORDER BY updated_at DESC LIMIT ?""",
                (owner_id, limit),
            ).fetchall()
        return [self._ticket_from_row(row) for row in rows]

    def update_ticket_status(
        self, owner_id: str, ticket_id: str, status: TicketStatus, note: str | None = None
    ) -> TicketDetail:
        detail = self.get_ticket(owner_id, ticket_id)
        now = iso_now()
        with self._transaction() as conn:
            conn.execute(
                "UPDATE complaint_tickets SET status=?,updated_at=? WHERE id=? AND owner_id=?",
                (status.value, now, detail.ticket.id, owner_id),
            )
            self._add_ticket_history_conn(conn, detail.ticket.id, owner_id, status, note)
            conn.execute(
                "UPDATE public_posts SET status=?,updated_at=? WHERE ticket_id=?",
                (status.value, now, detail.ticket.id),
            )
            post_row = conn.execute(
                "SELECT id,civitas_ticket_id FROM public_posts WHERE ticket_id=?",
                (detail.ticket.id,),
            ).fetchone()
            if post_row:
                follower_rows = conn.execute(
                    "SELECT user_id FROM post_follows WHERE post_id=? AND user_id<>?",
                    (post_row["id"], owner_id),
                ).fetchall()
                for follower in follower_rows:
                    self._add_notification_conn(
                        conn,
                        str(follower["user_id"]),
                        "ticket_status_changed",
                        f"{post_row['civitas_ticket_id']} is now {status.value.replace('_', ' ')}.",
                        post_id=str(post_row["id"]),
                        ticket_id=str(post_row["civitas_ticket_id"]),
                    )
        return self.get_ticket(owner_id, detail.ticket.id)

    def prepare_ticket(
        self,
        owner_id: str,
        ticket_id: str,
        *,
        authority_name: str | None,
        contact_route: str | None,
        intake_url: str | None,
        required_fields: list[str],
        fields: dict[str, str],
        attachment_ids: list[str],
    ) -> TicketPreparation:
        """Build a deterministic, reviewable payload for an authority route.

        This is the local equivalent of the observe phase of a browser/API
        connector. It validates requirements and records a content hash, but
        it never opens a government session or submits a form.
        """

        detail = self.get_ticket(owner_id, ticket_id)
        ticket = detail.ticket
        merged = {
            "title": ticket.title,
            "description": ticket.description,
            "locality": ticket.locality or "",
            **{key: value for key, value in fields.items() if value},
        }
        if attachment_ids:
            if not ticket.thread_id:
                raise ConflictError("Prepared attachments must belong to the ticket conversation")
            self.validate_attachment_ids(owner_id, ticket.thread_id, attachment_ids)
        missing = [key for key in required_fields if not str(merged.get(key, "")).strip()]
        payload = {
            "ticket_id": ticket.civitas_ticket_id,
            "authority_id": ticket.authority_id,
            "fields": merged,
            "attachment_ids": sorted(set(attachment_ids)),
            "destination": intake_url or contact_route or "",
        }
        content_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        preparation_id = str(uuid.uuid4())
        now = iso_now()
        status_value = "needs_information" if missing else "ready_for_review"
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO ticket_preparations(
                   id,ticket_id,owner_id,authority_id,fields_json,required_fields_json,
                   missing_fields_json,attachment_ids_json,destination,content_hash,status,
                   created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    preparation_id,
                    ticket.id,
                    owner_id,
                    ticket.authority_id,
                    json.dumps(merged, ensure_ascii=False, sort_keys=True),
                    json.dumps(required_fields, ensure_ascii=False),
                    json.dumps(missing, ensure_ascii=False),
                    json.dumps(sorted(set(attachment_ids)), ensure_ascii=False),
                    intake_url or contact_route,
                    content_hash,
                    status_value,
                    now,
                    now,
                ),
            )
            if ticket.status != TicketStatus(status_value):
                conn.execute(
                    "UPDATE complaint_tickets SET status=?,updated_at=? WHERE id=? AND owner_id=?",
                    (status_value, now, ticket.id, owner_id),
                )
                self._add_ticket_history_conn(
                    conn,
                    ticket.id,
                    owner_id,
                    TicketStatus(status_value),
                    "Preparation checked" if missing else "Ready for review",
                )
        preparation = self.get_preparation(owner_id, preparation_id)
        self.create_checkpoint(
            owner_id,
            ticket.id,
            phase="prepared" if missing else "reviewed",
            summary=(
                f"Preparation needs {len(missing)} field(s)"
                if missing
                else "Authority payload is ready for review"
            ),
            preparation_id=preparation.id,
        )
        return self.get_preparation(owner_id, preparation.id)

    def get_preparation(self, owner_id: str, preparation_id: str) -> TicketPreparation:
        with self._lock:
            row = self._conn.execute(
                """SELECT p.id,p.ticket_id,p.authority_id,p.fields_json,p.required_fields_json,
                   p.missing_fields_json,p.attachment_ids_json,p.destination,p.content_hash,p.status,
                   p.created_at,p.updated_at,p.approved_at,p.approval_expires_at,
                   t.civitas_ticket_id,t.title,t.description FROM ticket_preparations p
                   JOIN complaint_tickets t ON t.id=p.ticket_id
                   WHERE p.id=? AND p.owner_id=?""",
                (preparation_id, owner_id),
            ).fetchone()
        if row is None:
            raise NotFoundError("Ticket preparation not found")
        return self._preparation_from_row(row)

    def latest_preparation(self, owner_id: str, ticket_id: str) -> TicketPreparation | None:
        detail = self.get_ticket(owner_id, ticket_id)
        with self._lock:
            row = self._conn.execute(
                """SELECT p.id,p.ticket_id,p.authority_id,p.fields_json,p.required_fields_json,
                   p.missing_fields_json,p.attachment_ids_json,p.destination,p.content_hash,p.status,
                   p.created_at,p.updated_at,p.approved_at,p.approval_expires_at,
                   t.civitas_ticket_id,t.title,t.description FROM ticket_preparations p
                   JOIN complaint_tickets t ON t.id=p.ticket_id
                   WHERE p.ticket_id=? AND p.owner_id=? ORDER BY p.created_at DESC LIMIT 1""",
                (detail.ticket.id, owner_id),
            ).fetchone()
        return self._preparation_from_row(row) if row else None

    def approve_preparation(
        self, owner_id: str, preparation_id: str, content_hash: str
    ) -> PreparationApproval:
        preparation = self.get_preparation(owner_id, preparation_id)
        if preparation.content_hash != content_hash:
            raise ConflictError("The review changed. Recheck the preparation before approving.")
        if preparation.missing_fields:
            raise ConflictError("Complete the missing fields before approving this preparation")
        expires = utc_now() + timedelta(minutes=30)
        now = iso_now()
        with self._transaction() as conn:
            conn.execute(
                """UPDATE ticket_preparations SET status='approved',approved_at=?,approval_expires_at=?,updated_at=?
                   WHERE id=? AND owner_id=? AND content_hash=?""",
                (now, expires.isoformat(), now, preparation.id, owner_id, content_hash),
            )
            self._add_ticket_history_conn(
                conn,
                preparation.ticket_id,
                owner_id,
                TicketStatus.READY_FOR_REVIEW,
                "Review approval saved; external submission remains disabled",
            )
        self.create_checkpoint(
            owner_id,
            preparation.ticket_id,
            phase="reviewed",
            summary="Resident approved the exact local preparation payload",
            preparation_id=preparation.id,
        )
        return PreparationApproval(
            preparation_id=preparation.id,
            ticket_id=preparation.ticket_id,
            destination=preparation.destination or "",
            content_hash=content_hash,
            approved_at=parse_datetime(now),
            expires_at=expires,
            valid=True,
            submission_enabled=False,
        )

    def create_checkpoint(
        self,
        owner_id: str,
        ticket_id: str,
        *,
        phase: str,
        summary: str,
        preparation_id: str | None = None,
    ) -> Checkpoint:
        detail = self.get_ticket(owner_id, ticket_id)
        checkpoint_id = str(uuid.uuid4())
        now = iso_now()
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO ticket_checkpoints(id,ticket_id,owner_id,preparation_id,phase,summary,created_at) VALUES (?,?,?,?,?,?,?)",
                (checkpoint_id, detail.ticket.id, owner_id, preparation_id, phase, summary, now),
            )
            conn.execute(
                "UPDATE complaint_tickets SET last_checkpoint_id=?,updated_at=? WHERE id=? AND owner_id=?",
                (checkpoint_id, now, detail.ticket.id, owner_id),
            )
        return Checkpoint(
            id=checkpoint_id,
            ticket_id=detail.ticket.id,
            preparation_id=preparation_id,
            phase=phase,
            summary=summary,
            created_at=parse_datetime(now),
        )

    def list_checkpoints(self, owner_id: str, ticket_id: str) -> list[Checkpoint]:
        detail = self.get_ticket(owner_id, ticket_id)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id,ticket_id,preparation_id,phase,summary,created_at FROM ticket_checkpoints WHERE ticket_id=? AND owner_id=? ORDER BY created_at ASC",
                (detail.ticket.id, owner_id),
            ).fetchall()
        return [
            Checkpoint(
                id=str(row["id"]),
                ticket_id=str(row["ticket_id"]),
                preparation_id=row["preparation_id"],
                phase=str(row["phase"]),
                summary=str(row["summary"]),
                created_at=parse_datetime(str(row["created_at"])),
            )
            for row in rows
        ]

    def record_outcome(
        self,
        owner_id: str,
        ticket_id: str,
        *,
        status: TicketStatus,
        external_reference_id: str | None = None,
        acknowledgement: str | None = None,
        tracking_url: str | None = None,
        submitted_content_hash: str | None = None,
        note: str | None = None,
    ) -> TicketDetail:
        detail = self.get_ticket(owner_id, ticket_id)
        now = iso_now()
        with self._transaction() as conn:
            conn.execute(
                """UPDATE complaint_tickets SET status=?,external_reference_id=COALESCE(?,external_reference_id),
                   acknowledgement=COALESCE(?,acknowledgement),tracking_url=COALESCE(?,tracking_url),
                   submitted_content_hash=COALESCE(?,submitted_content_hash),updated_at=?
                   WHERE id=? AND owner_id=?""",
                (
                    status.value,
                    external_reference_id,
                    acknowledgement,
                    tracking_url,
                    submitted_content_hash,
                    now,
                    detail.ticket.id,
                    owner_id,
                ),
            )
            self._add_ticket_history_conn(
                conn, detail.ticket.id, owner_id, status, note or "Outcome recorded by resident"
            )
        self.create_checkpoint(
            owner_id,
            detail.ticket.id,
            phase="outcome_unknown" if status == TicketStatus.OUTCOME_UNKNOWN else "complete",
            summary=(
                "Submission outcome is uncertain; investigate before retrying"
                if status == TicketStatus.OUTCOME_UNKNOWN
                else f"Ticket marked {status.value.replace('_', ' ')}"
            ),
        )
        return self.get_ticket(owner_id, detail.ticket.id)

    # ---- public feed -------------------------------------------------------
    def publish_ticket(
        self,
        owner_id: str,
        ticket_id: str,
        *,
        title: str,
        body: str,
        locality: str | None,
        visibility: str,
        author_name: str,
        attachment_ids: list[str] | None = None,
        evidence_count: int | None = None,
    ) -> CivicPost:
        detail = self.get_ticket(owner_id, ticket_id)
        if detail.ticket.public_post_id:
            return self.get_post(detail.ticket.public_post_id, viewer_id=owner_id)
        if visibility not in {"nearby", "locality", "citywide"}:
            raise ValueError("Invalid public visibility")
        selected_attachment_ids = sorted(set(attachment_ids or []))
        if selected_attachment_ids:
            if not detail.ticket.thread_id:
                raise ConflictError("Public attachments must belong to the ticket conversation")
            self.validate_attachment_ids(owner_id, detail.ticket.thread_id, selected_attachment_ids)
        snapshot_evidence = self._thread_citation_evidence(owner_id, detail.ticket.thread_id)
        # ``evidence_count`` remains accepted for callers compiled against the
        # old adapter, but the persisted count is always derived from records
        # that were actually validated at publication time.
        del evidence_count
        snapshot_count = len(snapshot_evidence) + len(selected_attachment_ids)
        post_id = str(uuid.uuid4())
        now = iso_now()
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO public_posts(
                   id,ticket_id,civitas_ticket_id,owner_id,author_name,title,body,locality,
                   visibility,status,authority_id,evidence_count,is_demo,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)""",
                (
                    post_id,
                    detail.ticket.id,
                    detail.ticket.civitas_ticket_id,
                    owner_id,
                    author_name.strip()[:120],
                    redact_public_text(title.strip()),
                    redact_public_text(body.strip()),
                    locality.strip() if locality else None,
                    visibility,
                    detail.ticket.status.value,
                    detail.ticket.authority_id,
                    snapshot_count,
                    now,
                    now,
                ),
            )
            for evidence in snapshot_evidence:
                conn.execute(
                    """INSERT INTO public_post_evidence(
                       post_id,evidence_id,evidence_json,created_at) VALUES (?,?,?,?)""",
                    (
                        post_id,
                        evidence.source_id,
                        json.dumps(evidence.model_dump(mode="json"), ensure_ascii=False),
                        now,
                    ),
                )
            for attachment_id in selected_attachment_ids:
                conn.execute(
                    """INSERT INTO public_post_attachments(post_id,attachment_id,created_at)
                       VALUES (?,?,?)""",
                    (post_id, attachment_id, now),
                )
            conn.execute(
                """UPDATE complaint_tickets SET public_post_id=?,visibility=?,updated_at=?
                   WHERE id=? AND owner_id=?""",
                (post_id, visibility, now, detail.ticket.id, owner_id),
            )
        return self.get_post(post_id, viewer_id=owner_id)

    def create_share_snapshot(
        self, owner_id: str, post_id: str, *, ttl_hours: int = 72
    ) -> ShareSnapshot:
        post = self.get_post(post_id, viewer_id=owner_id)
        if post.visibility != "citywide" and not post.is_demo:
            raise ConflictError(
                "Public share links are available only for citywide posts; keep this post in its selected audience"
            )
        token = uuid.uuid4().hex
        now = utc_now()
        expires = now + timedelta(hours=max(1, min(ttl_hours, 24 * 30)))
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO share_snapshots(
                   token,post_id,owner_id,title,body,locality,visibility,civitas_ticket_id,status,
                   authority_id,expires_at,revoked,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    token,
                    post.id,
                    owner_id,
                    redact_public_text(post.title),
                    redact_public_text(post.body),
                    post.locality,
                    post.visibility,
                    post.civitas_ticket_id,
                    post.status.value,
                    post.authority_id,
                    expires.isoformat(),
                    0,
                    now.isoformat(),
                ),
            )
        return ShareSnapshot(
            token=token,
            post_id=post.id,
            url=f"/share/{token}",
            expires_at=expires,
            revoked=False,
        )

    def revoke_share_snapshot(self, owner_id: str, token: str) -> ShareSnapshot:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT token,post_id,expires_at,revoked FROM share_snapshots WHERE token=? AND owner_id=?",
                (token, owner_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("Share snapshot not found")
            conn.execute(
                "UPDATE share_snapshots SET revoked=1 WHERE token=? AND owner_id=?",
                (token, owner_id),
            )
        return ShareSnapshot(
            token=str(row["token"]),
            post_id=str(row["post_id"]),
            url=f"/share/{row['token']}",
            expires_at=parse_datetime(str(row["expires_at"])),
            revoked=True,
        )

    def get_share_snapshot(self, token: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """SELECT token,post_id,title,body,locality,visibility,civitas_ticket_id,status,authority_id,
                   expires_at,revoked FROM share_snapshots WHERE token=?""",
                (token,),
            ).fetchone()
        if row is None:
            raise NotFoundError("Share snapshot not found")
        expires_at = parse_datetime(str(row["expires_at"])) or utc_now()
        if bool(row["revoked"]) or expires_at <= utc_now():
            raise NotFoundError("Share snapshot has expired or was revoked")
        return {
            "token": str(row["token"]),
            "post_id": str(row["post_id"]),
            "civitas_ticket_id": str(row["civitas_ticket_id"]),
            "title": str(row["title"]),
            "body": str(row["body"]),
            "locality": row["locality"],
            "visibility": str(row["visibility"]),
            "status": str(row["status"]),
            "authority_id": row["authority_id"],
            "expires_at": expires_at,
        }

    def get_post(self, post_id: str, viewer_id: str | None = None, locality: str | None = None, enforce_visibility: bool = False) -> CivicPost:
        with self._lock:
            row = self._conn.execute(
                """SELECT p.*, (SELECT COUNT(*) FROM post_votes v WHERE v.post_id=p.id AND v.value=1) up,
                   (SELECT COUNT(*) FROM post_votes v WHERE v.post_id=p.id AND v.value=-1) down,
                   (SELECT COUNT(*) FROM post_comments c WHERE c.post_id=p.id AND c.moderation_state='visible' AND c.deleted_at IS NULL) comments,
                   (SELECT value FROM post_votes v WHERE v.post_id=p.id AND v.user_id=?) user_vote,
                   EXISTS(SELECT 1 FROM post_follows f WHERE f.post_id=p.id AND f.user_id=?) is_following,
                   EXISTS(SELECT 1 FROM post_saves s WHERE s.post_id=p.id AND s.user_id=?) is_saved,
                   EXISTS(SELECT 1 FROM post_mutes m WHERE m.post_id=p.id AND m.user_id=?) is_muted
                   FROM public_posts p WHERE p.id=?""",
                (viewer_id, viewer_id, viewer_id, viewer_id, post_id),
            ).fetchone()
        if row is None:
            raise NotFoundError("Public post not found")
        if viewer_id is not None and str(row["moderation_state"] or "visible") == "hidden":
            raise NotFoundError("Public post not found")
        if enforce_visibility and not bool(row["is_demo"]) and viewer_id is not None and str(row["owner_id"]) != viewer_id and str(row["visibility"]) in {"nearby", "locality"}:
            if not locality or not row["locality"] or str(row["locality"]).casefold() != locality.casefold():
                raise NotFoundError("Public post not found")
        return self._post_from_row(row)

    def _thread_citation_evidence(
        self, owner_id: str, thread_id: str | None
    ) -> list[SourceEvidence]:
        """Create a source snapshot while the owner is still in private scope."""

        if not thread_id:
            return []
        thread = self.get_thread_detail(owner_id, thread_id)
        seen: set[str] = set()
        evidence: list[SourceEvidence] = []
        for message in thread.messages:
            for part in message.parts:
                if part.type != "citation":
                    continue
                data = part.data or {}
                source_id = str(data.get("source_id") or "")
                if not source_id or source_id in seen:
                    continue
                seen.add(source_id)
                evidence.append(
                    SourceEvidence(
                        source_id=source_id,
                        title=str(data.get("title") or part.text or "Civic source"),
                        authority=str(data.get("authority") or "Official authority"),
                        status=str(data.get("status") or "unknown"),
                        page=int(data["page"]) if data.get("page") else None,
                        published_at=parse_datetime(str(data["published_at"]))
                        if data.get("published_at")
                        else None,
                        retrieved_at=parse_datetime(str(data["retrieved_at"]))
                        if data.get("retrieved_at")
                        else None,
                        url=str(data["url"]) if data.get("url") else None,
                        passage=str(data.get("passage") or ""),
                        original_passage=str(data["original_passage"])
                        if data.get("original_passage")
                        else None,
                        translation_language=str(data["translation_language"])
                        if data.get("translation_language")
                        else None,
                        content_hash=str(data["content_hash"])
                        if data.get("content_hash")
                        else None,
                        source_kind=str(data.get("source_kind") or "official_reference"),
                        extraction_method=str(data["extraction_method"])
                        if data.get("extraction_method")
                        else None,
                        extraction_status=str(data.get("extraction_status") or "unknown"),
                    )
                )
        return evidence

    def list_post_evidence(self, post_id: str) -> list[SourceEvidence]:
        """Return the immutable evidence snapshot captured at publication."""

        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM public_posts WHERE id=?", (post_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("Public post not found")
            rows = self._conn.execute(
                """SELECT evidence_json FROM public_post_evidence
                   WHERE post_id=? ORDER BY created_at ASC""",
                (post_id,),
            ).fetchall()
        evidence: list[SourceEvidence] = []
        for item in rows:
            try:
                evidence.append(SourceEvidence.model_validate(json.loads(item["evidence_json"])))
            except (TypeError, ValueError, json.JSONDecodeError):
                # A malformed historical snapshot should not make the whole
                # public post unavailable.
                continue
        return evidence

    @staticmethod
    def _encode_feed_cursor(offset: int) -> str:
        return (
            base64.urlsafe_b64encode(str(max(0, offset)).encode("ascii"))
            .decode("ascii")
            .rstrip("=")
        )

    @staticmethod
    def _decode_feed_cursor(cursor: str | None) -> int:
        if not cursor:
            return 0
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
            offset = int(raw)
        except (ValueError, UnicodeError):
            raise ValueError("Invalid feed cursor") from None
        if offset < 0:
            raise ValueError("Invalid feed cursor")
        return offset

    def list_feed(
        self,
        viewer_id: str,
        *,
        sort: str = "recent",
        locality: str | None = None,
        visibility: str | None = None,
        status: TicketStatus | None = None,
        authority_id: str | None = None,
        topic: str | None = None,
        limit: int = 30,
        cursor: str | None = None,
        enforce_visibility: bool = False,
    ) -> list[CivicPost]:
        items, _ = self.list_feed_page(
            viewer_id,
            sort=sort,
            locality=locality,
            visibility=visibility,
            status=status,
            authority_id=authority_id,
            topic=topic,
            limit=limit,
            cursor=cursor,
            enforce_visibility=enforce_visibility,
        )
        return items

    def list_feed_page(
        self,
        viewer_id: str,
        *,
        sort: str = "recent",
        locality: str | None = None,
        visibility: str | None = None,
        status: TicketStatus | None = None,
        authority_id: str | None = None,
        topic: str | None = None,
        limit: int = 30,
        cursor: str | None = None,
        enforce_visibility: bool = False,
    ) -> tuple[list[CivicPost], str | None]:
        limit = max(1, min(limit, 100))
        clauses = ["p.moderation_state='visible'"]
        where_params: list[Any] = []
        if visibility:
            clauses.append("p.visibility=?")
            where_params.append(visibility)
        if status:
            clauses.append("p.status=?")
            where_params.append(status.value)
        if authority_id:
            clauses.append("p.authority_id=?")
            where_params.append(authority_id)
        if topic:
            clauses.append("(LOWER(p.title) LIKE ? OR LOWER(p.body) LIKE ?)")
            term = f"%{topic.casefold()}%"
            where_params.extend([term, term])
        if enforce_visibility:
            if locality:
                clauses.append(
                    "(p.is_demo=1 OR p.owner_id=? OR p.visibility='citywide' "
                    "OR LOWER(p.locality)=LOWER(?))"
                )
                where_params.extend([viewer_id, locality])
            else:
                clauses.append("(p.is_demo=1 OR p.owner_id=? OR p.visibility='citywide')")
                where_params.append(viewer_id)
        if sort == "following":
            clauses.append(
                "(EXISTS (SELECT 1 FROM post_follows f WHERE f.post_id=p.id AND f.user_id=?)"
                " OR EXISTS (SELECT 1 FROM subject_follows sf WHERE sf.user_id=? AND "
                "((sf.subject_type='locality' AND LOWER(sf.subject_value)=LOWER(p.locality)) OR "
                "(sf.subject_type='authority' AND sf.subject_value=p.authority_id) OR "
                "(sf.subject_type='topic' AND (LOWER(p.title) LIKE '%'||LOWER(sf.subject_value)||'%' "
                "OR LOWER(p.body) LIKE '%'||LOWER(sf.subject_value)||'%')))))"
            )
            where_params.extend([viewer_id, viewer_id])
        if locality and sort in {"nearby", "following"}:
            clauses.append(
                "(p.is_demo=1 OR LOWER(p.locality)=LOWER(?) OR p.visibility='citywide')"
            )
            where_params.append(locality)
        if locality and sort not in {"nearby", "following"}:
            clauses.append(
                "(p.is_demo=1 OR p.visibility='citywide' OR LOWER(p.locality)=LOWER(?))"
            )
            where_params.append(locality)
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM post_mutes pm WHERE pm.post_id=p.id AND pm.user_id=?)"
        )
        where_params.append(viewer_id)
        order = "p.created_at DESC"
        order_params: list[Any] = []
        if sort == "popular":
            order = "(p.vote_score * 1.0) DESC, p.created_at DESC"
        elif sort == "nearby":
            order = "CASE WHEN LOWER(p.locality)=LOWER(?) THEN 0 ELSE 1 END, p.created_at DESC"
            order_params.append(locality or "")
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT p.*,
                   (SELECT COUNT(*) FROM post_votes v WHERE v.post_id=p.id AND v.value=1) up,
                   (SELECT COUNT(*) FROM post_votes v WHERE v.post_id=p.id AND v.value=-1) down,
                   (SELECT COUNT(*) FROM post_comments c WHERE c.post_id=p.id AND c.moderation_state='visible' AND c.deleted_at IS NULL) comments,
                   (SELECT value FROM post_votes v WHERE v.post_id=p.id AND v.user_id=?) user_vote,
                   EXISTS(SELECT 1 FROM post_follows f WHERE f.post_id=p.id AND f.user_id=?) is_following,
                   EXISTS(SELECT 1 FROM post_saves s WHERE s.post_id=p.id AND s.user_id=?) is_saved,
                   EXISTS(SELECT 1 FROM post_mutes m WHERE m.post_id=p.id AND m.user_id=?) is_muted
                   FROM public_posts p WHERE {" AND ".join(clauses)} ORDER BY {order}""",
                (viewer_id, viewer_id, viewer_id, viewer_id, *where_params, *order_params),
            ).fetchall()
        posts = [self._post_from_row(row) for row in rows]
        ranked = self._rank_feed(posts, locality=locality, sort=sort)
        start = self._decode_feed_cursor(cursor)
        page = ranked[start : start + limit]
        end = start + len(page)
        next_cursor = self._encode_feed_cursor(end) if end < len(ranked) else None
        return page, next_cursor

    @staticmethod
    def _rank_feed(posts: list[CivicPost], *, locality: str | None, sort: str) -> list[CivicPost]:
        """Rank local candidates with an inspectable X-style pipeline.

        The scorer deliberately uses only product signals available in the
        local store. It has no opaque model call: freshness, constructive
        engagement, locality, unresolved duration, evidence, acknowledgement,
        and vote confidence are combined, then a small author-diversity pass
        keeps one prolific account from filling the whole view. Every post
        carries the reasons that produced its score so the ordering can be
        explained and tuned before an AWS ranker is introduced.
        """

        now = utc_now()
        resolved = {
            TicketStatus.RESOLVED,
            TicketStatus.RESOLVED_PENDING_CONFIRMATION,
        }
        scored: list[tuple[float, CivicPost, list[str]]] = []
        for post in posts:
            age_hours = max(0.05, (now - post.created_at).total_seconds() / 3600)
            recency = 1 / (1 + age_hours / 24)
            engagement = math.log1p(max(0, post.upvotes) + post.comment_count * 2)
            downvote_penalty = math.log1p(max(0, post.downvotes)) * 0.28
            vote_total = post.upvotes + post.downvotes
            confidence = (post.upvotes / vote_total) if vote_total else 0.5
            local_match = bool(
                locality and post.locality and post.locality.casefold() == locality.casefold()
            )
            unresolved = post.status not in resolved
            unresolved_boost = min(1.0, age_hours / (24 * 14)) if unresolved else 0
            evidence_boost = min(1.0, post.evidence_count / 3)
            acknowledgement_boost = (
                0.18
                if post.status
                in {
                    TicketStatus.ACKNOWLEDGED,
                    TicketStatus.IN_PROGRESS,
                }
                else 0
            )
            score = (
                recency * 2.2
                + engagement * 0.42
                + max(0, post.vote_score) * 0.04
                + confidence * 0.4
                + unresolved_boost * 0.65
                + evidence_boost * 0.32
                + acknowledgement_boost
                + (1.25 if local_match else 0)
                - downvote_penalty
            )
            reasons: list[str] = []
            if local_match:
                reasons.append(f"Matches {post.locality}")
            if recency >= 0.5:
                reasons.append("Recent activity")
            if unresolved:
                reasons.append("Still open for community follow-up")
            if engagement >= 1:
                reasons.append("Neighbours are discussing it")
            if post.evidence_count:
                reasons.append("Includes evidence")
            if post.status in {TicketStatus.ACKNOWLEDGED, TicketStatus.IN_PROGRESS}:
                reasons.append("Authority status is visible")
            if post.downvotes > post.upvotes and post.downvotes:
                reasons.append("Mixed community signal")
            scored.append((score, post, reasons or ["Civic issue in your selected view"]))

        if sort == "recent":
            scored.sort(key=lambda item: item[1].created_at, reverse=True)
        elif sort == "nearby":
            scored.sort(
                key=lambda item: (
                    0
                    if locality
                    and item[1].locality
                    and item[1].locality.casefold() == locality.casefold()
                    else 1,
                    -item[0],
                )
            )
        elif sort == "popular":
            scored.sort(
                key=lambda item: (item[1].vote_score, item[1].comment_count, item[1].created_at),
                reverse=True,
            )
        else:
            scored.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)

        # Lightweight author diversity, mirroring the mix stage of a large
        # recommendation system while retaining every civic candidate.
        first_pass: list[tuple[float, CivicPost, list[str]]] = []
        deferred: list[tuple[float, CivicPost, list[str]]] = []
        author_counts: dict[str, int] = {}
        for item in scored:
            author = item[1].author_name.casefold()
            if author_counts.get(author, 0) >= 2:
                deferred.append(item)
                continue
            author_counts[author] = author_counts.get(author, 0) + 1
            first_pass.append(item)
        scored = first_pass + deferred
        return [
            post.model_copy(update={"ranking_score": round(score, 4), "ranking_reasons": reasons})
            for score, post, reasons in scored
        ]

    def vote_post(self, viewer_id: str, post_id: str, value: int) -> VoteResponse:
        if value not in {-1, 0, 1}:
            raise ValueError("Vote must be -1, 0, or 1")
        post = self.get_post(post_id, viewer_id=viewer_id)
        now = iso_now()
        with self._transaction() as conn:
            self._enforce_action_rate(conn, viewer_id, "vote", limit=60)
            existing = conn.execute(
                "SELECT value FROM post_votes WHERE post_id=? AND user_id=?",
                (post_id, viewer_id),
            ).fetchone()
            if value == 0:
                conn.execute(
                    "DELETE FROM post_votes WHERE post_id=? AND user_id=?", (post_id, viewer_id)
                )
            elif existing:
                conn.execute(
                    "UPDATE post_votes SET value=?,created_at=? WHERE post_id=? AND user_id=?",
                    (value, now, post_id, viewer_id),
                )
            else:
                conn.execute(
                    "INSERT INTO post_votes(post_id,user_id,value,created_at) VALUES (?,?,?,?)",
                    (post_id, viewer_id, value, now),
                )
            counts = conn.execute(
                """SELECT COALESCE(SUM(CASE WHEN value=1 THEN 1 ELSE 0 END),0) up,
                   COALESCE(SUM(CASE WHEN value=-1 THEN 1 ELSE 0 END),0) down
                   FROM post_votes WHERE post_id=?""",
                (post_id,),
            ).fetchone()
            up = int(counts["up"])
            down = int(counts["down"])
            conn.execute(
                "UPDATE public_posts SET upvotes=?,downvotes=?,vote_score=?,updated_at=? WHERE id=?",
                (up, down, up - down, now, post_id),
            )
            if value and post.author_id and post.author_id not in {viewer_id, "system"}:
                self._add_notification_conn(
                    conn,
                    post.author_id,
                    "post_voted",
                    f"Someone voted on {post.civitas_ticket_id}.",
                    post_id=post_id,
                    ticket_id=post.civitas_ticket_id,
                )
        return VoteResponse(
            post_id=post_id, value=value, upvotes=up, downvotes=down, vote_score=up - down
        )

    def follow_post(self, viewer_id: str, post_id: str, following: bool) -> dict[str, Any]:
        post = self.get_post(post_id, viewer_id=viewer_id)
        now = iso_now()
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT 1 FROM post_follows WHERE post_id=? AND user_id=?",
                (post_id, viewer_id),
            ).fetchone()
            if following:
                conn.execute(
                    "INSERT OR IGNORE INTO post_follows(post_id,user_id,created_at) VALUES (?,?,?)",
                    (post_id, viewer_id, now),
                )
            else:
                conn.execute(
                    "DELETE FROM post_follows WHERE post_id=? AND user_id=?",
                    (post_id, viewer_id),
                )
            if (
                following
                and not existing
                and post.author_id
                and post.author_id not in {viewer_id, "system"}
            ):
                self._add_notification_conn(
                    conn,
                    post.author_id,
                    "post_followed",
                    f"{post.civitas_ticket_id} is being followed.",
                    post_id=post_id,
                    ticket_id=post.civitas_ticket_id,
                )
        return {"post_id": post_id, "following": following}

    def save_post(self, viewer_id: str, post_id: str, saved: bool) -> dict[str, Any]:
        self.get_post(post_id, viewer_id=viewer_id)
        now = iso_now()
        with self._transaction() as conn:
            if saved:
                conn.execute(
                    "INSERT OR IGNORE INTO post_saves(post_id,user_id,created_at) VALUES (?,?,?)",
                    (post_id, viewer_id, now),
                )
            else:
                conn.execute(
                    "DELETE FROM post_saves WHERE post_id=? AND user_id=?",
                    (post_id, viewer_id),
                )
        return {"post_id": post_id, "saved": saved}

    def mute_post(self, viewer_id: str, post_id: str, muted: bool) -> dict[str, Any]:
        self.get_post(post_id, viewer_id=viewer_id)
        now = iso_now()
        with self._transaction() as conn:
            if muted:
                conn.execute(
                    "INSERT OR IGNORE INTO post_mutes(post_id,user_id,created_at) VALUES (?,?,?)",
                    (post_id, viewer_id, now),
                )
            else:
                conn.execute(
                    "DELETE FROM post_mutes WHERE post_id=? AND user_id=?",
                    (post_id, viewer_id),
                )
        return {"post_id": post_id, "muted": muted}

    def follow_subject(
        self, viewer_id: str, subject_type: str, value: str, following: bool
    ) -> FollowSubject:
        clean_value = " ".join(value.strip().split())
        if not clean_value:
            raise ValueError("Follow value cannot be empty")
        now = iso_now()
        with self._transaction() as conn:
            if following:
                conn.execute(
                    "INSERT OR IGNORE INTO subject_follows(subject_type,subject_value,user_id,created_at) VALUES (?,?,?,?)",
                    (subject_type, clean_value, viewer_id, now),
                )
            else:
                conn.execute(
                    "DELETE FROM subject_follows WHERE subject_type=? AND subject_value=? AND user_id=?",
                    (subject_type, clean_value, viewer_id),
                )
        return FollowSubject(
            subject_type=subject_type,
            value=clean_value,
            following=following,
            created_at=parse_datetime(now),
        )

    def list_subject_follows(self, viewer_id: str) -> list[FollowSubject]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT subject_type,subject_value,created_at FROM subject_follows WHERE user_id=? ORDER BY created_at DESC",
                (viewer_id,),
            ).fetchall()
        return [
            FollowSubject(
                subject_type=str(row["subject_type"]),
                value=str(row["subject_value"]),
                following=True,
                created_at=parse_datetime(str(row["created_at"])),
            )
            for row in rows
        ]

    def create_report(
        self,
        reporter_id: str,
        target_type: str,
        target_id: str,
        reason: str,
        details: str | None = None,
    ) -> Report:
        if target_type == "post":
            self.get_post(target_id)
        elif target_type == "comment":
            with self._lock:
                exists = self._conn.execute(
                    "SELECT id FROM post_comments WHERE id=?", (target_id,)
                ).fetchone()
            if exists is None:
                raise NotFoundError("Comment not found")
        else:
            raise ValueError("Report target must be a post or comment")
        report_id = str(uuid.uuid4())
        now = iso_now()
        with self._transaction() as conn:
            self._enforce_action_rate(conn, reporter_id, "report", limit=10)
            conn.execute(
                """INSERT OR IGNORE INTO civic_reports(
                   id,target_type,target_id,reporter_id,reason,details,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?, 'open', ?, ?)""",
                (report_id, target_type, target_id, reporter_id, reason, details, now, now),
            )
            row = conn.execute(
                """SELECT id,target_type,target_id,reporter_id,reason,details,status,created_at,updated_at
                   FROM civic_reports WHERE target_type=? AND target_id=? AND reporter_id=?""",
                (target_type, target_id, reporter_id),
            ).fetchone()
        return self._report_from_row(row)

    def list_reports(self, status: str | None = "open", limit: int = 100) -> list[Report]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    """SELECT id,target_type,target_id,reporter_id,reason,details,status,created_at,updated_at
                       FROM civic_reports WHERE status=? ORDER BY created_at DESC LIMIT ?""",
                    (status, max(1, min(limit, 250))),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT id,target_type,target_id,reporter_id,reason,details,status,created_at,updated_at
                       FROM civic_reports ORDER BY created_at DESC LIMIT ?""",
                    (max(1, min(limit, 250)),),
                ).fetchall()
        return [self._report_from_row(row) for row in rows]

    def list_moderation_actions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT id,moderator_id,target_type,target_id,action,note,created_at
                   FROM moderation_actions ORDER BY created_at DESC LIMIT ?""",
                (max(1, min(limit, 250)),),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "moderator_id": str(row["moderator_id"]),
                "target_type": str(row["target_type"]),
                "target_id": str(row["target_id"]),
                "action": str(row["action"]),
                "note": row["note"],
                "created_at": parse_datetime(str(row["created_at"])),
            }
            for row in rows
        ]

    def moderate(
        self,
        moderator_id: str,
        target_type: str,
        target_id: str,
        action: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        now = iso_now()
        with self._transaction() as conn:
            if target_type == "post":
                exists = conn.execute(
                    "SELECT id FROM public_posts WHERE id=?", (target_id,)
                ).fetchone()
                if exists is None:
                    raise NotFoundError("Public post not found")
                if action == "hide":
                    conn.execute(
                        "UPDATE public_posts SET moderation_state='hidden' WHERE id=?", (target_id,)
                    )
                elif action == "restore":
                    conn.execute(
                        "UPDATE public_posts SET moderation_state='visible' WHERE id=?",
                        (target_id,),
                    )
                elif action == "lock":
                    conn.execute(
                        "UPDATE public_posts SET locked=1,moderation_state='locked' WHERE id=?",
                        (target_id,),
                    )
                elif action == "unlock":
                    conn.execute(
                        "UPDATE public_posts SET locked=0,moderation_state='visible' WHERE id=?",
                        (target_id,),
                    )
                else:
                    raise ValueError("Unsupported post moderation action")
            elif target_type == "comment":
                exists = conn.execute(
                    """SELECT id,post_id,moderation_state,deleted_at
                       FROM post_comments WHERE id=?""",
                    (target_id,),
                ).fetchone()
                if exists is None:
                    raise NotFoundError("Comment not found")
                if action == "hide":
                    was_counted = (
                        str(exists["moderation_state"] or "visible") == "visible"
                        and not exists["deleted_at"]
                    )
                    conn.execute(
                        "UPDATE post_comments SET moderation_state='hidden' WHERE id=?",
                        (target_id,),
                    )
                    if was_counted:
                        conn.execute(
                            """UPDATE public_posts
                               SET comment_count=MAX(0,comment_count-1),updated_at=?
                               WHERE id=?""",
                            (now, str(exists["post_id"])),
                        )
                elif action == "restore":
                    was_counted = (
                        str(exists["moderation_state"] or "visible") != "visible"
                        and not exists["deleted_at"]
                    )
                    conn.execute(
                        "UPDATE post_comments SET moderation_state='visible' WHERE id=?",
                        (target_id,),
                    )
                    if was_counted:
                        conn.execute(
                            """UPDATE public_posts SET comment_count=comment_count+1,updated_at=?
                               WHERE id=?""",
                            (now, str(exists["post_id"])),
                        )
                else:
                    raise ValueError("Unsupported comment moderation action")
            elif target_type == "report":
                status_value = (
                    "resolved"
                    if action == "resolve_report"
                    else "dismissed"
                    if action == "dismiss_report"
                    else None
                )
                if status_value is None:
                    raise ValueError("Unsupported report moderation action")
                conn.execute(
                    "UPDATE civic_reports SET status=?,updated_at=? WHERE id=?",
                    (status_value, now, target_id),
                )
                if conn.total_changes == 0:
                    raise NotFoundError("Report not found")
            else:
                raise ValueError("Moderation target must be a post, comment, or report")
            action_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO moderation_actions(id,moderator_id,target_type,target_id,action,note,created_at) VALUES (?,?,?,?,?,?,?)",
                (action_id, moderator_id, target_type, target_id, action, note, now),
            )
        return {
            "id": action_id,
            "moderator_id": moderator_id,
            "target_type": target_type,
            "target_id": target_id,
            "action": action,
            "note": note,
            "created_at": parse_datetime(now),
        }

    def list_comments(self, post_id: str, viewer_id: str | None = None) -> list[Comment]:
        self.get_post(post_id, viewer_id=viewer_id)
        with self._lock:
            rows = self._conn.execute(
                """SELECT id,post_id,owner_id,author_name,body,parent_id,is_demo,created_at,updated_at
                   ,moderation_state,deleted_at FROM post_comments
                   WHERE post_id=? AND moderation_state='visible' AND deleted_at IS NULL
                   ORDER BY created_at ASC""",
                (post_id,),
            ).fetchall()
        return [self._comment_from_row(row, viewer_id) for row in rows]

    def create_comment(
        self, owner_id: str, post_id: str, author_name: str, body: str, parent_id: str | None = None
    ) -> Comment:
        post = self.get_post(post_id, viewer_id=owner_id)
        if post.is_locked:
            raise ConflictError("This civic post is locked for new comments")
        if parent_id:
            with self._lock:
                parent = self._conn.execute(
                    """SELECT id FROM post_comments
                       WHERE id=? AND post_id=? AND moderation_state='visible' AND deleted_at IS NULL""",
                    (parent_id, post_id),
                ).fetchone()
            if parent is None:
                raise NotFoundError("Parent comment not found")
        comment_id = str(uuid.uuid4())
        now = iso_now()
        with self._transaction() as conn:
            self._enforce_action_rate(conn, owner_id, "comment", limit=20)
            conn.execute(
                """INSERT INTO post_comments(id,post_id,owner_id,author_name,body,parent_id,is_demo,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,0,?,?)""",
                (
                    comment_id,
                    post_id,
                    owner_id,
                    author_name.strip()[:120],
                    body.strip(),
                    parent_id,
                    now,
                    now,
                ),
            )
            conn.execute(
                "UPDATE public_posts SET comment_count=comment_count+1,updated_at=? WHERE id=?",
                (now, post_id),
            )
            if post.author_id and post.author_id not in {owner_id, "system"}:
                self._add_notification_conn(
                    conn,
                    post.author_id,
                    "post_commented",
                    f"Someone commented on {post.civitas_ticket_id}.",
                    post_id=post_id,
                    ticket_id=post.civitas_ticket_id,
                )
        return Comment(
            id=comment_id,
            post_id=post_id,
            author_id=None,
            author_name=author_name.strip()[:120],
            body=body.strip(),
            parent_id=parent_id,
            created_at=parse_datetime(now),
            updated_at=parse_datetime(now),
        )

    def edit_comment(self, owner_id: str, comment_id: str, body: str) -> Comment:
        with self._transaction() as conn:
            row = conn.execute(
                """SELECT id,post_id,owner_id,author_name,body,parent_id,is_demo,moderation_state,
                   deleted_at,created_at,updated_at FROM post_comments WHERE id=? AND owner_id=?""",
                (comment_id, owner_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("Comment not found")
            if bool(row["is_demo"]):
                raise ConflictError("Demo comments cannot be edited")
            if row["deleted_at"]:
                raise ConflictError("Deleted comments cannot be edited")
            updated = iso_now()
            conn.execute(
                "UPDATE post_comments SET body=?,updated_at=? WHERE id=? AND owner_id=?",
                (body.strip(), updated, comment_id, owner_id),
            )
        return self._comment_from_row(
            {**dict(row), "body": body.strip(), "updated_at": updated}, owner_id
        )

    def delete_comment(self, owner_id: str, comment_id: str) -> None:
        with self._transaction() as conn:
            row = conn.execute(
                """SELECT owner_id,post_id,is_demo,moderation_state,deleted_at
                   FROM post_comments WHERE id=? AND owner_id=?""",
                (comment_id, owner_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("Comment not found")
            if bool(row["is_demo"]):
                raise ConflictError("Demo comments cannot be deleted")
            if row["deleted_at"]:
                return
            now = iso_now()
            conn.execute(
                """UPDATE post_comments
                   SET deleted_at=?,body='[deleted]',author_name='[deleted]',owner_id=NULL,updated_at=?
                   WHERE id=? AND owner_id=?""",
                (now, now, comment_id, owner_id),
            )
            if str(row["moderation_state"] or "visible") == "visible":
                conn.execute(
                    """UPDATE public_posts
                       SET comment_count=MAX(0,comment_count-1),updated_at=?
                       WHERE id=?""",
                    (now, str(row["post_id"])),
                )

    # ---- notifications -----------------------------------------------------
    def add_notification(
        self,
        owner_id: str,
        kind: str,
        message: str,
        post_id: str | None = None,
        ticket_id: str | None = None,
    ) -> dict[str, Any]:
        notification_id = str(uuid.uuid4())
        now = iso_now()
        with self._transaction() as conn:
            self._add_notification_conn(
                conn,
                owner_id,
                kind,
                message,
                post_id=post_id,
                ticket_id=ticket_id,
                notification_id=notification_id,
                created_at=now,
            )
        return {
            "id": notification_id,
            "owner_id": owner_id,
            "kind": kind,
            "message": message,
            "post_id": post_id,
            "ticket_id": ticket_id,
            "read": False,
            "created_at": parse_datetime(now),
        }

    def list_notifications(self, owner_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT id,owner_id,kind,message,post_id,ticket_id,read,created_at
                   FROM community_notifications WHERE owner_id=? ORDER BY created_at DESC LIMIT ?""",
                (owner_id, max(1, min(limit, 100))),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "owner_id": str(row["owner_id"]),
                "kind": str(row["kind"]),
                "message": str(row["message"]),
                "post_id": row["post_id"],
                "ticket_id": row["ticket_id"],
                "read": bool(row["read"]),
                "created_at": parse_datetime(str(row["created_at"])),
            }
            for row in rows
        ]

    def mark_notification(self, owner_id: str, notification_id: str, read: bool = True) -> None:
        with self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE community_notifications SET read=? WHERE id=? AND owner_id=?",
                (1 if read else 0, notification_id, owner_id),
            )
            if cursor.rowcount == 0:
                raise NotFoundError("Notification not found")

    def mark_all_notifications(self, owner_id: str, read: bool = True) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE community_notifications SET read=? WHERE owner_id=?",
                (1 if read else 0, owner_id),
            )

    @staticmethod
    def _add_notification_conn(
        conn: sqlite3.Connection,
        owner_id: str,
        kind: str,
        message: str,
        *,
        post_id: str | None = None,
        ticket_id: str | None = None,
        notification_id: str | None = None,
        created_at: str | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO community_notifications(
               id,owner_id,kind,message,post_id,ticket_id,read,created_at)
               VALUES (?,?,?,?,?,?,0,?)""",
            (
                notification_id or str(uuid.uuid4()),
                owner_id,
                kind,
                message,
                post_id,
                ticket_id,
                created_at or iso_now(),
            ),
        )

    # ---- seed and conversion helpers --------------------------------------
    def _seed_demo_feed(self) -> None:
        existing = self._conn.execute(
            "SELECT COUNT(*) AS count FROM public_posts WHERE is_demo=1"
        ).fetchone()
        if existing and int(existing["count"]) > 0:
            return
        now = iso_now()
        demos = [
            (
                "demo-post-1",
                "demo-ticket-1",
                "CX-BLR-DEMO-0001",
                "Civic Desk",
                "Streetlights out near Indiranagar 12th Main",
                "Several streetlights have been out along the walking route near the 12th Main junction. Residents have reported dark stretches after 7 pm.",
                "Indiranagar",
                "locality",
                TicketStatus.IN_PROGRESS.value,
                "gba",
                23,
                7,
                1,
                12,
            ),
            (
                "demo-post-2",
                "demo-ticket-2",
                "CX-BLR-DEMO-0002",
                "Maya K.",
                "Safer crossings needed by the metro station",
                "The proposed station-access record mentions safer crossings and accessible paths. What is the timeline for the crossing outside the station?",
                "Jayanagar",
                "nearby",
                TicketStatus.ACKNOWLEDGED.value,
                "bmrcl",
                41,
                12,
                1,
                8,
            ),
            (
                "demo-post-3",
                "demo-ticket-3",
                "CX-BLR-DEMO-0003",
                "Rahul S.",
                "Ward drain still blocked after the last rain",
                "The drain was marked for restoration, but water is still pooling at the corner. Sharing the ticket so neighbours can add dates and photos.",
                "Malleshwaram",
                "citywide",
                TicketStatus.NOT_SOLVED.value,
                "gba",
                8,
                15,
                0,
                4,
            ),
        ]
        self._conn.executemany(
            """INSERT INTO public_posts(
               id,ticket_id,civitas_ticket_id,owner_id,author_name,title,body,locality,visibility,
               status,authority_id,vote_score,upvotes,downvotes,comment_count,evidence_count,is_demo,
               created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, ?,1,?,?)""",
            [
                (
                    post_id,
                    ticket_id,
                    civitas_id,
                    "system",
                    author_name,
                    title,
                    body,
                    locality,
                    visibility,
                    status,
                    authority_id,
                    upvotes - downvotes,
                    upvotes,
                    downvotes,
                    comment_count,
                    evidence_count,
                    now,
                    now,
                )
                for (
                    post_id,
                    ticket_id,
                    civitas_id,
                    author_name,
                    title,
                    body,
                    locality,
                    visibility,
                    status,
                    authority_id,
                    upvotes,
                    downvotes,
                    comment_count,
                    evidence_count,
                ) in demos
            ],
        )
        # Demo scores are represented by anonymous seed votes so the same
        # aggregate queries power demo and user-created posts.  They are
        # system-owned rows and cannot collide with a resident account.
        seed_votes: list[tuple[str, str, int, str]] = []
        for (
            post_id,
            _ticket_id,
            _civitas_id,
            _author_name,
            _title,
            _body,
            _locality,
            _visibility,
            _status,
            _authority_id,
            upvotes,
            downvotes,
            _comment_count,
            _evidence_count,
        ) in demos:
            seed_votes.extend(
                (post_id, f"demo-up-{post_id}-{index}", 1, now) for index in range(upvotes)
            )
            seed_votes.extend(
                (post_id, f"demo-down-{post_id}-{index}", -1, now) for index in range(downvotes)
            )
        self._conn.executemany(
            "INSERT INTO post_votes(post_id,user_id,value,created_at) VALUES (?,?,?,?)",
            seed_votes,
        )
        comments = [
            (
                "demo-comment-1",
                "demo-post-1",
                "system",
                "Neighbour note",
                "I saw the same issue near the bus stop yesterday.",
                None,
            ),
            (
                "demo-comment-2",
                "demo-post-2",
                "system",
                "Civic Desk",
                "The linked record is still marked acknowledged; add a photo if the crossing remains unsafe.",
                None,
            ),
        ]
        self._conn.executemany(
            """INSERT INTO post_comments(id,post_id,owner_id,author_name,body,parent_id,is_demo,created_at,updated_at)
               VALUES (?,?,?,?,?,?,1,?,?)""",
            [(a, b, c, d, e, f, now, now) for a, b, c, d, e, f in comments],
        )

    @staticmethod
    def _thread_from_row(row: sqlite3.Row) -> AgentThread:
        return AgentThread(
            id=str(row["id"]),
            case_id=row["case_id"],
            title=str(row["title"]),
            status=AgentThreadStatus(str(row["status"])),
            created_at=parse_datetime(str(row["created_at"])),
            updated_at=parse_datetime(str(row["updated_at"])),
            message_count=int(row["message_count"] or 0),
            ticket_id=row["ticket_id"],
        )

    @staticmethod
    def _attachment_from_row(row: sqlite3.Row) -> Attachment:
        return Attachment(
            id=str(row["id"]),
            thread_id=row["thread_id"],
            case_id=row["case_id"],
            owner_id=str(row["owner_id"]),
            filename=str(row["filename"]),
            content_type=str(row["content_type"]),
            size_bytes=int(row["size_bytes"]),
            sha256=str(row["sha256"]),
            storage_key=str(row["storage_key"]),
            visibility=str(row["visibility"]),
            created_at=parse_datetime(str(row["created_at"])),
        )

    @staticmethod
    def _ticket_from_row(row: sqlite3.Row) -> ComplaintTicket:
        return ComplaintTicket(
            id=str(row["id"]),
            civitas_ticket_id=str(row["civitas_ticket_id"]),
            thread_id=row["thread_id"],
            case_id=row["case_id"],
            owner_id=str(row["owner_id"]),
            title=str(row["title"]),
            description=str(row["description"]),
            authority_id=row["authority_id"],
            locality=row["locality"],
            status=TicketStatus(str(row["status"])),
            visibility=TicketVisibility(str(row["visibility"])),
            external_reference_id=row["external_reference_id"],
            acknowledgement=row["acknowledgement"] if "acknowledgement" in row.keys() else None,
            tracking_url=row["tracking_url"] if "tracking_url" in row.keys() else None,
            submitted_content_hash=(
                row["submitted_content_hash"] if "submitted_content_hash" in row.keys() else None
            ),
            last_checkpoint_id=(
                row["last_checkpoint_id"] if "last_checkpoint_id" in row.keys() else None
            ),
            public_post_id=row["public_post_id"],
            created_at=parse_datetime(str(row["created_at"])),
            updated_at=parse_datetime(str(row["updated_at"])),
        )

    @staticmethod
    def _status_event_from_row(row: sqlite3.Row) -> TicketStatusEvent:
        return TicketStatusEvent(
            id=str(row["id"]),
            ticket_id=str(row["ticket_id"]),
            status=TicketStatus(str(row["status"])),
            note=row["note"],
            created_at=parse_datetime(str(row["created_at"])),
        )

    @staticmethod
    def _post_from_row(row: sqlite3.Row) -> CivicPost:
        up = int(row["up"] if "up" in row.keys() else row["upvotes"] or 0)
        down = int(row["down"] if "down" in row.keys() else row["downvotes"] or 0)
        comments = int(row["comments"] if "comments" in row.keys() else row["comment_count"] or 0)
        return CivicPost(
            id=str(row["id"]),
            ticket_id=str(row["ticket_id"]),
            civitas_ticket_id=str(row["civitas_ticket_id"]),
            author_id=row["owner_id"],
            author_name=str(row["author_name"]),
            title=str(row["title"]),
            body=str(row["body"]),
            locality=row["locality"],
            visibility=str(row["visibility"]),
            status=TicketStatus(str(row["status"])),
            authority_id=row["authority_id"],
            authority_name=None,
            vote_score=up - down,
            upvotes=up,
            downvotes=down,
            comment_count=comments,
            evidence_count=int(row["evidence_count"] or 0),
            is_demo=bool(row["is_demo"]),
            created_at=parse_datetime(str(row["created_at"])),
            updated_at=parse_datetime(str(row["updated_at"])),
            user_vote=int(row["user_vote"] or 0),
            is_following=bool(row["is_following"]),
            is_saved=bool(row["is_saved"]) if "is_saved" in row.keys() else False,
            is_muted=bool(row["is_muted"]) if "is_muted" in row.keys() else False,
            is_locked=bool(row["locked"]) if "locked" in row.keys() else False,
            moderation_state=(
                str(row["moderation_state"]) if "moderation_state" in row.keys() else "visible"
            ),
        )

    @staticmethod
    def _comment_from_row(row: sqlite3.Row, viewer_id: str | None = None) -> Comment:
        return Comment(
            id=str(row["id"]),
            post_id=str(row["post_id"]),
            # Public comment payloads expose the display name only. The
            # internal owner UUID stays available to moderation code in SQL.
            author_id=None,
            author_name=str(row["author_name"]),
            body=str(row["body"]),
            parent_id=row["parent_id"],
            created_at=parse_datetime(str(row["created_at"])),
            updated_at=parse_datetime(str(row["updated_at"])),
            is_demo=bool(row["is_demo"]),
            is_owner=bool(viewer_id and row["owner_id"] == viewer_id),
            moderation_state=(
                str(row["moderation_state"]) if "moderation_state" in row.keys() else "visible"
            ),
            is_deleted=bool(row["deleted_at"]) if "deleted_at" in row.keys() else False,
        )

    @staticmethod
    def _report_from_row(row: sqlite3.Row) -> Report:
        return Report(
            id=str(row["id"]),
            target_type=str(row["target_type"]),
            target_id=str(row["target_id"]),
            reporter_id=str(row["reporter_id"]),
            reason=str(row["reason"]),
            details=row["details"],
            status=str(row["status"]),
            created_at=parse_datetime(str(row["created_at"])),
            updated_at=parse_datetime(str(row["updated_at"])),
        )

    @staticmethod
    def _preparation_from_row(row: sqlite3.Row) -> TicketPreparation:
        def decode(name: str) -> Any:
            try:
                return json.loads(row[name] or "{}")
            except (TypeError, json.JSONDecodeError):
                return {} if name == "fields_json" else []

        authority_id = row["authority_id"]
        fields = decode("fields_json")
        if not isinstance(fields, dict):
            fields = {}
        required = decode("required_fields_json")
        missing = decode("missing_fields_json")
        attachment_ids = decode("attachment_ids_json")
        return TicketPreparation(
            id=str(row["id"]),
            ticket_id=str(row["ticket_id"]),
            civitas_ticket_id=str(row["civitas_ticket_id"]),
            authority_id=authority_id,
            authority_name=None,
            contact_route=None,
            intake_url=row["destination"] if row["destination"] else None,
            destination=row["destination"],
            fields={str(k): str(v) for k, v in fields.items()},
            required_fields=[str(v) for v in required] if isinstance(required, list) else [],
            missing_fields=[str(v) for v in missing] if isinstance(missing, list) else [],
            attachment_ids=[str(v) for v in attachment_ids]
            if isinstance(attachment_ids, list)
            else [],
            content_hash=str(row["content_hash"]),
            status=str(row["status"]),
            submission_enabled=False,
            created_at=parse_datetime(str(row["created_at"])),
            updated_at=parse_datetime(str(row["updated_at"])),
            approved_at=(
                datetime.fromisoformat(str(row["approved_at"])) if row["approved_at"] else None
            ),
            approval_expires_at=(
                datetime.fromisoformat(str(row["approval_expires_at"]))
                if row["approval_expires_at"]
                else None
            ),
        )

    @staticmethod
    def _add_ticket_history_conn(
        conn: sqlite3.Connection,
        ticket_id: str,
        owner_id: str,
        status: TicketStatus,
        note: str | None,
    ) -> str:
        event_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO ticket_status_history(id,ticket_id,owner_id,status,note,created_at) VALUES (?,?,?,?,?,?)",
            (event_id, ticket_id, owner_id, status.value, note, iso_now()),
        )
        return event_id
