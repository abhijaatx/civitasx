"""Read-only connectors for public Bengaluru government sources.

The registry is deliberately explicit: every URL is allowlisted, every fetch
is GET-only, and the response is stored as a page-addressable source with a
content hash. A missing API key or a portal failure becomes a visible status;
the agent never silently substitutes a local fixture for a failed live fetch.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .errors import RateLimitError
from .models import LiveEndpointProfile, LiveRefreshResult
from .research import DocumentIngestor, ResearchIndex


@dataclass(frozen=True)
class LiveEndpointSpec:
    endpoint_id: str
    authority_id: str
    authority_name: str
    title: str
    url: str
    transport: str = "html"
    requires_env: str | None = None
    source_kind: str = "official"
    priority: str = "P2"
    access_mode: str = "public_read"
    capabilities: tuple[str, ...] = ("read_public",)
    docs_url: str | None = None
    refreshable: bool = True
    consent_scope: str | None = None


LIVE_ENDPOINTS: tuple[LiveEndpointSpec, ...] = (
    LiveEndpointSpec(
        "gba-home",
        "gba",
        "Greater Bengaluru Authority",
        "GBA public portal",
        "https://bbmp.gov.in/",
        priority="P0",
        access_mode="browser_only",
        capabilities=(
            "sahaaya_grievance",
            "ekhata",
            "property_tax",
            "building_permission",
            "trade_licence",
            "birth_death",
            "gis",
            "road_history",
        ),
        docs_url="https://bbmp.gov.in/",
    ),
    LiveEndpointSpec(
        "gba-legacy-api-help",
        "gba",
        "Greater Bengaluru Authority",
        "BBMP legacy Indira Canteens API documentation",
        "https://webapps.bbmpgov.in/ICWebApi/Help",
    ),
    LiveEndpointSpec(
        "bengaluru-open-data-gba",
        "gba",
        "Greater Bengaluru Authority",
        "Bengaluru Open Data — GBA/BBMP datasets",
        "https://opendata.benscl.com/?q=group/bruhat-bengaluru-mahanagara-palike",
        priority="P0",
        capabilities=("discover_datasets", "query_public", "refresh"),
    ),
    LiveEndpointSpec(
        "bengaluru-open-data-bmrcl",
        "bmrcl",
        "Bangalore Metro Rail Corporation Limited",
        "Bengaluru Open Data — BMRCL datasets",
        "https://opendata.benscl.com/?q=group/bangalore-metro-rail-corporation-limited-bmrcl",
        priority="P1",
        capabilities=("discover_datasets", "query_public", "refresh"),
    ),
    LiveEndpointSpec(
        "bengaluru-open-data-bwssb",
        "bwssb",
        "Bangalore Water Supply and Sewerage Board",
        "Bengaluru Open Data — BWSSB datasets",
        "https://opendata.benscl.com/?q=group/bangalore-water-supply-and-sewerage-board",
        priority="P1",
        capabilities=("discover_datasets", "query_public", "refresh"),
    ),
    LiveEndpointSpec(
        "bengaluru-open-data-bmrcl-phase-2a",
        "bmrcl",
        "Bangalore Metro Rail Corporation Limited",
        "BMRCL Phase 2A alternatives analysis",
        "https://opendata.benscl.com/sites/default/files/056b88_CareerFiles.pdf",
        transport="pdf",
    ),
    LiveEndpointSpec(
        "bmrcl-home",
        "bmrcl",
        "Bangalore Metro Rail Corporation Limited",
        "Namma Metro public portal",
        "https://english.bmrc.co.in/",
    ),
    LiveEndpointSpec(
        "bmrcl-standees-ratecard-2025",
        "bmrcl",
        "Bangalore Metro Rail Corporation Limited",
        "BMRCL 2025 standee rate card",
        "https://english.bmrc.co.in/pd/StandeesRatecard2025.pdf",
        transport="pdf",
    ),
    LiveEndpointSpec(
        "bda-town-planning",
        "bda",
        "Bangalore Development Authority",
        "BDA town planning section",
        "https://eng.bdabangalore.org/town-planning-section.html",
    ),
    LiveEndpointSpec(
        "bda-acts-rules",
        "bda",
        "Bangalore Development Authority",
        "BDA acts and rules",
        "https://eng.bdabangalore.org/acts-rules-amendments.html",
    ),
    LiveEndpointSpec(
        "bda-important-links",
        "bda",
        "Bangalore Development Authority",
        "BDA official links and routes",
        "https://eng.bdabangalore.org/important-links.html",
    ),
    LiveEndpointSpec(
        "karnataka-open-data-portal",
        "karnataka",
        "Government of Karnataka",
        "Karnataka Open Government Data portal",
        "https://karnataka.data.gov.in/",
        priority="P0",
        capabilities=("discover_datasets", "query_public", "refresh"),
    ),
    LiveEndpointSpec(
        "data-gov-in-api",
        "karnataka",
        "Government of Karnataka",
        "India Open Government Data API",
        "https://api.data.gov.in/resource",
        transport="api",
        requires_env="CIVITAS_DATA_GOV_API_KEY",
        priority="P0",
        access_mode="api_key",
        capabilities=("discover", "query_public", "refresh"),
        docs_url="https://data.gov.in/help/apis",
    ),
    LiveEndpointSpec(
        "api-setu-discovery",
        "api-setu",
        "API Setu / NeGD",
        "API Setu API discovery and subscription documentation",
        "https://docs.apisetu.gov.in/document-central/explore-apisetu/Overview.html",
        priority="P0",
        access_mode="documentation",
        capabilities=("discover", "list_apis", "subscription_info"),
        docs_url="https://docs.apisetu.gov.in/document-central/explore-apisetu/Overview.html",
    ),
    LiveEndpointSpec(
        "digilocker-document-services",
        "digilocker",
        "DigiLocker / MeriPehchaan",
        "DigiLocker consent and document service documentation",
        "https://apisetu.gov.in/digilocker",
        priority="P0",
        access_mode="consent_required",
        capabilities=("consent", "pull_document", "verify_document"),
        docs_url="https://apisetu.gov.in/digilocker",
        requires_env="CIVITAS_DIGILOCKER_CLIENT_ID",
        refreshable=False,
        consent_scope="user-approved document pull; no private document access by default",
    ),
    LiveEndpointSpec(
        "seva-sindhu-services",
        "seva-sindhu",
        "Government of Karnataka",
        "Seva Sindhu service catalogue and application portal",
        "https://sevasindhu.karnataka.gov.in/Sevasindhu/English",
        priority="P0",
        access_mode="browser_only",
        capabilities=("discover_services", "prepare_application", "track_application"),
        docs_url="https://sevasindhu.karnataka.gov.in/Sevasindhu/English",
    ),
    LiveEndpointSpec(
        "cpgrams-grievances",
        "cpgrams",
        "Department of Administrative Reforms and Public Grievances",
        "CPGRAMS grievance registration and tracking portal",
        "https://www.pgportal.gov.in/",
        priority="P0",
        access_mode="approval_required",
        capabilities=("prepare_grievance", "track_grievance", "reminder", "appeal"),
        docs_url="https://www.pgportal.gov.in/",
        requires_env="CIVITAS_CPGRAMS_CLIENT_ID",
        refreshable=False,
    ),
    LiveEndpointSpec(
        "vahan-vehicle-services",
        "vahan",
        "Ministry of Road Transport and Highways",
        "VAHAN vehicle services",
        "https://parivahan.gov.in/",
        priority="P1",
        access_mode="approval_required",
        capabilities=("vehicle_lookup", "prepare_service"),
        docs_url="https://parivahan.gov.in/",
        requires_env="CIVITAS_PARIVAHAN_CLIENT_ID",
        refreshable=False,
    ),
    LiveEndpointSpec(
        "sarathi-driving-licence",
        "sarathi",
        "Ministry of Road Transport and Highways",
        "Sarathi driving licence services",
        "https://sarathi.parivahan.gov.in/sarathiservice/stateSelection.do",
        priority="P1",
        access_mode="approval_required",
        capabilities=("licence_lookup", "prepare_service"),
        docs_url="https://sarathi.parivahan.gov.in/",
        requires_env="CIVITAS_PARIVAHAN_CLIENT_ID",
        refreshable=False,
    ),
    LiveEndpointSpec(
        "echallan-transport",
        "echallan",
        "Ministry of Road Transport and Highways",
        "eChallan vehicle challan services",
        "https://echallan.parivahan.gov.in/",
        priority="P1",
        access_mode="browser_only",
        capabilities=("challan_lookup", "prepare_payment"),
        docs_url="https://echallan.parivahan.gov.in/",
    ),
    LiveEndpointSpec(
        "bhoomi-land-records",
        "bhoomi",
        "Government of Karnataka",
        "Bhoomi land records portal",
        "https://landrecords.karnataka.gov.in/",
        priority="P1",
        access_mode="browser_only",
        capabilities=("land_record_lookup", "view_public_record"),
        docs_url="https://landrecords.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "kaveri-property-registration",
        "kaveri",
        "Government of Karnataka",
        "Kaveri 2.0 property registration services",
        "https://kaveri.karnataka.gov.in/",
        priority="P1",
        access_mode="browser_only",
        capabilities=("property_lookup", "prepare_registration"),
        docs_url="https://kaveri.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "eswathu-rural-property",
        "e-swathu",
        "Government of Karnataka",
        "e-Swathu rural property records",
        "https://eswathu.karnataka.gov.in/",
        priority="P1",
        access_mode="browser_only",
        capabilities=("property_lookup", "view_public_record"),
        docs_url="https://eswathu.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "mojini-survey",
        "mojini",
        "Government of Karnataka",
        "Mojini land survey services",
        "https://www.mojini.karnataka.gov.in/",
        priority="P1",
        access_mode="browser_only",
        capabilities=("survey_lookup", "application_status"),
        docs_url="https://www.mojini.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "ecourts-case-status",
        "ecourts",
        "eCourts Mission Mode Project",
        "eCourts CNR and case status services",
        "https://services.ecourts.gov.in/ecourtindia_v6/casestatus/",
        priority="P1",
        access_mode="browser_only",
        capabilities=("case_lookup", "hearing_status", "order_lookup"),
        docs_url="https://services.ecourts.gov.in/ecourtindia_v6/casestatus/",
    ),
    LiveEndpointSpec(
        "bmtc-transit",
        "bmtc",
        "Bangalore Metropolitan Transport Corporation",
        "BMTC public transit services",
        "https://bmtc.co.in/",
        priority="P1",
        access_mode="browser_only",
        capabilities=("route_lookup", "service_status", "prepare_complaint"),
        docs_url="https://bmtc.co.in/",
    ),
    LiveEndpointSpec(
        "bwssb-water-services",
        "bwssb",
        "Bangalore Water Supply and Sewerage Board",
        "BWSSB water complaints and services",
        "https://bwssb.gov.in/",
        priority="P1",
        access_mode="browser_only",
        capabilities=("complaint_prepare", "service_status", "bill_lookup"),
        docs_url="https://bwssb.gov.in/",
    ),
    LiveEndpointSpec(
        "bescom-electricity",
        "bescom",
        "Bangalore Electricity Supply Company",
        "BESCOM electricity services and outage routes",
        "https://bescom.karnataka.gov.in/",
        priority="P1",
        access_mode="browser_only",
        capabilities=("outage_status", "complaint_prepare", "bill_lookup"),
        docs_url="https://bescom.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "myscheme-discovery",
        "myscheme",
        "Government of India",
        "myScheme scheme discovery and eligibility",
        "https://www.myscheme.gov.in/",
        priority="P2",
        access_mode="public_read",
        capabilities=("discover_schemes", "eligibility_guidance", "application_route"),
        docs_url="https://www.myscheme.gov.in/",
    ),
    LiveEndpointSpec(
        "umang-services",
        "umang",
        "Government of India",
        "UMANG citizen services catalogue",
        "https://web.umang.gov.in/",
        priority="P2",
        access_mode="browser_only",
        capabilities=("discover_services", "prepare_application", "track_application"),
        docs_url="https://web.umang.gov.in/",
    ),
    LiveEndpointSpec(
        "gstn-verification",
        "gstn",
        "Goods and Services Tax Network",
        "GSTN public taxpayer services",
        "https://www.gst.gov.in/",
        priority="P2",
        access_mode="approval_required",
        capabilities=("gstin_lookup", "prepare_service"),
        docs_url="https://www.gst.gov.in/",
        requires_env="CIVITAS_GSTN_CLIENT_ID",
        refreshable=False,
    ),
    LiveEndpointSpec(
        "udyam-registration",
        "udyam",
        "Ministry of Micro, Small and Medium Enterprises",
        "Udyam registration services",
        "https://udyamregistration.gov.in/",
        priority="P2",
        access_mode="browser_only",
        capabilities=("registration_prepare", "status_lookup"),
        docs_url="https://udyamregistration.gov.in/",
    ),
    LiveEndpointSpec(
        "mca-company-services",
        "mca",
        "Ministry of Corporate Affairs",
        "MCA company and filing services",
        "https://www.mca.gov.in/",
        priority="P2",
        access_mode="browser_only",
        capabilities=("company_lookup", "filing_route"),
        docs_url="https://www.mca.gov.in/",
    ),
    LiveEndpointSpec(
        "karnataka-eprocurement",
        "karnataka-eprocurement",
        "Government of Karnataka",
        "Karnataka e-Procurement portal",
        "https://eproc.karnataka.gov.in/",
        priority="P2",
        access_mode="browser_only",
        capabilities=("tender_search", "tender_document"),
        docs_url="https://eproc.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "abdm-health",
        "abdm",
        "National Health Authority",
        "ABDM health account and network documentation",
        "https://abdm.gov.in/",
        priority="P2",
        access_mode="consent_required",
        capabilities=("consent", "health-service-discovery"),
        docs_url="https://abdm.gov.in/",
        requires_env="CIVITAS_ABDM_CLIENT_ID",
        refreshable=False,
        consent_scope="explicit health-data consent; no clinical record access by default",
    ),
    LiveEndpointSpec(
        "abha-services",
        "abha",
        "National Health Authority",
        "ABHA citizen services",
        "https://abha.abdm.gov.in/",
        priority="P2",
        access_mode="consent_required",
        capabilities=("consent", "abha_lookup", "prepare_service"),
        docs_url="https://abha.abdm.gov.in/",
        requires_env="CIVITAS_ABHA_CLIENT_ID",
        refreshable=False,
        consent_scope="explicit user consent; identity and health identifiers are private",
    ),
    LiveEndpointSpec(
        "nad-digilocker-education",
        "nad",
        "National Academic Depository",
        "Academic credential verification via DigiLocker",
        "https://nad.digilocker.gov.in/",
        priority="P2",
        access_mode="consent_required",
        capabilities=("consent", "credential_verify", "pull_document"),
        docs_url="https://nad.digilocker.gov.in/",
        requires_env="CIVITAS_DIGILOCKER_CLIENT_ID",
        refreshable=False,
        consent_scope="user-approved academic credential pull",
    ),
    LiveEndpointSpec(
        "national-career-service",
        "ncs",
        "Ministry of Labour and Employment",
        "National Career Service jobs and skills",
        "https://www.ncs.gov.in/",
        priority="P2",
        access_mode="browser_only",
        capabilities=("job_search", "skill_service_discovery", "application_route"),
        docs_url="https://www.ncs.gov.in/",
    ),
)


def _allowlisted(url: str) -> bool:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in {None, 443}:
        return False
    host = (parsed.hostname or "").casefold()
    return host in {
        "bbmp.gov.in",
        "www.bbmp.gov.in",
        "webapps.bbmpgov.in",
        "opendata.benscl.com",
        "english.bmrc.co.in",
        "eng.bdabangalore.org",
        "karnataka.data.gov.in",
        "api.data.gov.in",
        "docs.apisetu.gov.in",
        "apisetu.gov.in",
        "sevasindhu.karnataka.gov.in",
        "www.pgportal.gov.in",
        "pgportal.gov.in",
        "parivahan.gov.in",
        "sarathi.parivahan.gov.in",
        "echallan.parivahan.gov.in",
        "landrecords.karnataka.gov.in",
        "kaveri.karnataka.gov.in",
        "eswathu.karnataka.gov.in",
        "www.mojini.karnataka.gov.in",
        "services.ecourts.gov.in",
        "bmtc.co.in",
        "bwssb.gov.in",
        "bescom.karnataka.gov.in",
        "www.myscheme.gov.in",
        "web.umang.gov.in",
        "www.gst.gov.in",
        "udyamregistration.gov.in",
        "www.mca.gov.in",
        "eproc.karnataka.gov.in",
        "abdm.gov.in",
        "abha.abdm.gov.in",
        "nad.digilocker.gov.in",
        "www.ncs.gov.in",
    }


class LiveConnectorRegistry:
    def __init__(
        self,
        *,
        timeout_seconds: int = 20,
        max_bytes: int = 25 * 1024 * 1024,
        status_path: str | Path | None = None,
        status_bucket: str | None = None,
        status_prefix: str = "runtime/live-status/",
        credentials: Mapping[str, str | None] | None = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.status_path = Path(status_path).expanduser() if status_path else None
        self.status_bucket = status_bucket.strip() if status_bucket else None
        self.status_prefix = status_prefix.strip("/") + "/"
        self.credentials = dict(credentials or {})
        self._status: dict[str, LiveEndpointProfile] = {}
        self._refresh_times: dict[str, float] = {}
        self._status_lock = threading.RLock()
        self._s3_client: Any | None = None
        self._load_status()

    def _s3(self) -> Any:
        if self._s3_client is None:
            import boto3

            self._s3_client = boto3.client("s3")
        return self._s3_client

    def _load_status(self) -> None:
        if self.status_bucket:
            try:
                paginator = self._s3().get_paginator("list_objects_v2")
                for page in paginator.paginate(
                    Bucket=self.status_bucket, Prefix=self.status_prefix
                ):
                    for item in page.get("Contents", []):
                        key = str(item.get("Key", ""))
                        if not key.endswith(".json"):
                            continue
                        body = self._s3().get_object(Bucket=self.status_bucket, Key=key)[
                            "Body"
                        ].read()
                        profile = LiveEndpointProfile.model_validate(json.loads(body))
                        self._status[profile.endpoint_id] = profile
                return
            except Exception:  # pragma: no cover - requires cloud credentials
                self._status = {}
        if self.status_path and self.status_path.exists():
            try:
                raw = json.loads(self.status_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._status = {
                        endpoint_id: LiveEndpointProfile.model_validate(profile)
                        for endpoint_id, profile in raw.items()
                        if isinstance(profile, dict)
                    }
            except (OSError, json.JSONDecodeError, ValueError):
                self._status = {}

    def list(self, index: ResearchIndex) -> list[LiveEndpointProfile]:
        profiles: list[LiveEndpointProfile] = []
        for spec in LIVE_ENDPOINTS:
            previous = self._status.get(spec.endpoint_id)
            source_ids = sorted(
                source_id
                for source_id in index.documents
                if source_id.startswith(f"live-{spec.endpoint_id}-")
            )
            latest_source = max(
                (index.documents[source_id] for source_id in source_ids),
                key=lambda document: document.retrieved_at or datetime.min.replace(tzinfo=UTC),
                default=None,
            )
            extraction_status = latest_source.extraction_status if latest_source else "unknown"
            if previous:
                profiles.append(
                    previous.model_copy(
                        update={
                            "priority": spec.priority,
                            "access_mode": spec.access_mode,
                            "capabilities": list(spec.capabilities),
                            "docs_url": spec.docs_url,
                            "refreshable": spec.refreshable,
                            "consent_scope": spec.consent_scope,
                            "source_ids": source_ids,
                            "source_extraction_status": extraction_status,
                        }
                    )
                )
                continue
            status = self._initial_status(spec, source_ids)
            profiles.append(
                LiveEndpointProfile(
                    endpoint_id=spec.endpoint_id,
                    authority_id=spec.authority_id,
                    authority_name=spec.authority_name,
                    title=spec.title,
                    url=spec.url,
                    source_kind=spec.source_kind,
                    transport=spec.transport,
                    status=status,
                    requires_env=spec.requires_env,
                    priority=spec.priority,
                    access_mode=spec.access_mode,
                    capabilities=list(spec.capabilities),
                    docs_url=spec.docs_url,
                    refreshable=spec.refreshable,
                    consent_scope=spec.consent_scope,
                    source_ids=source_ids,
                    source_extraction_status=extraction_status,
                )
            )
        return profiles

    def get(self, endpoint_id: str, index: ResearchIndex) -> LiveEndpointProfile | None:
        return next((item for item in self.list(index) if item.endpoint_id == endpoint_id), None)

    def query_data_gov(
        self,
        resource_id: str,
        *,
        query: str | None = None,
        filters: Mapping[str, str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Run a bounded read-only India OGD query with the publisher key.

        The resource identifier and filter keys are constrained so an API key
        cannot be redirected to an arbitrary URL or used for a write request.
        """

        api_key = self._credential("CIVITAS_DATA_GOV_API_KEY")
        if not api_key:
            raise PermissionError("Set CIVITAS_DATA_GOV_API_KEY before querying India OGD")
        if not re.fullmatch(r"[A-Za-z0-9-]{8,128}", resource_id):
            raise ValueError("resource_id must be the API Setu/data.gov.in resource identifier")
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, min(int(offset), 100_000))
        params: dict[str, str | int] = {
            "api-key": api_key,
            "format": "json",
            "limit": safe_limit,
            "offset": safe_offset,
        }
        if query:
            params["query"] = query[:200]
        for key, value in (filters or {}).items():
            if not re.fullmatch(r"[A-Za-z0-9_ -]{1,80}", key):
                raise ValueError("Filter names may contain only letters, numbers, spaces, _ or -")
            params[f"filters[{key}]"] = str(value)[:200]
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                headers={
                    "User-Agent": "CivitasX-local-connector/0.1",
                    "Accept": "application/json",
                },
            ) as client:
                response = client.get(
                    f"https://api.data.gov.in/resource/{resource_id}", params=params
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(f"India OGD query failed: {exc}") from exc
        if not isinstance(body, dict):
            raise RuntimeError("India OGD returned a non-object response")
        return body

    def _credential(self, name: str) -> str | None:
        return os.getenv(name) or self.credentials.get(name)

    def _initial_status(self, spec: LiveEndpointSpec, source_ids: list[str]) -> str:
        if spec.access_mode == "approval_required":
            return "approval_required"
        if spec.access_mode == "consent_required":
            return "consent_required"
        if spec.access_mode == "browser_only" and not source_ids:
            return "browser_only"
        if spec.requires_env and not self._credential(spec.requires_env):
            return "requires_api_key"
        return "ready" if source_ids else "unknown"

    def refresh(self, endpoint_id: str, index: ResearchIndex) -> LiveRefreshResult:
        spec = next((item for item in LIVE_ENDPOINTS if item.endpoint_id == endpoint_id), None)
        if spec is None:
            raise KeyError("Live endpoint not found")
        now_monotonic = time.monotonic()
        with self._status_lock:
            previous_refresh = self._refresh_times.get(endpoint_id, 0.0)
            if now_monotonic - previous_refresh < 30.0:
                raise RateLimitError("This connector was refreshed recently; try again shortly")
            self._refresh_times[endpoint_id] = now_monotonic
        checked_at = datetime.now(UTC)
        if not spec.refreshable:
            status = (
                "consent_required"
                if spec.access_mode == "consent_required"
                else "approval_required"
                if spec.access_mode == "approval_required"
                else "browser_only"
            )
            profile = self._save_status(
                spec,
                status=status,
                checked_at=checked_at,
                error=(
                    "This connector is metadata-only until the official approval or consent flow "
                    "is configured"
                ),
                source_ids=[],
            )
            return LiveRefreshResult(
                endpoint=profile,
                message=(
                    "No request was sent. Complete the official approval/consent flow and "
                    "configure "
                    "the connector credentials first."
                ),
            )
        if spec.access_mode == "approval_required" and not self._credential(
            spec.requires_env or ""
        ):
            profile = self._save_status(
                spec,
                status="approval_required",
                checked_at=checked_at,
                error="Official approval is required before this connector can be used",
                source_ids=[],
            )
            return LiveRefreshResult(
                endpoint=profile,
                message="No request was sent; official approval is required for this connector.",
            )
        if spec.access_mode == "consent_required" and not self._credential(spec.requires_env or ""):
            profile = self._save_status(
                spec,
                status="consent_required",
                checked_at=checked_at,
                error="Explicit user consent and approved credentials are required",
                source_ids=[],
            )
            return LiveRefreshResult(
                endpoint=profile,
                message="No request was sent; explicit consent is required for this connector.",
            )
        if spec.requires_env and not self._credential(spec.requires_env):
            profile = self._save_status(
                spec,
                status="requires_api_key",
                checked_at=checked_at,
                error=f"Set {spec.requires_env} before calling this endpoint",
                source_ids=[],
            )
            return LiveRefreshResult(
                endpoint=profile,
                message=f"This connector requires {spec.requires_env}; no request was sent.",
            )
        if not _allowlisted(spec.url):
            profile = self._save_status(
                spec,
                status="disabled",
                checked_at=checked_at,
                error="URL is not on the CivitasX allowlist",
                source_ids=[],
            )
            return LiveRefreshResult(endpoint=profile, message="Endpoint is not allowlisted")
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                # The endpoint itself is allowlisted.  Following a server
                # redirect would turn that check into an SSRF bypass.
                follow_redirects=False,
                headers={
                    "User-Agent": "CivitasX-local-connector/0.1 (+local civic research)",
                    "Accept": "text/html,application/pdf,application/json,text/plain,*/*",
                },
            ) as client:
                with client.stream("GET", spec.url) as response:
                    if 300 <= response.status_code < 400:
                        raise ValueError("Redirects are disabled for live civic sources")
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self.max_bytes:
                            raise ValueError(f"Response exceeds {self.max_bytes} byte safety limit")
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    content_type = response.headers.get("content-type", "")
            pages, method, extraction_status = DocumentIngestor().extract(
                raw, content_type=content_type, language="en"
            )
            digest = hashlib.sha256(raw).hexdigest()[:12]
            source_id = f"live-{spec.endpoint_id}-{digest}"
            entry = DocumentIngestor().manifest_entry(
                source_id=source_id,
                title=spec.title,
                authority=spec.authority_name,
                authority_id=spec.authority_id,
                raw=raw,
                pages=pages,
                url=spec.url,
                status="unknown",
                retrieved_at=checked_at,
                source_kind=spec.source_kind,
                extraction_method=method,
                extraction_status=extraction_status,
            )
            document = index.add_live_entry(entry)
            profile = self._save_status(
                spec,
                status="ready",
                checked_at=checked_at,
                error=None,
                source_ids=[document.source_id],
                extraction_status=document.extraction_status,
            )
            return LiveRefreshResult(
                endpoint=profile,
                fetched=True,
                source_id=document.source_id,
                bytes_fetched=len(raw),
                pages_indexed=len(pages),
                message=f"Fetched and indexed {len(pages)} page(s) from the official source.",
            )
        except (httpx.HTTPError, OSError, ValueError) as exc:
            profile = self._save_status(
                spec,
                status="offline",
                checked_at=checked_at,
                error=str(exc)[:500],
                source_ids=[],
            )
            return LiveRefreshResult(endpoint=profile, message=f"Live fetch failed: {exc}")

    def _save_status(
        self,
        spec: LiveEndpointSpec,
        *,
        status: str,
        checked_at: datetime,
        error: str | None,
        source_ids: list[str],
        extraction_status: str = "unknown",
    ) -> LiveEndpointProfile:
        profile = LiveEndpointProfile(
            endpoint_id=spec.endpoint_id,
            authority_id=spec.authority_id,
            authority_name=spec.authority_name,
            title=spec.title,
            url=spec.url,
            source_kind=spec.source_kind,
            transport=spec.transport,
            status=status,
            requires_env=spec.requires_env,
            priority=spec.priority,
            access_mode=spec.access_mode,
            capabilities=list(spec.capabilities),
            docs_url=spec.docs_url,
            refreshable=spec.refreshable,
            consent_scope=spec.consent_scope,
            last_checked_at=checked_at,
            last_error=error,
            source_ids=source_ids,
            source_extraction_status=extraction_status,
        )
        with self._status_lock:
            self._status[spec.endpoint_id] = profile
            if self.status_bucket:
                try:
                    self._s3().put_object(
                        Bucket=self.status_bucket,
                        Key=f"{self.status_prefix}{spec.endpoint_id}.json",
                        Body=(
                            json.dumps(profile.model_dump(mode="json"), ensure_ascii=False)
                            + "\n"
                        ).encode("utf-8"),
                        ContentType="application/json",
                    )
                except Exception:  # pragma: no cover - requires cloud credentials
                    # The in-memory result remains authoritative for this
                    # request; a status persistence outage must not turn a
                    # successful read-only fetch into a 500.
                    pass
            if self.status_path:
                self.status_path.parent.mkdir(parents=True, exist_ok=True)
                payload = (
                    json.dumps(
                        {key: value.model_dump(mode="json") for key, value in self._status.items()},
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                )
                temporary = self.status_path.with_suffix(self.status_path.suffix + ".tmp")
                temporary.write_text(payload, encoding="utf-8")
                temporary.replace(self.status_path)
        return profile
