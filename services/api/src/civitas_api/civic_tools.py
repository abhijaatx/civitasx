"""Application-owned civic tools exposed to the ReAct model.

The model receives only these JSON Schemas.  It never gets direct database or
browser access, and every tool returns structured data that can be persisted as
evidence or reviewed as an explicit action.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from pypdf import PdfReader

from .community_store import LocalCommunityStore
from .live_connectors import LiveConnectorRegistry
from .research import ResearchIndex

CIVIC_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_official_records",
            "description": (
                "Search verified Bengaluru government documents. Use the returned "
                "passages and source IDs as the only basis for civic claims."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 3},
                    "authority_id": {"type": ["string", "null"]},
                },
                "required": ["query", "authority_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_ward_and_authority",
            "description": (
                "Resolve a Bengaluru locality, landmark, or street to a locality "
                "and likely ward/zone/agency. Boundary-sensitive results must be "
                "shown as candidates and confirmed before submission."
            ),
            "parameters": {
                "type": "object",
                "properties": {"location_text": {"type": "string", "minLength": 2}},
                "required": ["location_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_complaint_ticket",
            "description": (
                "Prepare a structured private complaint payload. This never submits "
                "to a government portal or publishes to the civic feed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 3, "maxLength": 180},
                    "description": {"type": "string", "minLength": 10, "maxLength": 10000},
                    "locality": {"type": "string", "minLength": 2, "maxLength": 160},
                    "authority_id": {"type": "string", "minLength": 2, "maxLength": 80},
                },
                "required": ["title", "description", "locality", "authority_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_official_documents",
            "description": (
                "Compute page-linked text differences between two indexed official documents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "current_doc_id": {"type": "string", "minLength": 3},
                    "baseline_doc_id": {"type": "string", "minLength": 3},
                },
                "required": ["current_doc_id", "baseline_doc_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_private_attachments",
            "description": (
                "Extract text and metadata from private files attached to the current thread. "
                "Use this before relying on an attachment; private files never become "
                "public evidence."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "attachment_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 8},
                        "minItems": 1,
                        "maxItems": 8,
                    }
                },
                "required": ["attachment_ids"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_live_official_sources",
            "description": (
                "List the configured allowlisted read-only Bengaluru government sources, "
                "their freshness, capabilities, and whether a refresh is currently safe."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refresh_live_official_source",
            "description": (
                "Refresh one named allowlisted public government source. This is read-only; "
                "it never submits a complaint or authenticates to a portal."
            ),
            "parameters": {
                "type": "object",
                "properties": {"endpoint_id": {"type": "string", "minLength": 2, "maxLength": 120}},
                "required": ["endpoint_id"],
                "additionalProperties": False,
            },
        },
    },
]


@dataclass(frozen=True)
class LocalityRecord:
    canonical: str
    zone: str
    ward: str
    aliases: tuple[str, ...]
    authority_id: str = "gba"
    boundary_note: str | None = None


def _row(
    canonical: str,
    zone: str,
    aliases: tuple[str, ...],
    *,
    authority_id: str = "gba",
    boundary_note: str | None = None,
) -> LocalityRecord:
    return LocalityRecord(
        canonical, zone, f"{canonical} ward candidate", aliases, authority_id, boundary_note
    )


# This is a broad locality gazetteer, not a fabricated legal ward map. Ward
# boundaries change and some named areas sit outside the core BBMP boundary;
# results therefore carry a confirmation flag and a clear provenance note.
BENGALURU_GAZETTEER: tuple[LocalityRecord, ...] = (
    _row("Indiranagar", "East", ("indiranagar", "12th main", "100 feet road")),
    _row("Domlur", "East", ("domlur",)),
    _row("Ulsoor", "East", ("ulsoor", "halasuru")),
    _row("CV Raman Nagar", "East", ("cv raman nagar", "c v raman nagar")),
    _row("Jeevan Bima Nagar", "East", ("jeevan bima nagar",)),
    _row("HAL", "East", ("hal airport", "hal")),
    _row("Marathahalli", "East", ("marathahalli", "marathalli")),
    _row("Bellandur", "East", ("bellandur", "bellanduru")),
    _row("Brookefield", "East", ("brookefield", "brook field")),
    _row("Whitefield", "East", ("whitefield", "itpl")),
    _row("Hoodi", "East", ("hoodi",)),
    _row("KR Puram", "East", ("kr puram", "k r puram", "krishnarajapuram")),
    _row("Mahadevapura", "East", ("mahadevapura",)),
    _row("Ramamurthy Nagar", "East", ("ramamurthy nagar", "ramamurthinagar")),
    _row("Varthur", "East", ("varthur",)),
    _row("Kadugodi", "East", ("kadugodi",)),
    _row("HSR Layout", "South East", ("hsr", "hsr layout", "hsr sector")),
    _row("Bommanahalli", "South", ("bommanahalli",)),
    _row("BTM Layout", "South", ("btm", "btm layout")),
    _row("JP Nagar", "South", ("jp nagar", "j p nagar")),
    _row("Jayanagar", "South", ("jayanagar", "jaya nagar")),
    _row("Banashankari", "South", ("banashankari", "bsk")),
    _row("Basavanagudi", "South", ("basavanagudi",)),
    _row("Koramangala", "South East", ("koramangala", "kormangala")),
    _row("Ejipura", "South East", ("ejipura",)),
    _row("Adugodi", "South East", ("adugodi",)),
    _row("Begur", "South", ("begur",)),
    _row(
        "Electronic City",
        "South",
        ("electronic city", "ecity", "e-city"),
        boundary_note="Confirm current BBMP/urban district boundary before routing.",
    ),
    _row(
        "Sarjapur Road",
        "South East",
        ("sarjapur road", "sarjapur"),
        boundary_note="Confirm the jurisdiction at the exact landmark.",
    ),
    _row("Vijayanagar", "West", ("vijayanagar",)),
    _row("Rajajinagar", "West", ("rajajinagar", "rajaji nagar")),
    _row("Malleshwaram", "West", ("malleshwaram", "malleswaram")),
    _row("Yeshwanthpur", "West", ("yeshwanthpur", "yeswantpur")),
    _row("Nagarbhavi", "West", ("nagarbhavi",)),
    _row("Kengeri", "West", ("kengeri",)),
    _row("Kumbalgodu", "West", ("kumbalgodu",)),
    _row("Hebbal", "North", ("hebbal",)),
    _row("Nagawara", "North", ("nagawara",)),
    _row("Hennur", "North East", ("hennur", "hennur road")),
    _row("Kalyan Nagar", "North East", ("kalyan nagar",)),
    _row("Banaswadi", "North East", ("banaswadi",)),
    _row("Kammanahalli", "North East", ("kammanahalli",)),
    _row("RT Nagar", "North", ("rt nagar", "r t nagar")),
    _row("Sadashivanagar", "North", ("sadashivanagar",)),
    _row("Yelahanka", "North", ("yelahanka",)),
    _row(
        "Devanahalli",
        "North",
        ("devanahalli",),
        boundary_note="May fall outside BBMP; confirm district authority.",
    ),
    _row("Shivajinagar", "Central", ("shivajinagar",)),
    _row("Frazer Town", "Central", ("frazer town", "fraser town")),
    _row("Cox Town", "Central", ("cox town",)),
    _row("Richmond Town", "Central", ("richmond town",)),
    _row("MG Road", "Central", ("mg road", "m g road")),
    _row("Bengaluru Urban", "Citywide", ("bengaluru", "bangalore", "bengaluru city")),
)

_ALIAS_TO_RECORD = {
    alias.casefold(): record for record in BENGALURU_GAZETTEER for alias in record.aliases
}

AUTHORITY_NAMES = {
    "gba": "Greater Bengaluru Authority / BBMP civic services",
    "bda": "Bangalore Development Authority",
    "bmrcl": "Bangalore Metro Rail Corporation Limited",
    "bwssb": "Bangalore Water Supply and Sewerage Board",
    "bescom": "Bangalore Electricity Supply Company",
}


def resolve_location(location_text: str) -> dict[str, Any]:
    normalized = " ".join(re.sub(r"[^a-z0-9 -]", " ", location_text.casefold()).split())
    record = next(
        (
            item
            for alias, item in sorted(
                _ALIAS_TO_RECORD.items(), key=lambda pair: len(pair[0]), reverse=True
            )
            if alias in normalized
        ),
        None,
    )
    if record is None:
        locality = normalized.title() or "Unspecified Bengaluru location"
        zone = "Unverified"
        confidence = "low"
        ward = None
        note = (
            "No matching gazetteer entry was found; ask for a nearby landmark or parcel reference."
        )
    else:
        locality = record.canonical
        zone = record.zone
        confidence = "medium"
        ward = record.ward
        note = (
            record.boundary_note
            or "Ward label is a candidate and must be confirmed before submission."
        )
    authority_id = record.authority_id if record else "gba"
    if any(term in normalized for term in ("metro", "station access", "namma metro")):
        authority_id = "bmrcl"
    elif any(term in normalized for term in ("water", "sewer", "drainage water")):
        authority_id = "bwssb"
    elif any(
        term in normalized for term in ("electricity", "power cut", "transformer", "street light")
    ):
        authority_id = "bescom"
    elif any(
        term in normalized for term in ("zoning", "land use", "layout approval", "building plan")
    ):
        authority_id = "bda"
    return {
        "location_text": location_text,
        "canonical_locality": locality,
        "ward": ward,
        "zone": zone,
        "authority_id": authority_id,
        "authority_name": AUTHORITY_NAMES[authority_id],
        "confidence": confidence,
        "needs_confirmation": True,
        "boundary_note": note,
        "provenance": (
            "CivitasX local Bengaluru locality gazetteer; verify the current official "
            "boundary before filing."
        ),
    }


class CivicToolRuntime:
    def __init__(
        self,
        index: ResearchIndex,
        community: LocalCommunityStore,
        live_registry: LiveConnectorRegistry | None = None,
    ):
        self.index = index
        self.community = community
        self.live_registry = live_registry

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return CIVIC_TOOL_DEFINITIONS

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        owner_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        if name == "search_official_records":
            query = str(arguments.get("query", "")).strip()
            authority_id = arguments.get("authority_id")
            answer = self.index.answer(query, max_sources=6, authority_id=authority_id)
            return {"status": "ok", "answer": answer.model_dump(mode="json")}
        if name == "resolve_ward_and_authority":
            return {
                "status": "ok",
                "resolution": resolve_location(str(arguments.get("location_text", ""))),
            }
        if name == "draft_complaint_ticket":
            title = str(arguments.get("title", "")).strip()
            description = str(arguments.get("description", "")).strip()
            locality = str(arguments.get("locality", "")).strip()
            authority_id = str(arguments.get("authority_id", "")).strip()
            return {
                "status": "draft_ready",
                "submission_enabled": False,
                "payload": {
                    "title": title,
                    "description": description,
                    "locality": locality,
                    "authority_id": authority_id,
                    "ticket_status": "draft",
                    "visibility": "private",
                },
                "next_step": (
                    "Show the payload to the resident and wait for explicit ticket "
                    "creation approval."
                ),
            }
        if name == "compare_official_documents":
            comparison = self.index.compare_documents(
                str(arguments.get("current_doc_id", "")),
                str(arguments.get("baseline_doc_id", "")),
            )
            return {"status": "ok", "comparison": comparison.model_dump(mode="json")}
        if name == "inspect_private_attachments":
            if not owner_id or not thread_id:
                return {"status": "error", "error": "Attachment scope is unavailable"}
            attachment_ids = arguments.get("attachment_ids")
            if not isinstance(attachment_ids, list) or not attachment_ids:
                return {"status": "error", "error": "At least one attachment ID is required"}
            inspected: list[dict[str, Any]] = []
            for attachment_id in attachment_ids[:8]:
                attachment = self.community.get_attachment(owner_id, str(attachment_id))
                if attachment.thread_id != thread_id:
                    return {"status": "error", "error": "Attachment does not belong to this thread"}
                path = self.community.attachment_path(attachment)
                item: dict[str, Any] = {
                    "attachment_id": attachment.id,
                    "filename": attachment.filename,
                    "content_type": attachment.content_type,
                    "size_bytes": attachment.size_bytes,
                    "sha256": attachment.sha256,
                }
                if (
                    attachment.content_type == "application/pdf"
                    or attachment.filename.lower().endswith(".pdf")
                ):
                    reader = PdfReader(str(path))
                    pages: list[dict[str, Any]] = []
                    for page_number, page in enumerate(reader.pages[:20], start=1):
                        text = (page.extract_text() or "").strip()
                        if text:
                            pages.append({"page": page_number, "text": text[:6000]})
                    item["pages"] = pages
                    item["page_count"] = len(reader.pages)
                elif (
                    attachment.content_type.startswith("text/")
                    or attachment.filename.lower().endswith((".txt", ".csv", ".json", ".md"))
                ):
                    item["text"] = path.read_text(encoding="utf-8", errors="replace")[:12000]
                else:
                    item["note"] = (
                        "Binary media is attached; use the filename and metadata, and ask "
                        "the resident for a description if visual inspection is needed."
                    )
                inspected.append(item)
            return {"status": "ok", "attachments": inspected, "private": True}
        if name == "list_live_official_sources":
            if self.live_registry is None:
                return {"status": "unavailable", "error": "Live source registry is not configured"}
            profiles = await asyncio.to_thread(self.live_registry.list, self.index)
            return {
                "status": "ok",
                "sources": [profile.model_dump(mode="json") for profile in profiles],
                "read_only": True,
            }
        if name == "refresh_live_official_source":
            if self.live_registry is None:
                return {"status": "unavailable", "error": "Live source registry is not configured"}
            endpoint_id = str(arguments.get("endpoint_id", "")).strip()
            result = await asyncio.to_thread(self.live_registry.refresh, endpoint_id, self.index)
            return {
                "status": "ok",
                "message": result.message,
                "endpoint": result.endpoint.model_dump(mode="json"),
                "read_only": True,
            }
        return {"status": "error", "error": f"Unknown civic tool: {name}"}
