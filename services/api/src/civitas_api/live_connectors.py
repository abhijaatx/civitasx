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
from urllib.parse import urljoin, urlparse

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
        "https://gba.karnataka.gov.in/",
        priority="P0",
        capabilities=(
            "current_officials",
            "ward_lookup",
            "document_index",
            "refresh",
        ),
        docs_url="https://gba.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "gba-ward-lookup",
        "gba",
        "Greater Bengaluru Authority",
        "GBA / BBMP current ward and city-corporation lookup",
        "https://www.bbmp.gov.in/KnowYourNewCorporation/",
        priority="P0",
        capabilities=("ward_lookup", "current_corporations", "refresh"),
        docs_url="https://www.bbmp.gov.in/KnowYourNewCorporation/",
    ),
    LiveEndpointSpec(
        "gba-services-legacy",
        "gba",
        "Greater Bengaluru Authority",
        "GBA public service portal",
        "https://bbmp.gov.in/",
        priority="P1",
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
        "bbmp-property-tax-portal",
        "gba",
        "Greater Bengaluru Authority",
        "BBMP property tax portal",
        "https://bbmptax.karnataka.gov.in/login.aspx",
        priority="P0",
        access_mode="browser_only",
        capabilities=(
            "property_tax_lookup",
            "tax_demand",
            "payment_status",
            "receipt_route",
            "refresh",
        ),
        docs_url="https://bbmptax.karnataka.gov.in/login.aspx",
    ),
    LiveEndpointSpec(
        "bbmp-property-tax-officers",
        "gba",
        "Greater Bengaluru Authority",
        "BBMP property-tax officer and jurisdiction directory",
        "https://bbmptax.karnataka.gov.in/officialsdetails.aspx",
        priority="P1",
        capabilities=("officer_directory", "jurisdiction_lookup", "refresh"),
        docs_url="https://bbmptax.karnataka.gov.in/officialsdetails.aspx",
    ),
    LiveEndpointSpec(
        "bbmp-property-tax-auctions",
        "gba",
        "Greater Bengaluru Authority",
        "BBMP property-tax proclamation and auction notices",
        "https://bbmptax.karnataka.gov.in/Forms/Proclamation_AuctionList.aspx",
        priority="P1",
        capabilities=("auction_notices", "tax_default_notices", "refresh"),
        docs_url="https://bbmptax.karnataka.gov.in/Forms/Proclamation_AuctionList.aspx",
    ),
    LiveEndpointSpec(
        "bbmp-eaasthi-citizen",
        "gba",
        "Greater Bengaluru Authority",
        "BBMP e-Aasthi citizen eKhata portal",
        "https://bbmpeaasthi.karnataka.gov.in/citizen_core/",
        priority="P0",
        access_mode="browser_only",
        capabilities=("ekhata_search", "property_records", "ward_lookup", "refresh"),
        docs_url="https://bbmpeaasthi.karnataka.gov.in/citizen_core/",
    ),
    LiveEndpointSpec(
        "bbmp-eaasthi-ward-data",
        "gba",
        "Greater Bengaluru Authority",
        "BBMP e-Aasthi public ward boundary data",
        "https://bbmpeaasthi.karnataka.gov.in/citizen_core/data/ward_boundaries.json",
        transport="json",
        priority="P0",
        capabilities=("ward_boundaries", "ward_statistics", "refresh"),
        docs_url="https://bbmpeaasthi.karnataka.gov.in/citizen_core/",
    ),
    LiveEndpointSpec(
        "bengaluru-gis-viewer",
        "gba",
        "Greater Bengaluru Authority",
        "Bengaluru GIS viewer",
        "https://www.bbmp.gov.in/gisviewer/",
        priority="P1",
        access_mode="browser_only",
        capabilities=("location_details", "ward_map", "civic_asset_map", "refresh"),
        docs_url="https://www.bbmp.gov.in/gisviewer/",
    ),
    LiveEndpointSpec(
        "bbmp-court-case-citizen",
        "gba",
        "Greater Bengaluru Authority",
        "BBMP court case monitoring citizen portal",
        "https://bbmpenyaya.karnataka.gov.in/Citizen/CitizenLogin.aspx",
        priority="P2",
        access_mode="browser_only",
        capabilities=("case_lookup", "case_status", "court_document_route", "refresh"),
        docs_url="https://bbmpenyaya.karnataka.gov.in/Citizen/CitizenLogin.aspx",
    ),
    LiveEndpointSpec(
        "bengaluru-open-data-gba",
        "gba",
        "Greater Bengaluru Authority",
        "GBA official public records index",
        "https://gba.karnataka.gov.in/",
        priority="P0",
        capabilities=("current_officials", "ward_lookup", "document_index", "refresh"),
    ),
    LiveEndpointSpec(
        "bengaluru-open-data-bmrcl",
        "bmrcl",
        "Bangalore Metro Rail Corporation Limited",
        "BMRCL official public records index",
        "https://english.bmrc.co.in/",
        priority="P1",
        capabilities=("document_index", "refresh"),
    ),
    LiveEndpointSpec(
        "bengaluru-open-data-bwssb",
        "bwssb",
        "Bangalore Water Supply and Sewerage Board",
        "BWSSB official public records index",
        "https://bwssb.karnataka.gov.in/en",
        priority="P1",
        capabilities=("document_index", "refresh"),
    ),
    LiveEndpointSpec(
        "bengaluru-open-data-bmrcl-phase-2a",
        "bmrcl",
        "Bangalore Metro Rail Corporation Limited",
        "Legacy BMRCL Phase 2A source — verification required",
        "https://english.bmrc.co.in/",
        access_mode="browser_only",
        capabilities=("document_index",),
        refreshable=False,
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
        capabilities=("discover", "query_public"),
        docs_url="https://data.gov.in/help/apis",
        # The API requires a publisher resource ID.  The base `/resource`
        # route is not a meaningful probe and returns 404 by design; callers
        # use `/api/live/open-data/search` with a concrete resource ID.
        refreshable=False,
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
        "karnataka-ipgrs-grievances",
        "karnataka",
        "Government of Karnataka",
        "Karnataka iPGRS grievance portal",
        "https://ipgrs.karnataka.gov.in/",
        priority="P0",
        access_mode="browser_only",
        capabilities=("discover_services", "prepare_grievance", "track_grievance", "refresh"),
        docs_url="https://ipgrs.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "nadakacheri-ajsk-portal",
        "karnataka",
        "Government of Karnataka",
        "Nadakacheri AJSK certificate services",
        "https://nadakacheri.karnataka.gov.in/ajsk",
        priority="P0",
        access_mode="browser_only",
        capabilities=(
            "income_certificate",
            "caste_certificate",
            "residence_certificate",
            "service_catalog",
            "refresh",
        ),
        docs_url="https://nadakacheri.karnataka.gov.in/ajsk",
    ),
    LiveEndpointSpec(
        "nadakacheri-application-status",
        "karnataka",
        "Government of Karnataka",
        "Nadakacheri application status",
        "https://ajsk.karnataka.gov.in/NK_Status",
        priority="P0",
        access_mode="browser_only",
        capabilities=("application_status", "certificate_status", "refresh"),
        docs_url="https://nadakacheri.karnataka.gov.in/ajsk",
    ),
    LiveEndpointSpec(
        "sakala-service-portal",
        "karnataka",
        "Government of Karnataka",
        "Karnataka Sakala service-guarantee portal",
        "https://sakala.kar.nic.in/Onlineservices_kan.aspx",
        priority="P0",
        capabilities=("service_catalog", "service_deadlines", "application_status", "refresh"),
        docs_url="https://sakala.kar.nic.in/",
    ),
    LiveEndpointSpec(
        "sakala-monthly-reports",
        "karnataka",
        "Government of Karnataka",
        "Karnataka Sakala public monthly reports",
        "https://sakala.kar.nic.in/monthly_report_kan.aspx",
        priority="P1",
        capabilities=("service_performance_reports", "sakala_statistics", "refresh"),
        docs_url="https://sakala.kar.nic.in/",
    ),
    LiveEndpointSpec(
        "karnataka-one-services",
        "karnataka",
        "Government of Karnataka",
        "KarnatakaOne citizen service catalogue",
        "https://www.karnatakaone.gov.in/Public/Services",
        priority="P0",
        access_mode="browser_only",
        capabilities=(
            "service_catalog",
            "service_routes",
            "receipt_lookup",
            "center_locator",
            "refresh",
        ),
        docs_url="https://www.karnatakaone.gov.in/",
    ),
    LiveEndpointSpec(
        "ahara-pds-services",
        "ahara",
        "Government of Karnataka",
        "Ahara Karnataka food and ration-card services",
        "https://ahara.karnataka.gov.in/Home/EServices",
        priority="P0",
        access_mode="browser_only",
        capabilities=(
            "ration_card_services",
            "application_route",
            "status_lookup",
            "grievance_route",
            "refresh",
        ),
        docs_url="https://ahara.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "ahara-pds-ration-statistics",
        "ahara",
        "Government of Karnataka",
        "Ahara ration-card and PDS statistics",
        "https://ahara.karnataka.gov.in/fcs_office_statistics/Stat_AAY_APL_BPL_Details.aspx",
        priority="P1",
        capabilities=("ration_card_statistics", "pds_statistics", "refresh"),
        docs_url="https://ahara.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "ahara-pds-distribution-statistics",
        "ahara",
        "Government of Karnataka",
        "Ahara ration distribution statistics",
        "https://ahara.karnataka.gov.in/fcs_office_statistics/stat_ration_taken_details.aspx",
        priority="P1",
        capabilities=("ration_distribution_statistics", "pds_statistics", "refresh"),
        docs_url="https://ahara.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "ejanma-certificate-search",
        "ejanma",
        "Government of Karnataka",
        "eJanMa birth and death certificate verification",
        "https://ejanma.karnataka.gov.in/frmBirthDeathSearch.aspx",
        priority="P0",
        access_mode="browser_only",
        capabilities=("birth_certificate_verify", "death_certificate_verify", "refresh"),
        docs_url="https://ejanma.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "ejanma-application-status",
        "ejanma",
        "Government of Karnataka",
        "eJanMa birth/death application status",
        "https://ejanma.karnataka.gov.in/frmApplicationStatus.aspx",
        priority="P1",
        access_mode="browser_only",
        capabilities=("application_status", "refresh"),
        docs_url="https://ejanma.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "ejanma-vital-statistics",
        "ejanma",
        "Government of Karnataka",
        "eJanMa vital statistics and registration counts",
        "https://ejanma.karnataka.gov.in/frmBirthDeathcount.aspx",
        priority="P1",
        capabilities=("vital_statistics", "registration_counts", "refresh"),
        docs_url="https://ejanma.karnataka.gov.in/",
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
        "karnataka-rtc-services",
        "bhoomi",
        "Government of Karnataka",
        "Karnataka RTC citizen services",
        "https://rtc.karnataka.gov.in/Service78/",
        priority="P1",
        access_mode="browser_only",
        capabilities=("rtc_lookup", "mutation_route", "land_record_service", "refresh"),
        docs_url="https://rtc.karnataka.gov.in/Service78/",
    ),
    LiveEndpointSpec(
        "karnataka-rtc-citizen",
        "bhoomi",
        "Government of Karnataka",
        "Karnataka RTC citizen application",
        "https://rtc.karnataka.gov.in/Service78/RTC.aspx",
        priority="P1",
        access_mode="browser_only",
        capabilities=("rtc_lookup", "mutation_lookup", "refresh"),
        docs_url="https://rtc.karnataka.gov.in/Service78/",
    ),
    LiveEndpointSpec(
        "mojini-survey",
        "mojini",
        "Government of Karnataka",
        "Mojini land survey services",
        "https://bhoomojini.karnataka.gov.in/",
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
        "ksrtc-portal",
        "ksrtc",
        "Karnataka State Road Transport Corporation",
        "KSRTC public transport portal",
        "https://ksrtc.in/",
        priority="P1",
        access_mode="browser_only",
        capabilities=("bus_service_routes", "ticket_route", "service_notices", "refresh"),
        docs_url="https://ksrtc.in/",
    ),
    LiveEndpointSpec(
        "ksrtc-booking-enquiry",
        "ksrtc",
        "Karnataka State Road Transport Corporation",
        "KSRTC booking enquiry",
        "https://ksrtc.in/booking-enquiry",
        priority="P1",
        access_mode="browser_only",
        capabilities=("booking_status", "ticket_enquiry", "refresh"),
        docs_url="https://ksrtc.in/",
    ),
    LiveEndpointSpec(
        "bwssb-water-services",
        "bwssb",
        "Bangalore Water Supply and Sewerage Board",
        "BWSSB water complaints and services",
        "https://owcv2.bwssb.gov.in/consumer",
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
        "bescom-complaint-tracker",
        "bescom",
        "Bangalore Electricity Supply Company",
        "BESCOM complaint tracker",
        "https://www.bescom.co.in/bescom/dashboard/consumer-dashboard/track-complaints",
        priority="P1",
        access_mode="browser_only",
        capabilities=("complaint_status", "billing_complaint_status", "refresh"),
        docs_url="https://www.bescom.co.in/",
    ),
    LiveEndpointSpec(
        "bescom-service-dashboard",
        "bescom",
        "Bangalore Electricity Supply Company",
        "BESCOM consumer service dashboard",
        "https://www.bescom.co.in/bescom/dashboard/consumer-dashboard/my-services",
        priority="P1",
        access_mode="browser_only",
        capabilities=(
            "service_status",
            "bill_statement_route",
            "connection_service_route",
            "refresh",
        ),
        docs_url="https://www.bescom.co.in/",
    ),
    LiveEndpointSpec(
        "bengaluru-city-police",
        "bengaluru-police",
        "Bengaluru City Police",
        "Bengaluru City Police citizen portal",
        "https://bcp.karnataka.gov.in/en",
        priority="P1",
        capabilities=("police_notices", "citizen_service_routes", "public_information", "refresh"),
        docs_url="https://bcp.karnataka.gov.in/en",
    ),
    LiveEndpointSpec(
        "karnataka-police-e-lost-reports",
        "karnataka-police",
        "Karnataka State Police",
        "Karnataka Police e-Lost reports",
        "https://kspapp.ksp.gov.in/ksp/api/elost-reports",
        priority="P1",
        access_mode="browser_only",
        capabilities=("lost_report_lookup", "lost_document_route", "refresh"),
        docs_url="https://bcp.karnataka.gov.in/en",
    ),
    LiveEndpointSpec(
        "karnataka-land-resource-inventory",
        "karnataka",
        "Government of Karnataka",
        "Karnataka Sujala land-resource inventory portal",
        "https://sujala3lri.karnataka.gov.in/",
        priority="P2",
        access_mode="browser_only",
        capabilities=("land_resource_inventory", "watershed_information", "refresh"),
        docs_url="https://sujala3lri.karnataka.gov.in/",
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
        "karnataka-professional-tax",
        "karnataka",
        "Government of Karnataka",
        "Karnataka professional tax portal",
        "https://ptax.karnataka.gov.in/",
        priority="P1",
        access_mode="browser_only",
        capabilities=("professional_tax_services", "enrolment_route", "payment_route", "refresh"),
        docs_url="https://ptax.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "k-gis-portal",
        "karnataka",
        "Government of Karnataka",
        "Karnataka GIS portal",
        "https://kgis.ksrsac.in/kgis/",
        priority="P0",
        capabilities=("gis_catalog", "metadata", "web_api_discovery", "refresh"),
        docs_url="https://kgis.ksrsac.in/kgis/aboutkgis.aspx",
    ),
    LiveEndpointSpec(
        "k-gis-bda-map-metadata",
        "bda",
        "Bangalore Development Authority",
        "K-GIS BDA spatial layer metadata",
        "https://kgis.ksrsac.in/kgismaps2/rest/services/BDA/BDA/MapServer?f=pjson",
        transport="json",
        priority="P0",
        capabilities=("bda_spatial_layers", "layer_metadata", "refresh"),
        docs_url="https://kgis.ksrsac.in/kgis/",
    ),
    LiveEndpointSpec(
        "k-gis-bda-layout-layer",
        "bda",
        "Bangalore Development Authority",
        "K-GIS BDA layout-boundary layer metadata",
        "https://kgis.ksrsac.in/kgismaps2/rest/services/BDA/BDA/MapServer/9?f=pjson",
        transport="json",
        priority="P0",
        capabilities=("layout_boundaries", "layer_fields", "refresh"),
        docs_url="https://kgis.ksrsac.in/kgis/",
    ),
    LiveEndpointSpec(
        "k-gis-watershed-wms",
        "karnataka",
        "Government of Karnataka",
        "K-GIS watershed WMS capabilities",
        "https://kgis.ksrsac.in/kgismaps1/services/NR_V2/Watershed/MapServer/WMSServer?request=GetCapabilities&service=WMS",
        priority="P1",
        capabilities=("watershed_layers", "map_capabilities", "refresh"),
        docs_url="https://kgis.ksrsac.in/kgis/",
    ),
    LiveEndpointSpec(
        "rera-karnataka-portal",
        "rera-karnataka",
        "Karnataka Real Estate Regulatory Authority",
        "Karnataka RERA public portal",
        "https://rera.karnataka.gov.in/",
        priority="P1",
        capabilities=("project_search", "agent_search", "complaint_search", "orders", "refresh"),
        docs_url="https://rera.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "rera-karnataka-projects",
        "rera-karnataka",
        "Karnataka Real Estate Regulatory Authority",
        "Karnataka RERA registered projects",
        "https://rera.karnataka.gov.in/viewAllProjects",
        priority="P1",
        capabilities=("project_search", "registered_project_records", "refresh"),
        docs_url="https://rera.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "rera-karnataka-complaints",
        "rera-karnataka",
        "Karnataka Real Estate Regulatory Authority",
        "Karnataka RERA complaints and orders",
        "https://rera.karnataka.gov.in/viewAllComplaints",
        priority="P1",
        capabilities=("complaint_search", "complaint_status", "orders", "refresh"),
        docs_url="https://rera.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "rera-karnataka-cause-list",
        "rera-karnataka",
        "Karnataka Real Estate Regulatory Authority",
        "Karnataka RERA project cause list",
        "https://rera.karnataka.gov.in/projectDailyCauseList",
        priority="P2",
        capabilities=("cause_list", "hearing_information", "refresh"),
        docs_url="https://rera.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "kspcb-environment-portal",
        "kspcb",
        "Karnataka State Pollution Control Board",
        "KSPCB environmental portal",
        "https://kspcb.karnataka.gov.in/",
        priority="P1",
        capabilities=("environmental_notices", "consent_guidance", "reports", "refresh"),
        docs_url="https://kspcb.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "kspcb-e-citizen",
        "kspcb",
        "Karnataka State Pollution Control Board",
        "KSPCB e-citizen information",
        "https://kspcb.karnataka.gov.in/index.php/e-citizen",
        priority="P1",
        capabilities=("public_complaint_route", "environmental_services", "refresh"),
        docs_url="https://kspcb.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "karnataka-housing-board",
        "khb",
        "Karnataka Housing Board",
        "Karnataka Housing Board schemes and public notices",
        "https://khb.karnataka.gov.in/",
        priority="P1",
        capabilities=("housing_schemes", "public_notices", "application_routes", "refresh"),
        docs_url="https://khb.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "ceo-karnataka-portal",
        "karnataka",
        "Chief Electoral Officer, Karnataka",
        "Chief Electoral Officer Karnataka portal",
        "https://ceo.karnataka.gov.in/",
        priority="P1",
        capabilities=("electoral_notices", "roll_download_route", "polling_information", "refresh"),
        docs_url="https://ceo.karnataka.gov.in/",
    ),
    LiveEndpointSpec(
        "eci-karnataka-electoral-roll",
        "election-commission",
        "Election Commission of India",
        "Karnataka electoral-roll download route",
        "https://voters.eci.gov.in/download-eroll?stateCode=S10",
        priority="P1",
        access_mode="browser_only",
        capabilities=("electoral_roll_download", "state_roll_selection", "refresh"),
        docs_url="https://voters.eci.gov.in/download-eroll?stateCode=S10",
    ),
    LiveEndpointSpec(
        "eci-elector-search",
        "election-commission",
        "Election Commission of India",
        "ECI elector search portal",
        "https://electoralsearch.eci.gov.in/",
        priority="P1",
        access_mode="browser_only",
        capabilities=("elector_search", "polling_station_route", "refresh"),
        docs_url="https://electoralsearch.eci.gov.in/",
    ),
    LiveEndpointSpec(
        "karsec-local-elections",
        "karsec",
        "State Election Commission, Karnataka",
        "Karnataka local-body election portal",
        "https://karsec.karnataka.gov.in/",
        priority="P1",
        capabilities=("local_election_notices", "results_route", "roll_route", "refresh"),
        docs_url="https://karsec.karnataka.gov.in/",
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
        "https://eproc.karnataka.gov.in/eprocportal/pages/index.jsp",
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
        "https://ncs.gov.in/",
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
        "gba.karnataka.gov.in",
        "english.bmrc.co.in",
        "eng.bdabangalore.org",
        "bdakarnataka.in",
        "kbda.karnataka.gov.in",
        "bbmptax.karnataka.gov.in",
        "bbmpeaasthi.karnataka.gov.in",
        "bbmpenyaya.karnataka.gov.in",
        "kgis.ksrsac.in",
        "karnataka.data.gov.in",
        "api.data.gov.in",
        "docs.apisetu.gov.in",
        "apisetu.gov.in",
        "sevasindhu.karnataka.gov.in",
        "ipgrs.karnataka.gov.in",
        "www.karnatakaone.gov.in",
        "ahara.karnataka.gov.in",
        "ejanma.karnataka.gov.in",
        "www.pgportal.gov.in",
        "pgportal.gov.in",
        "parivahan.gov.in",
        "sarathi.parivahan.gov.in",
        "echallan.parivahan.gov.in",
        "landrecords.karnataka.gov.in",
        "rtc.karnataka.gov.in",
        "kaveri.karnataka.gov.in",
        "eswathu.karnataka.gov.in",
        "bhoomojini.karnataka.gov.in",
        "services.ecourts.gov.in",
        "bmtc.co.in",
        "majestic.bmtc.co.in",
        "ksrtc.in",
        "bwssb.gov.in",
        "www.bwssb.gov.in",
        "bwssb.karnataka.gov.in",
        "owcv2.bwssb.gov.in",
        "bescom.karnataka.gov.in",
        "www.bescom.co.in",
        "bcp.karnataka.gov.in",
        "kspapp.ksp.gov.in",
        "sujala3lri.karnataka.gov.in",
        "www.myscheme.gov.in",
        "ncs.gov.in",
        "web.umang.gov.in",
        "www.gst.gov.in",
        "ptax.karnataka.gov.in",
        "nadakacheri.karnataka.gov.in",
        "ajsk.karnataka.gov.in",
        "sakala.kar.nic.in",
        "rera.karnataka.gov.in",
        "kspcb.karnataka.gov.in",
        "khb.karnataka.gov.in",
        "ceo.karnataka.gov.in",
        "voters.eci.gov.in",
        "electoralsearch.eci.gov.in",
        "karsec.karnataka.gov.in",
        "udyamregistration.gov.in",
        "www.mca.gov.in",
        "eproc.karnataka.gov.in",
        "abdm.gov.in",
        "abha.abdm.gov.in",
        "nad.digilocker.gov.in",
        "www.ncs.gov.in",
    }


