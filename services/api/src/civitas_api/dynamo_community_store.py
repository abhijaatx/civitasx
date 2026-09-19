"""DynamoDB persistence for the Agent, ticket, and civic-feed surfaces.

The local adapter intentionally keeps the relational SQLite implementation, but
cloud mode must not silently leave the community half of the product on local
disk.  This adapter uses the same ``pk``/``sk`` table as :class:`DynamoStore`.
Public feed records live in the ``FEED`` partition, while private records are
owner- or ticket-scoped.  The methods mirror ``LocalCommunityStore`` so the API
and MCP layers have one storage contract in either deployment mode.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .community_store import (
    LocalCommunityStore,
    iso_now,
    normalize_message_parts,
    parse_datetime,
    redact_public_text,
    utc_now,
)
from .errors import ConflictError, NotFoundError
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


class DynamoCommunityStore:
    """Single-table DynamoDB implementation of the community store contract."""

    def __init__(
        self,
        table_name: str,
        region: str,
        *,
        attachments_bucket: str | None = None,
    ):
        try:
            import boto3
            from boto3.dynamodb.conditions import Key
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("boto3 is required for DynamoDB storage") from exc
        self._key = Key
        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        self._client = self._table.meta.client
        self._attachments_bucket = attachments_bucket
        self._s3 = boto3.client("s3", region_name=region) if attachments_bucket else None
        self._temporary_paths: dict[str, Path] = {}
        self._seed_demo_feed()

    def close(self) -> None:
        for path in self._temporary_paths.values():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self._temporary_paths.clear()

    # ---- Dynamo helpers ---------------------------------------------------
    def _query(
        self, partition: str, prefix: str | None = None, *, ascending: bool = True
    ) -> list[dict[str, Any]]:
        condition = self._key("pk").eq(partition)
        if prefix:
            condition &= self._key("sk").begins_with(prefix)
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": condition,
            "ScanIndexForward": ascending,
        }
        while True:
            response = self._table.query(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return items
            kwargs["ExclusiveStartKey"] = last_key

    def _get(self, partition: str, sort: str) -> dict[str, Any] | None:
        return self._table.get_item(Key={"pk": partition, "sk": sort}).get("Item")

    def _put(self, item: dict[str, Any], *, condition: str | None = None) -> None:
        kwargs: dict[str, Any] = {"Item": item}
        if condition:
            kwargs["ConditionExpression"] = condition
        self._table.put_item(**kwargs)

    def _update_fields(self, partition: str, sort: str, fields: dict[str, Any]) -> dict[str, Any]:
        names: dict[str, str] = {}
        values: dict[str, Any] = {}
        sets: list[str] = []
        for index, (field, value) in enumerate(fields.items()):
            name = f"#n{index}"
            token = f":v{index}"
            names[name] = field
            values[token] = value
            sets.append(f"{name} = {token}")
        return self._table.update_item(
            Key={"pk": partition, "sk": sort},
            UpdateExpression="SET " + ", ".join(sets),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )["Attributes"]

    @staticmethod
    def _dt(value: str | datetime | None) -> datetime:
        if isinstance(value, datetime):
            return value
        return parse_datetime(str(value)) if value else utc_now()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _decode(value: Any, default: Any) -> Any:
        try:
            return json.loads(value or "")
        except (TypeError, json.JSONDecodeError):
            return default

    # ---- agent threads and attachments -----------------------------------
    def create_thread(self, owner_id: str, title: str, case_id: str | None = None) -> AgentThread:
        thread_id = str(uuid.uuid4())
        now = iso_now()
        item = {
            "pk": f"USER#{owner_id}",
            "sk": f"THREAD#{thread_id}",
            "entity": "thread",
            "id": thread_id,
            "owner_id": owner_id,
            "case_id": case_id,
            "title": title.strip()[:160],
            "status": AgentThreadStatus.ACTIVE.value,
            "created_at": now,
            "updated_at": now,
        }
        self._put(item, condition="attribute_not_exists(pk)")
        return self.get_thread(owner_id, thread_id)

    def _thread_item(self, owner_id: str, thread_id: str) -> dict[str, Any]:
        item = self._get(f"USER#{owner_id}", f"THREAD#{thread_id}")
        if not item:
            raise NotFoundError("Agent thread not found")
        return item

    def get_thread(self, owner_id: str, thread_id: str) -> AgentThread:
        item = self._thread_item(owner_id, thread_id)
        messages = self._query(f"THREAD#{thread_id}", "MESSAGE#")
        ticket_id = next(
            (
                str(ticket.get("civitas_ticket_id"))
                for ticket in self._query(f"USER#{owner_id}", "TICKET#")
                if ticket.get("thread_id") == thread_id
            ),
            None,
        )
        return AgentThread(
            id=str(item["id"]),
            case_id=item.get("case_id"),
            title=str(item["title"]),
            status=AgentThreadStatus(str(item["status"])),
            created_at=self._dt(item.get("created_at")),
            updated_at=self._dt(item.get("updated_at")),
            message_count=len(messages),
            ticket_id=ticket_id,
        )

    def list_threads(self, owner_id: str, limit: int = 30) -> list[AgentThread]:
        items = [
            item
            for item in self._query(f"USER#{owner_id}", "THREAD#", ascending=False)
            if item.get("status") != AgentThreadStatus.ARCHIVED.value
        ]
        threads = [self.get_thread(owner_id, str(item["id"])) for item in items]
        return sorted(threads, key=lambda item: item.updated_at, reverse=True)[
            : max(1, min(limit, 100))
        ]

    def update_thread(
        self,
        owner_id: str,
        thread_id: str,
        *,
        title: str | None = None,
        status: AgentThreadStatus | None = None,
    ) -> AgentThread:
        item = self._thread_item(owner_id, thread_id)
        fields: dict[str, Any] = {"updated_at": iso_now()}
        if title is not None:
            clean_title = " ".join(title.strip().split())
            if not clean_title:
                raise ValueError("Conversation title cannot be empty")
            fields["title"] = clean_title[:160]
        if status is not None:
            fields["status"] = status.value
        self._update_fields(str(item["pk"]), str(item["sk"]), fields)
        return self.get_thread(owner_id, thread_id)

    def add_message(
        self,
        owner_id: str,
        thread_id: str,
        role: AgentMessageRole,
        content: str,
        parts: list[dict[str, Any]] | None = None,
    ) -> AgentMessage:
        self._thread_item(owner_id, thread_id)
        message_id = str(uuid.uuid4())
        now = iso_now()
        item = {
            "pk": f"THREAD#{thread_id}",
            "sk": f"MESSAGE#{now}#{message_id}",
            "entity": "message",
            "id": message_id,
            "thread_id": thread_id,
            "owner_id": owner_id,
            "role": role.value,
            "content": content,
            "parts_json": self._json(parts or []),
            "created_at": now,
        }
        self._put(item)
        self._update_fields(f"USER#{owner_id}", f"THREAD#{thread_id}", {"updated_at": now})
        return AgentMessage(
            id=message_id,
            thread_id=thread_id,
            role=role,
            content=content,
            parts=parts or [],
            created_at=self._dt(now),
        )

    def get_thread_detail(self, owner_id: str, thread_id: str) -> AgentThreadDetail:
        thread = self.get_thread(owner_id, thread_id)
        messages: list[AgentMessage] = []
        for item in self._query(f"THREAD#{thread_id}", "MESSAGE#"):
            messages.append(
                AgentMessage(
                    id=str(item["id"]),
                    thread_id=str(item["thread_id"]),
                    role=AgentMessageRole(str(item["role"])),
                    content=str(item.get("content", "")),
                    parts=normalize_message_parts(self._decode(item.get("parts_json"), [])),
                    created_at=self._dt(item.get("created_at")),
                )
            )
        return AgentThreadDetail(thread=thread, messages=messages)

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
            self._thread_item(owner_id, thread_id)
        if len(content) > 15 * 1024 * 1024:
            raise ValueError("Attachments must be 15 MB or smaller")
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).name).strip("-") or "upload"
        attachment_id = str(uuid.uuid4())
        digest = hashlib.sha256(content).hexdigest()
        now = iso_now()
        storage_key = f"attachments/{owner_id}/{attachment_id}-{safe_name}"
        item: dict[str, Any] = {
            "pk": f"USER#{owner_id}",
            "sk": f"ATTACHMENT#{attachment_id}",
            "entity": "attachment",
            "id": attachment_id,
            "thread_id": thread_id,
            "case_id": case_id,
            "owner_id": owner_id,
            "filename": safe_name,
            "content_type": content_type or "application/octet-stream",
            "size_bytes": len(content),
            "sha256": digest,
            "storage_key": storage_key,
            "visibility": "private",
            "created_at": now,
        }
        if self._s3 and self._attachments_bucket:
            self._s3.put_object(
                Bucket=self._attachments_bucket,
                Key=storage_key,
                Body=content,
                ContentType=item["content_type"],
                ServerSideEncryption="AES256",
            )
        else:
            if len(content) > 300 * 1024:
                raise ValueError("Cloud attachments require the configured private S3 bucket")
            item["inline_content"] = base64.b64encode(content).decode("ascii")
        self._put(item, condition="attribute_not_exists(pk)")
        return self._attachment_from_item(item)

    def list_attachments(self, owner_id: str, thread_id: str) -> list[Attachment]:
        self._thread_item(owner_id, thread_id)
        return [
            self._attachment_from_item(item)
            for item in self._query(f"USER#{owner_id}", "ATTACHMENT#")
            if item.get("thread_id") == thread_id
        ]

    def get_attachment(self, owner_id: str, attachment_id: str) -> Attachment:
        item = self._get(f"USER#{owner_id}", f"ATTACHMENT#{attachment_id}")
        if not item:
            raise NotFoundError("Attachment not found")
        return self._attachment_from_item(item)

    def delete_attachment(self, owner_id: str, attachment_id: str) -> None:
        """Delete an uploaded file only while it is still an unreferenced draft."""

        attachment = self.get_attachment(owner_id, attachment_id)
        if attachment.thread_id:
            for message in self._query(f"THREAD#{attachment.thread_id}", "MESSAGE#"):
                parts = self._decode(message.get("parts_json"), [])
                if any(
                    isinstance(part, dict)
                    and str(part.get("attachment_id") or "") == attachment_id
                    for part in parts
                ):
                    raise ConflictError("This attachment is already part of a saved civic record")
        for item in self._query(f"USER#{owner_id}", "TICKET#"):
            if attachment_id in self._decode(item.get("attachment_ids_json"), []):
                raise ConflictError("This attachment is already part of a saved civic record")
        for post in self._query("FEED", "POST#"):
            post_id = str(post.get("id", ""))
            if any(
                str(item.get("attachment_id", "")) == attachment_id
                for item in self._query(f"POST#{post_id}", "ATTACHMENT#")
            ):
                raise ConflictError("This attachment is already part of a saved civic record")
        self._table.delete_item(
            Key={"pk": f"USER#{owner_id}", "sk": f"ATTACHMENT#{attachment_id}"}
        )
        if self._s3 and attachment.storage_key:
            try:
                self._s3.delete_object(Bucket=self._attachments_bucket, Key=attachment.storage_key)
            except Exception:  # pragma: no cover - provider-specific S3 errors
                pass

    def validate_attachment_ids(
        self, owner_id: str, thread_id: str, attachment_ids: list[str]
    ) -> list[Attachment]:
        attachments: list[Attachment] = []
        for attachment_id in attachment_ids:
            attachment = self.get_attachment(owner_id, attachment_id)
            if attachment.thread_id != thread_id:
                raise NotFoundError("Attachment is not part of this conversation")
            attachments.append(attachment)
        return attachments

    def attachment_path(self, attachment: Attachment) -> Path:
        path = self._temporary_paths.get(attachment.id)
        if path and path.exists():
            return path
        item = self._get(f"USER#{attachment.owner_id}", f"ATTACHMENT#{attachment.id}")
        if not item:
            raise NotFoundError("Attachment not found")
        if self._s3 and self._attachments_bucket:
            response = self._s3.get_object(
                Bucket=self._attachments_bucket, Key=attachment.storage_key
            )
            content = response["Body"].read()
        else:
            try:
                content = base64.b64decode(str(item.get("inline_content", "")))
            except (ValueError, TypeError) as exc:
                raise NotFoundError("Attachment content is unavailable") from exc
        handle = tempfile.NamedTemporaryFile(
            prefix="civitas-attachment-", suffix=Path(attachment.filename).suffix, delete=False
        )
        try:
            handle.write(content)
        finally:
            handle.close()
        path = Path(handle.name)
        self._temporary_paths[attachment.id] = path
        return path

    # ---- tickets ----------------------------------------------------------
    def _ticket_item(self, owner_id: str, ticket_id: str) -> dict[str, Any]:
        item = self._get(f"USER#{owner_id}", f"TICKET#{ticket_id}")
        if item:
            return item
        for candidate in self._query(f"USER#{owner_id}", "TICKET#"):
            if candidate.get("civitas_ticket_id") == ticket_id:
                return candidate
        raise NotFoundError("Ticket not found")

    def _ticket_detail_from_item(self, item: dict[str, Any]) -> TicketDetail:
        ticket_id = str(item["id"])
        history = [
            TicketStatusEvent(
                id=str(event["id"]),
                ticket_id=ticket_id,
                status=TicketStatus(str(event["status"])),
                note=event.get("note"),
                created_at=self._dt(event.get("created_at")),
            )
            for event in self._query(f"TICKET#{ticket_id}", "HISTORY#")
        ]
        return TicketDetail(ticket=self._ticket_from_item(item), history=history)

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
            self._thread_item(owner_id, thread_id)
        ticket_id = str(uuid.uuid4())
        year = utc_now().year
        sequence = self._table.update_item(
            Key={"pk": "GLOBAL", "sk": f"TICKET_SEQUENCE#{year}"},
            UpdateExpression="SET #last = if_not_exists(#last, :zero) + :one, #entity = :entity",
            ExpressionAttributeNames={"#last": "last_value", "#entity": "entity"},
            ExpressionAttributeValues={":zero": 0, ":one": 1, ":entity": "ticket_sequence"},
            ReturnValues="UPDATED_NEW",
        )["Attributes"]["last_value"]
        now = iso_now()
        item = {
            "pk": f"USER#{owner_id}",
            "sk": f"TICKET#{ticket_id}",
            "entity": "ticket",
            "id": ticket_id,
            "civitas_ticket_id": f"CX-BLR-{year}-{int(sequence):06d}",
            "thread_id": thread_id,
            "case_id": case_id,
            "owner_id": owner_id,
            "title": title.strip(),
            "description": description.strip(),
            "authority_id": authority_id,
            "locality": locality.strip() if locality else None,
            "status": TicketStatus.DRAFT.value,
            "visibility": visibility.value,
            "external_reference_id": None,
            "acknowledgement": None,
            "tracking_url": None,
            "submitted_content_hash": None,
            "last_checkpoint_id": None,
            "public_post_id": None,
            "created_at": now,
            "updated_at": now,
        }
        self._put(item, condition="attribute_not_exists(pk)")
        self._add_history(item, TicketStatus.DRAFT, "Ticket created", now=now)
        return self._ticket_detail_from_item(item)

    def get_ticket(self, owner_id: str, ticket_id: str) -> TicketDetail:
        return self._ticket_detail_from_item(self._ticket_item(owner_id, ticket_id))

    def list_tickets(self, owner_id: str, limit: int = 50) -> list[ComplaintTicket]:
        items = self._query(f"USER#{owner_id}", "TICKET#", ascending=False)
        tickets = [self._ticket_from_item(item) for item in items]
        return sorted(tickets, key=lambda item: item.updated_at, reverse=True)[
            : max(1, min(limit, 100))
        ]

    def _add_history(
        self,
        ticket: dict[str, Any],
        status: TicketStatus,
        note: str | None,
        *,
        now: str | None = None,
    ) -> TicketStatusEvent:
        event_id = str(uuid.uuid4())
        created_at = now or iso_now()
        item = {
            "pk": f"TICKET#{ticket['id']}",
            "sk": f"HISTORY#{created_at}#{event_id}",
            "entity": "ticket_history",
            "id": event_id,
            "ticket_id": ticket["id"],
            "owner_id": ticket["owner_id"],
            "status": status.value,
            "note": note,
            "created_at": created_at,
        }
        self._put(item)
        return TicketStatusEvent(
            id=event_id,
            ticket_id=str(ticket["id"]),
            status=status,
            note=note,
            created_at=self._dt(created_at),
        )

    def update_ticket_status(
        self, owner_id: str, ticket_id: str, status: TicketStatus, note: str | None = None
    ) -> TicketDetail:
        item = self._ticket_item(owner_id, ticket_id)
        now = iso_now()
        updated = self._update_fields(
            str(item["pk"]), str(item["sk"]), {"status": status.value, "updated_at": now}
        )
        self._add_history(updated, status, note, now=now)
        if updated.get("public_post_id"):
            self._update_post_fields(str(updated["public_post_id"]), {"status": status.value})
        return self._ticket_detail_from_item(updated)

    # ---- preparation/checkpoints/outcomes ---------------------------------
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
        del authority_name, contact_route
        detail = self.get_ticket(owner_id, ticket_id)
        ticket = detail.ticket
        if ticket.thread_id and attachment_ids:
            self.validate_attachment_ids(owner_id, ticket.thread_id, attachment_ids)
        merged = {
            "title": ticket.title,
            "description": ticket.description,
            "locality": ticket.locality or "",
            **{key: value for key, value in fields.items() if value},
        }
        missing = [key for key in required_fields if not str(merged.get(key, "")).strip()]
        payload = {
            "ticket_id": ticket.civitas_ticket_id,
            "authority_id": ticket.authority_id,
            "fields": merged,
            "attachment_ids": sorted(set(attachment_ids)),
            "destination": intake_url or "",
        }
        content_hash = hashlib.sha256(self._json(payload).encode("utf-8")).hexdigest()
        preparation_id = str(uuid.uuid4())
        now = iso_now()
        item = {
            "pk": f"PREPARATION#{preparation_id}",
            "sk": "META",
            "entity": "preparation",
            "id": preparation_id,
            "ticket_id": ticket.id,
            "owner_id": owner_id,
            "authority_id": ticket.authority_id,
            "fields_json": self._json(merged),
            "required_fields_json": self._json(required_fields),
            "missing_fields_json": self._json(missing),
            "attachment_ids_json": self._json(sorted(set(attachment_ids))),
            "destination": intake_url,
            "content_hash": content_hash,
            "status": "needs_information" if missing else "ready_for_review",
            "created_at": now,
            "updated_at": now,
            "approved_at": None,
            "approval_expires_at": None,
        }
        self._put(item, condition="attribute_not_exists(pk)")
        self._put({**item, "pk": f"TICKET#{ticket.id}", "sk": f"PREP#{now}#{preparation_id}"})
        if ticket.status.value != item["status"]:
            updated = self._update_fields(
                f"USER#{owner_id}",
                f"TICKET#{ticket.id}",
                {"status": item["status"], "updated_at": now},
            )
            self._add_history(
                updated,
                TicketStatus(item["status"]),
                "Preparation checked" if missing else "Ready for review",
                now=now,
            )
        preparation = self._preparation_from_item(item, ticket)
        self.create_checkpoint(
            owner_id,
            ticket.id,
            phase="prepared" if missing else "reviewed",
            summary=f"Preparation needs {len(missing)} field(s)"
            if missing
            else "Authority payload is ready for review",
            preparation_id=preparation.id,
        )
        return self.get_preparation(owner_id, preparation_id)

    def _preparation_item(self, owner_id: str, preparation_id: str) -> dict[str, Any]:
        item = self._get(f"PREPARATION#{preparation_id}", "META")
        if not item or item.get("owner_id") != owner_id:
            raise NotFoundError("Ticket preparation not found")
        return item

    def get_preparation(self, owner_id: str, preparation_id: str) -> TicketPreparation:
        item = self._preparation_item(owner_id, preparation_id)
        ticket = self.get_ticket(owner_id, str(item["ticket_id"])).ticket
        return self._preparation_from_item(item, ticket)

    def latest_preparation(self, owner_id: str, ticket_id: str) -> TicketPreparation | None:
        ticket = self.get_ticket(owner_id, ticket_id).ticket
        items = self._query(f"TICKET#{ticket.id}", "PREP#", ascending=False)
        if not items:
            return None
        return self._preparation_from_item(items[0], ticket)

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
        item = self._update_fields(
            f"PREPARATION#{preparation_id}",
            "META",
            {
                "status": "approved",
                "approved_at": now,
                "approval_expires_at": expires.isoformat(),
                "updated_at": now,
            },
        )
        ticket = self.get_ticket(owner_id, preparation.ticket_id).ticket
        for indexed in self._query(f"TICKET#{ticket.id}", "PREP#"):
            if indexed.get("id") == preparation_id:
                self._update_fields(
                    str(indexed["pk"]),
                    str(indexed["sk"]),
                    {
                        "status": "approved",
                        "approved_at": now,
                        "approval_expires_at": expires.isoformat(),
                        "updated_at": now,
                    },
                )
                break
        self._add_history(
            ticket.model_dump(mode="json") | {"id": ticket.id, "owner_id": owner_id},
            TicketStatus.READY_FOR_REVIEW,
            "Review approval saved; external submission remains disabled",
            now=now,
        )
        self.create_checkpoint(
            owner_id,
            preparation.ticket_id,
            phase="reviewed",
            summary="Resident approved the exact local preparation payload",
            preparation_id=preparation_id,
        )
        return PreparationApproval(
            preparation_id=preparation_id,
            ticket_id=preparation.ticket_id,
            destination=preparation.destination or "",
            content_hash=content_hash,
            approved_at=self._dt(item.get("approved_at")),
            expires_at=self._dt(item.get("approval_expires_at")),
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
        ticket = self.get_ticket(owner_id, ticket_id).ticket
        checkpoint_id = str(uuid.uuid4())
        now = iso_now()
        item = {
            "pk": f"TICKET#{ticket.id}",
            "sk": f"CHECKPOINT#{now}#{checkpoint_id}",
            "entity": "checkpoint",
            "id": checkpoint_id,
            "ticket_id": ticket.id,
            "owner_id": owner_id,
            "preparation_id": preparation_id,
            "phase": phase,
            "summary": summary,
            "created_at": now,
        }
        self._put(item)
        self._update_fields(
            f"USER#{owner_id}",
            f"TICKET#{ticket.id}",
            {"last_checkpoint_id": checkpoint_id, "updated_at": now},
        )
        return self._checkpoint_from_item(item)

    def list_checkpoints(self, owner_id: str, ticket_id: str) -> list[Checkpoint]:
        ticket = self.get_ticket(owner_id, ticket_id).ticket
        return [
            self._checkpoint_from_item(item)
            for item in self._query(f"TICKET#{ticket.id}", "CHECKPOINT#")
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
        ticket = self._ticket_item(owner_id, ticket_id)
        now = iso_now()
        fields: dict[str, Any] = {"status": status.value, "updated_at": now}
        for key, value in {
            "external_reference_id": external_reference_id,
            "acknowledgement": acknowledgement,
            "tracking_url": tracking_url,
            "submitted_content_hash": submitted_content_hash,
        }.items():
            if value is not None:
                fields[key] = value
        updated = self._update_fields(str(ticket["pk"]), str(ticket["sk"]), fields)
        self._add_history(updated, status, note or "Outcome recorded by resident", now=now)
        self.create_checkpoint(
            owner_id,
            str(updated["id"]),
            phase="outcome_unknown" if status == TicketStatus.OUTCOME_UNKNOWN else "complete",
            summary=(
                "Submission outcome is uncertain; investigate before retrying"
                if status == TicketStatus.OUTCOME_UNKNOWN
                else f"Ticket marked {status.value.replace('_', ' ')}"
            ),
        )
        return self._ticket_detail_from_item(updated)

    # ---- public feed ------------------------------------------------------
    def _post_item(self, post_id: str) -> dict[str, Any]:
        item = self._get(f"POST#{post_id}", "META")
        if not item:
            raise NotFoundError("Public post not found")
        return item

    def _post_relationship(self, post_id: str, prefix: str, user_id: str) -> bool:
        return self._get(f"POST#{post_id}", f"{prefix}{user_id}") is not None

    def _post_aggregate(self, post_id: str) -> tuple[int, int, int]:
        items = self._query(f"POST#{post_id}")
        up = sum(
            1
            for item in items
            if item.get("sk", "").startswith("VOTE#") and int(item.get("value", 0)) == 1
        )
        down = sum(
            1
            for item in items
            if item.get("sk", "").startswith("VOTE#") and int(item.get("value", 0)) == -1
        )
        comments = sum(
            1
            for item in items
            if item.get("sk", "").startswith("COMMENT#")
            and item.get("moderation_state", "visible") == "visible"
            and not item.get("deleted_at")
        )
        return up, down, comments

    def _post_from_item(self, item: dict[str, Any], viewer_id: str | None = None) -> CivicPost:
        post_id = str(item["id"])
        up, down, comments = self._post_aggregate(post_id)
        user_vote_item = self._get(f"POST#{post_id}", f"VOTE#{viewer_id}") if viewer_id else None
        return CivicPost(
            id=post_id,
            ticket_id=str(item["ticket_id"]),
            civitas_ticket_id=str(item["civitas_ticket_id"]),
            author_id=item.get("owner_id"),
            author_name=str(item["author_name"]),
            title=str(item["title"]),
            body=str(item["body"]),
            locality=item.get("locality"),
            visibility=str(item["visibility"]),
            status=TicketStatus(str(item["status"])),
            authority_id=item.get("authority_id"),
            authority_name=None,
            vote_score=up - down,
            upvotes=up,
            downvotes=down,
            comment_count=comments,
            evidence_count=int(item.get("evidence_count", 0)),
            is_demo=bool(item.get("is_demo", False)),
            created_at=self._dt(item.get("created_at")),
            updated_at=self._dt(item.get("updated_at")),
            user_vote=int(user_vote_item.get("value", 0)) if user_vote_item else 0,
            is_following=self._post_relationship(post_id, "FOLLOW#", viewer_id)
            if viewer_id
            else False,
            is_saved=self._post_relationship(post_id, "SAVE#", viewer_id) if viewer_id else False,
            is_muted=self._post_relationship(post_id, "MUTE#", viewer_id) if viewer_id else False,
            is_locked=bool(item.get("locked", False)),
            moderation_state=str(item.get("moderation_state", "visible")),
        )

    def get_post(
        self,
        post_id: str,
        viewer_id: str | None = None,
        locality: str | None = None,
        enforce_visibility: bool = False,
    ) -> CivicPost:
        item = self._post_item(post_id)
        if viewer_id is not None and item.get("moderation_state", "visible") == "hidden":
            raise NotFoundError("Public post not found")
        if (
            enforce_visibility
            and not bool(item.get("is_demo", False))
            and viewer_id is not None
            and str(item.get("owner_id")) != viewer_id
            and str(item.get("visibility")) in {"nearby", "locality"}
        ):
            post_locality = str(item.get("locality") or "")
            if not locality or not post_locality or post_locality.casefold() != locality.casefold():
                raise NotFoundError("Public post not found")
        return self._post_from_item(item, viewer_id)

    def _thread_citation_evidence(
        self, owner_id: str, thread_id: str | None
    ) -> list[SourceEvidence]:
        if not thread_id:
            return []
        detail = self.get_thread_detail(owner_id, thread_id)
        seen: set[str] = set()
        evidence: list[SourceEvidence] = []
        for message in detail.messages:
            for part in message.parts:
                if part.type != "citation":
                    continue
                data = dict(part.data or {})
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
                        published_at=self._dt(data["published_at"])
                        if data.get("published_at")
                        else None,
                        retrieved_at=self._dt(data["retrieved_at"])
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
        del evidence_count
        ticket = self.get_ticket(owner_id, ticket_id).ticket
        if ticket.public_post_id:
            return self.get_post(ticket.public_post_id, viewer_id=owner_id)
        if visibility not in {"nearby", "locality", "citywide"}:
            raise ValueError("Invalid public visibility")
        selected = sorted(set(attachment_ids or []))
        if selected:
            if not ticket.thread_id:
                raise ConflictError("Public attachments must belong to the ticket conversation")
            self.validate_attachment_ids(owner_id, ticket.thread_id, selected)
        evidence = self._thread_citation_evidence(owner_id, ticket.thread_id)
        post_id = str(uuid.uuid4())
        now = iso_now()
        item = {
            "pk": f"POST#{post_id}",
            "sk": "META",
            "entity": "public_post",
            "id": post_id,
            "ticket_id": ticket.id,
            "civitas_ticket_id": ticket.civitas_ticket_id,
            "owner_id": owner_id,
            "author_name": author_name.strip()[:120],
            "title": redact_public_text(title.strip()),
            "body": redact_public_text(body.strip()),
            "locality": locality.strip() if locality else None,
            "visibility": visibility,
            "status": ticket.status.value,
            "authority_id": ticket.authority_id,
            "evidence_count": len(evidence) + len(selected),
            "is_demo": False,
            "moderation_state": "visible",
            "locked": False,
            "created_at": now,
            "updated_at": now,
        }
        feed_item = {**item, "pk": "FEED", "sk": f"POST#{now}#{post_id}"}
        writes: list[dict[str, Any]] = [
            {
                "Put": {
                    "TableName": self._table.name,
                    "Item": item,
                    "ConditionExpression": "attribute_not_exists(pk)",
                }
            },
            {"Put": {"TableName": self._table.name, "Item": feed_item}},
            {
                "Update": {
                    "TableName": self._table.name,
                    "Key": {"pk": f"USER#{owner_id}", "sk": f"TICKET#{ticket.id}"},
                    "UpdateExpression": "SET public_post_id=:post, #visibility=:visibility, updated_at=:now",
                    "ExpressionAttributeNames": {"#visibility": "visibility"},
                    "ExpressionAttributeValues": {
                        ":post": post_id,
                        ":visibility": visibility,
                        ":now": now,
                    },
                }
            },
        ]
        for source in evidence:
            writes.append(
                {
                    "Put": {
                        "TableName": self._table.name,
                        "Item": {
                            "pk": f"POST#{post_id}",
                            "sk": f"EVIDENCE#{source.source_id}",
                            "entity": "public_post_evidence",
                            "evidence_json": self._json(source.model_dump(mode="json")),
                            "created_at": now,
                        },
                    }
                }
            )
        for attachment_id in selected:
            writes.append(
                {
                    "Put": {
                        "TableName": self._table.name,
                        "Item": {
                            "pk": f"POST#{post_id}",
                            "sk": f"ATTACHMENT#{attachment_id}",
                            "entity": "public_post_attachment",
                            "attachment_id": attachment_id,
                            "created_at": now,
                        },
                    }
                }
            )
        self._client.transact_write_items(TransactItems=writes)
        return self.get_post(post_id, viewer_id=owner_id)

    def list_post_evidence(self, post_id: str) -> list[SourceEvidence]:
        self._post_item(post_id)
        result: list[SourceEvidence] = []
        for item in self._query(f"POST#{post_id}", "EVIDENCE#"):
            try:
                result.append(
                    SourceEvidence.model_validate(self._decode(item.get("evidence_json"), {}))
                )
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        return LocalCommunityStore._decode_feed_cursor(cursor)

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        return LocalCommunityStore._encode_feed_cursor(offset)

    def _follows_subject(self, viewer_id: str, post: CivicPost) -> bool:
        return any(
            item.get("subject_type") == "locality"
            and str(item.get("subject_value", "")).casefold() == str(post.locality or "").casefold()
            for item in self._query(f"USER#{viewer_id}", "SUBJECT#")
        ) or any(
            item.get("subject_type") == "authority"
            and item.get("subject_value") == post.authority_id
            for item in self._query(f"USER#{viewer_id}", "SUBJECT#")
        )

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
        page, _ = self.list_feed_page(
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
        return page

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
        posts: list[CivicPost] = []
        for item in self._query("FEED", "POST#", ascending=False):
            if item.get("moderation_state", "visible") != "visible":
                continue
            post = self._post_from_item(item, viewer_id)
            if post.is_muted:
                continue
            if visibility and post.visibility != visibility:
                continue
            if status and post.status != status:
                continue
            if authority_id and post.authority_id != authority_id:
                continue
            if topic and topic.casefold() not in f"{post.title} {post.body}".casefold():
                continue
            if enforce_visibility and not post.is_demo and not post.is_owner:
                if locality:
                    if post.visibility != "citywide" and (
                        not post.locality or post.locality.casefold() != locality.casefold()
                    ):
                        continue
                elif post.visibility != "citywide":
                    continue
            if (
                locality
                and sort in {"nearby", "following"}
                and not (
                    (post.locality and post.locality.casefold() == locality.casefold())
                    or post.visibility == "citywide"
                    or post.is_demo
                )
            ):
                continue
            if (
                locality
                and sort not in {"nearby", "following"}
                and not (
                    post.visibility == "citywide"
                    or (post.locality and post.locality.casefold() == locality.casefold())
                    or post.is_demo
                )
            ):
                continue
            if sort == "following" and not (
                post.is_following or self._follows_subject(viewer_id, post)
            ):
                continue
            posts.append(post)
        ranked = LocalCommunityStore._rank_feed(posts, locality=locality, sort=sort)
        start = self._decode_cursor(cursor)
        page = ranked[start : start + max(1, min(limit, 100))]
        end = start + len(page)
        return page, self._encode_cursor(end) if end < len(ranked) else None

    def _update_post_fields(self, post_id: str, fields: dict[str, Any]) -> None:
        self._update_fields(f"POST#{post_id}", "META", fields)
        for item in self._query("FEED", "POST#"):
            if item.get("id") == post_id:
                self._update_fields(str(item["pk"]), str(item["sk"]), fields)
                break

    def _find_comment(self, comment_id: str) -> tuple[dict[str, Any], str] | None:
        for post in self._query("FEED", "POST#"):
            post_id = str(post["id"])
            for item in self._query(f"POST#{post_id}", "COMMENT#"):
                if item.get("id") == comment_id:
                    return item, post_id
        return None

    def list_comments(self, post_id: str, viewer_id: str | None = None) -> list[Comment]:
        self.get_post(post_id, viewer_id=viewer_id)
        return [
            self._comment_from_item(item, viewer_id)
            for item in self._query(f"POST#{post_id}", "COMMENT#")
            if item.get("moderation_state", "visible") == "visible" and not item.get("deleted_at")
        ]

    def create_comment(
        self, owner_id: str, post_id: str, author_name: str, body: str, parent_id: str | None = None
    ) -> Comment:
        post = self.get_post(post_id, viewer_id=owner_id)
        if post.is_locked:
            raise ConflictError("This civic post is locked for new comments")
        if parent_id and not any(
            item.get("id") == parent_id for item in self._query(f"POST#{post_id}", "COMMENT#")
        ):
            raise NotFoundError("Parent comment not found")
        comment_id = str(uuid.uuid4())
        now = iso_now()
        item = {
            "pk": f"POST#{post_id}",
            "sk": f"COMMENT#{now}#{comment_id}",
            "entity": "comment",
            "id": comment_id,
            "post_id": post_id,
            "owner_id": owner_id,
            "author_name": author_name.strip()[:120],
            "body": body.strip(),
            "parent_id": parent_id,
            "is_demo": False,
            "moderation_state": "visible",
            "deleted_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self._put(item)
        self._update_post_fields(post_id, {"updated_at": now})
        return self._comment_from_item(item, owner_id)

    def edit_comment(self, owner_id: str, comment_id: str, body: str) -> Comment:
        found = self._find_comment(comment_id)
        if not found or found[0].get("owner_id") != owner_id:
            raise NotFoundError("Comment not found")
        item, post_id = found
        if item.get("is_demo"):
            raise ConflictError("Demo comments cannot be edited")
        if item.get("deleted_at"):
            raise ConflictError("Deleted comments cannot be edited")
        updated = self._update_fields(
            f"POST#{post_id}", str(item["sk"]), {"body": body.strip(), "updated_at": iso_now()}
        )
        return self._comment_from_item(updated, owner_id)

    def delete_comment(self, owner_id: str, comment_id: str) -> None:
        found = self._find_comment(comment_id)
        if not found or found[0].get("owner_id") != owner_id:
            raise NotFoundError("Comment not found")
        item, post_id = found
        if item.get("is_demo"):
            raise ConflictError("Demo comments cannot be deleted")
        if item.get("deleted_at"):
            return
        now = iso_now()
        self._update_fields(
            f"POST#{post_id}",
            str(item["sk"]),
            {
                "deleted_at": now,
                "body": "[deleted]",
                "author_name": "[deleted]",
                "owner_id": None,
                "updated_at": now,
            },
        )
        self._update_post_fields(post_id, {"updated_at": now})

    # ---- votes, follows, reports, moderation, notifications --------------
    def vote_post(self, viewer_id: str, post_id: str, value: int) -> VoteResponse:
        if value not in {-1, 0, 1}:
            raise ValueError("Vote must be -1, 0, or 1")
        self.get_post(post_id, viewer_id=viewer_id)
        key = {"pk": f"POST#{post_id}", "sk": f"VOTE#{viewer_id}"}
        if value == 0:
            self._table.delete_item(Key=key)
        else:
            self._put({**key, "entity": "vote", "value": value, "created_at": iso_now()})
        up, down, _ = self._post_aggregate(post_id)
        return VoteResponse(
            post_id=post_id, value=value, upvotes=up, downvotes=down, vote_score=up - down
        )

    def follow_post(self, viewer_id: str, post_id: str, following: bool) -> dict[str, Any]:
        self.get_post(post_id, viewer_id=viewer_id)
        key = {"pk": f"POST#{post_id}", "sk": f"FOLLOW#{viewer_id}"}
        if following:
            self._put({**key, "entity": "follow", "created_at": iso_now()})
        else:
            self._table.delete_item(Key=key)
        return {"post_id": post_id, "following": following}

    def save_post(self, viewer_id: str, post_id: str, saved: bool) -> dict[str, Any]:
        self.get_post(post_id, viewer_id=viewer_id)
        key = {"pk": f"POST#{post_id}", "sk": f"SAVE#{viewer_id}"}
        if saved:
            self._put({**key, "entity": "save", "created_at": iso_now()})
        else:
            self._table.delete_item(Key=key)
        return {"post_id": post_id, "saved": saved}

    def mute_post(self, viewer_id: str, post_id: str, muted: bool) -> dict[str, Any]:
        self.get_post(post_id, viewer_id=viewer_id)
        key = {"pk": f"POST#{post_id}", "sk": f"MUTE#{viewer_id}"}
        if muted:
            self._put({**key, "entity": "mute", "created_at": iso_now()})
        else:
            self._table.delete_item(Key=key)
        return {"post_id": post_id, "muted": muted}

    def follow_subject(
        self, viewer_id: str, subject_type: str, value: str, following: bool
    ) -> FollowSubject:
        clean = " ".join(value.strip().split())
        key_value = base64.urlsafe_b64encode(clean.casefold().encode()).decode().rstrip("=")
        key = {"pk": f"USER#{viewer_id}", "sk": f"SUBJECT#{subject_type}#{key_value}"}
        now = iso_now()
        if following:
            self._put(
                {
                    **key,
                    "entity": "subject_follow",
                    "subject_type": subject_type,
                    "subject_value": clean,
                    "created_at": now,
                }
            )
        else:
            self._table.delete_item(Key=key)
        return FollowSubject(
            subject_type=subject_type, value=clean, following=following, created_at=self._dt(now)
        )

    def list_subject_follows(self, viewer_id: str) -> list[FollowSubject]:
        return [
            FollowSubject(
                subject_type=str(item["subject_type"]),
                value=str(item["subject_value"]),
                following=True,
                created_at=self._dt(item.get("created_at")),
            )
            for item in self._query(f"USER#{viewer_id}", "SUBJECT#", ascending=False)
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
            if not self._find_comment(target_id):
                raise NotFoundError("Comment not found")
        else:
            raise ValueError("Report target must be a post or comment")
        report_id = str(uuid.uuid4())
        now = iso_now()
        item = {
            "pk": f"REPORT#{report_id}",
            "sk": "META",
            "entity": "report",
            "id": report_id,
            "target_type": target_type,
            "target_id": target_id,
            "reporter_id": reporter_id,
            "reason": reason,
            "details": details,
            "status": "open",
            "created_at": now,
            "updated_at": now,
        }
        self._put(item)
        self._put({**item, "pk": "MODERATION", "sk": f"REPORT#{now}#{report_id}"})
        return self._report_from_item(item)

    def list_reports(self, status: str | None = "open", limit: int = 100) -> list[Report]:
        result = [
            self._report_from_item(item)
            for item in self._query("MODERATION", "REPORT#", ascending=False)
        ]
        if status:
            result = [item for item in result if item.status == status]
        return result[: max(1, min(limit, 250))]

    def list_moderation_actions(self, limit: int = 100) -> list[dict[str, Any]]:
        items = self._query("MODERATION", "ACTION#", ascending=False)[: max(1, min(limit, 250))]
        return [
            {
                "id": str(item["id"]),
                "moderator_id": str(item["moderator_id"]),
                "target_type": str(item["target_type"]),
                "target_id": str(item["target_id"]),
                "action": str(item["action"]),
                "note": item.get("note"),
                "created_at": self._dt(item.get("created_at")),
            }
            for item in items
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
        if target_type == "post":
            item = self._post_item(target_id)
            if action not in {"hide", "restore", "lock", "unlock"}:
                raise ValueError("Unsupported post moderation action")
            fields = {
                "hide": {"moderation_state": "hidden"},
                "restore": {"moderation_state": "visible"},
                "lock": {"locked": True, "moderation_state": "locked"},
                "unlock": {"locked": False, "moderation_state": "visible"},
            }[action]
            self._update_post_fields(target_id, fields)
        elif target_type == "comment":
            found = self._find_comment(target_id)
            if not found:
                raise NotFoundError("Comment not found")
            item, post_id = found
            if action not in {"hide", "restore"}:
                raise ValueError("Unsupported comment moderation action")
            was_counted = item.get("moderation_state", "visible") == "visible" and not item.get(
                "deleted_at"
            )
            next_state = "hidden" if action == "hide" else "visible"
            self._update_fields(
                f"POST#{post_id}", str(item["sk"]), {"moderation_state": next_state}
            )
            if was_counted != (next_state == "visible") and not item.get("deleted_at"):
                self._update_post_fields(post_id, {"updated_at": now})
        elif target_type == "report":
            report = self._get(f"REPORT#{target_id}", "META")
            if not report:
                raise NotFoundError("Report not found")
            if action not in {"resolve_report", "dismiss_report"}:
                raise ValueError("Unsupported report moderation action")
            status = "resolved" if action == "resolve_report" else "dismissed"
            self._update_fields(
                f"REPORT#{target_id}", "META", {"status": status, "updated_at": now}
            )
            for item in self._query("MODERATION", "REPORT#"):
                if item.get("id") == target_id:
                    self._update_fields(
                        str(item["pk"]), str(item["sk"]), {"status": status, "updated_at": now}
                    )
                    break
        else:
            raise ValueError("Moderation target must be a post, comment, or report")
        action_id = str(uuid.uuid4())
        self._put(
            {
                "pk": "MODERATION",
                "sk": f"ACTION#{now}#{action_id}",
                "entity": "moderation_action",
                "id": action_id,
                "moderator_id": moderator_id,
                "target_type": target_type,
                "target_id": target_id,
                "action": action,
                "note": note,
                "created_at": now,
            }
        )
        return {
            "id": action_id,
            "moderator_id": moderator_id,
            "target_type": target_type,
            "target_id": target_id,
            "action": action,
            "note": note,
            "created_at": self._dt(now),
        }

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
        item = {
            "pk": f"USER#{owner_id}",
            "sk": f"NOTIFICATION#{now}#{notification_id}",
            "entity": "notification",
            "id": notification_id,
            "owner_id": owner_id,
            "kind": kind,
            "message": message,
            "post_id": post_id,
            "ticket_id": ticket_id,
            "read": False,
            "created_at": now,
        }
        self._put(item)
        return {**item, "read": False, "created_at": self._dt(now)}

    def list_notifications(self, owner_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return [
            {
                "id": str(item["id"]),
                "owner_id": owner_id,
                "kind": str(item["kind"]),
                "message": str(item["message"]),
                "post_id": item.get("post_id"),
                "ticket_id": item.get("ticket_id"),
                "read": bool(item.get("read", False)),
                "created_at": self._dt(item.get("created_at")),
            }
            for item in self._query(f"USER#{owner_id}", "NOTIFICATION#", ascending=False)[
                : max(1, min(limit, 100))
            ]
        ]

    def mark_notification(self, owner_id: str, notification_id: str, read: bool = True) -> None:
        for item in self._query(f"USER#{owner_id}", "NOTIFICATION#"):
            if item.get("id") == notification_id:
                self._update_fields(str(item["pk"]), str(item["sk"]), {"read": read})
                return
        raise NotFoundError("Notification not found")

    def mark_all_notifications(self, owner_id: str, read: bool = True) -> None:
        for item in self._query(f"USER#{owner_id}", "NOTIFICATION#"):
            self._update_fields(str(item["pk"]), str(item["sk"]), {"read": read})

    # ---- shares -----------------------------------------------------------
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
        item = {
            "pk": f"SHARE#{token}",
            "sk": "META",
            "entity": "share",
            "token": token,
            "post_id": post.id,
            "owner_id": owner_id,
            "title": redact_public_text(post.title),
            "body": redact_public_text(post.body),
            "locality": post.locality,
            "visibility": post.visibility,
            "civitas_ticket_id": post.civitas_ticket_id,
            "status": post.status.value,
            "authority_id": post.authority_id,
            "expires_at": expires.isoformat(),
            "revoked": False,
            "created_at": now.isoformat(),
        }
        self._put(item)
        return ShareSnapshot(
            token=token, post_id=post.id, url=f"/share/{token}", expires_at=expires, revoked=False
        )

    def revoke_share_snapshot(self, owner_id: str, token: str) -> ShareSnapshot:
        item = self._get(f"SHARE#{token}", "META")
        if not item or item.get("owner_id") != owner_id:
            raise NotFoundError("Share snapshot not found")
        self._update_fields(f"SHARE#{token}", "META", {"revoked": True})
        return ShareSnapshot(
            token=token,
            post_id=str(item["post_id"]),
            url=f"/share/{token}",
            expires_at=self._dt(item.get("expires_at")),
            revoked=True,
        )

    def get_share_snapshot(self, token: str) -> dict[str, Any]:
        item = self._get(f"SHARE#{token}", "META")
        if not item or bool(item.get("revoked")) or self._dt(item.get("expires_at")) <= utc_now():
            raise NotFoundError("Share snapshot has expired or was revoked")
        return {
            "token": token,
            "post_id": str(item["post_id"]),
            "civitas_ticket_id": str(item["civitas_ticket_id"]),
            "title": str(item["title"]),
            "body": str(item["body"]),
            "locality": item.get("locality"),
            "visibility": str(item["visibility"]),
            "status": str(item["status"]),
            "authority_id": item.get("authority_id"),
            "expires_at": self._dt(item.get("expires_at")),
        }

    # ---- conversions and seed data ---------------------------------------
    @staticmethod
    def _attachment_from_item(item: dict[str, Any]) -> Attachment:
        return Attachment(
            id=str(item["id"]),
            thread_id=item.get("thread_id"),
            case_id=item.get("case_id"),
            owner_id=str(item["owner_id"]),
            filename=str(item["filename"]),
            content_type=str(item["content_type"]),
            size_bytes=int(item["size_bytes"]),
            sha256=str(item["sha256"]),
            storage_key=str(item["storage_key"]),
            visibility=str(item.get("visibility", "private")),
            created_at=parse_datetime(str(item["created_at"])),
        )

    @staticmethod
    def _ticket_from_item(item: dict[str, Any]) -> ComplaintTicket:
        return ComplaintTicket(
            id=str(item["id"]),
            civitas_ticket_id=str(item["civitas_ticket_id"]),
            thread_id=item.get("thread_id"),
            case_id=item.get("case_id"),
            owner_id=str(item["owner_id"]),
            title=str(item["title"]),
            description=str(item["description"]),
            authority_id=item.get("authority_id"),
            locality=item.get("locality"),
            status=TicketStatus(str(item["status"])),
            visibility=TicketVisibility(str(item["visibility"])),
            external_reference_id=item.get("external_reference_id"),
            acknowledgement=item.get("acknowledgement"),
            tracking_url=item.get("tracking_url"),
            submitted_content_hash=item.get("submitted_content_hash"),
            last_checkpoint_id=item.get("last_checkpoint_id"),
            public_post_id=item.get("public_post_id"),
            created_at=parse_datetime(str(item["created_at"])),
            updated_at=parse_datetime(str(item["updated_at"])),
        )

    @staticmethod
    def _preparation_from_item(item: dict[str, Any], ticket: ComplaintTicket) -> TicketPreparation:
        fields = DynamoCommunityStore._decode(item.get("fields_json"), {})
        required = DynamoCommunityStore._decode(item.get("required_fields_json"), [])
        missing = DynamoCommunityStore._decode(item.get("missing_fields_json"), [])
        attachment_ids = DynamoCommunityStore._decode(item.get("attachment_ids_json"), [])
        return TicketPreparation(
            id=str(item["id"]),
            ticket_id=ticket.id,
            civitas_ticket_id=ticket.civitas_ticket_id,
            authority_id=item.get("authority_id"),
            destination=item.get("destination"),
            intake_url=item.get("destination") if item.get("destination") else None,
            fields={str(k): str(v) for k, v in fields.items()} if isinstance(fields, dict) else {},
            required_fields=[str(v) for v in required] if isinstance(required, list) else [],
            missing_fields=[str(v) for v in missing] if isinstance(missing, list) else [],
            attachment_ids=[str(v) for v in attachment_ids]
            if isinstance(attachment_ids, list)
            else [],
            content_hash=str(item["content_hash"]),
            status=str(item["status"]),
            submission_enabled=False,
            created_at=parse_datetime(str(item["created_at"])),
            updated_at=parse_datetime(str(item["updated_at"])),
            approved_at=parse_datetime(str(item["approved_at"]))
            if item.get("approved_at")
            else None,
            approval_expires_at=parse_datetime(str(item["approval_expires_at"]))
            if item.get("approval_expires_at")
            else None,
        )

    @staticmethod
    def _checkpoint_from_item(item: dict[str, Any]) -> Checkpoint:
        return Checkpoint(
            id=str(item["id"]),
            ticket_id=str(item["ticket_id"]),
            preparation_id=item.get("preparation_id"),
            phase=str(item["phase"]),
            summary=str(item["summary"]),
            created_at=parse_datetime(str(item["created_at"])),
        )

    @staticmethod
    def _comment_from_item(item: dict[str, Any], viewer_id: str | None = None) -> Comment:
        return Comment(
            id=str(item["id"]),
            post_id=str(item["post_id"]),
            author_id=None,
            author_name=str(item["author_name"]),
            body=str(item["body"]),
            parent_id=item.get("parent_id"),
            created_at=parse_datetime(str(item["created_at"])),
            updated_at=parse_datetime(str(item["updated_at"])),
            is_demo=bool(item.get("is_demo", False)),
            is_owner=bool(viewer_id and item.get("owner_id") == viewer_id),
            moderation_state=str(item.get("moderation_state", "visible")),
            is_deleted=bool(item.get("deleted_at")),
        )

    @staticmethod
    def _report_from_item(item: dict[str, Any]) -> Report:
        return Report(
            id=str(item["id"]),
            target_type=str(item["target_type"]),
            target_id=str(item["target_id"]),
            reporter_id=str(item["reporter_id"]),
            reason=str(item["reason"]),
            details=item.get("details"),
            status=str(item.get("status", "open")),
            created_at=parse_datetime(str(item["created_at"])),
            updated_at=parse_datetime(str(item["updated_at"])),
        )

    def _seed_demo_feed(self) -> None:
        if self._query("FEED", "POST#"):
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
                4,
            ),
        ]
        for (
            post_id,
            ticket_id,
            civitas_id,
            author,
            title,
            body,
            locality,
            visibility,
            status,
            authority,
            evidence_count,
        ) in demos:
            item = {
                "pk": f"POST#{post_id}",
                "sk": "META",
                "entity": "public_post",
                "id": post_id,
                "ticket_id": ticket_id,
                "civitas_ticket_id": civitas_id,
                "owner_id": "system",
                "author_name": author,
                "title": title,
                "body": body,
                "locality": locality,
                "visibility": visibility,
                "status": status,
                "authority_id": authority,
                "evidence_count": evidence_count,
                "is_demo": True,
                "moderation_state": "visible",
                "locked": False,
                "created_at": now,
                "updated_at": now,
            }
            self._put(item, condition="attribute_not_exists(pk)")
            self._put({**item, "pk": "FEED", "sk": f"POST#{now}#{post_id}"})
            for index in range(evidence_count + 10):
                self._put(
                    {
                        "pk": f"POST#{post_id}",
                        "sk": f"VOTE#demo-{index}",
                        "entity": "vote",
                        "value": 1 if index < evidence_count + 5 else -1,
                        "created_at": now,
                    }
                )