def _fetch_allowlisted_source(
    client: httpx.Client,
    url: str,
    *,
    max_bytes: int,
) -> tuple[bytes, str]:
    """Fetch a source while allowing only bounded, allowlisted redirects.

    Government portals commonly move from a legacy hostname to a current
    official hostname.  Following arbitrary redirects would undermine the
    connector allowlist, so every redirect target is validated before another
    request is made.
    """

    current_url = url
    for _ in range(4):
        with client.stream("GET", current_url) as response:
            if 300 <= response.status_code < 400:
                location = response.headers.get("location")
                target = urljoin(current_url, location or "")
                if not _allowlisted(target):
                    raise ValueError(
                        "Redirects are disabled for live civic sources unless the target "
                        "is on the CivitasX allowlist"
                    )
                current_url = target
                continue
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"Response exceeds {max_bytes} byte safety limit")
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("content-type", "")
    raise ValueError("Live civic source returned too many redirects")


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
                key=lambda document: (
                    document.published_at is not None,
                    document.published_at
                    or document.retrieved_at
                    or datetime.min.replace(tzinfo=UTC),
                ),
                default=None,
            )
            extraction_status = latest_source.extraction_status if latest_source else "unknown"
            if previous:
                profiles.append(
                    LiveEndpointProfile.model_validate(
                        {
                            **previous.model_dump(mode="json"),
                            "authority_id": spec.authority_id,
                            "authority_name": spec.authority_name,
                            "title": spec.title,
                            "url": spec.url,
                            "source_kind": spec.source_kind,
                            "transport": spec.transport,
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
                else "requires_api_key"
                if spec.access_mode == "api_key" and not self._credential(spec.requires_env or "")
                else "unknown"
                if spec.access_mode == "api_key"
                else "browser_only"
            )
            if spec.access_mode == "api_key":
                message = (
                    "No request was sent; this connector is query-only. Set "
                    f"{spec.requires_env} and provide a resource ID through the India OGD "
                    "search route."
                )
                error = (
                    "Set the configured API key before querying this connector"
                    if status == "requires_api_key"
                    else "Query-only connector; use the resource search route with a resource ID"
                )
            else:
                if spec.access_mode == "browser_only":
                    message = (
                        "No request was sent; this source requires a browser-backed connector "
                        "and is not refreshable by the bounded HTTP reader."
                    )
                    error = "Browser-only connector is not refreshable by the bounded HTTP reader"
                else:
                    message = (
                        "No request was sent. Complete the official approval/consent flow and "
                        "configure the connector credentials first."
                    )
                    error = (
                        "This connector is metadata-only until the official approval or consent "
                        "flow is configured"
                    )
            profile = self._save_status(
                spec,
                status=status,
                checked_at=checked_at,
                error=error,
                source_ids=[],
            )
            return LiveRefreshResult(
                endpoint=profile,
                message=message,
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
                raw, content_type = _fetch_allowlisted_source(
                    client,
                    str(spec.url),
                    max_bytes=self.max_bytes,
                )
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
