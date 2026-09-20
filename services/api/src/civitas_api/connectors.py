"""Authority connector registry for the local-first complaint workflow.

The registry is deliberately declarative. It lets the Agent explain which
authority and fields would be needed without pretending that a local fixture
has submitted anything to a government portal. The same profile is the seam a
future official API or bounded browser connector will implement.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .models import ConnectorField, ConnectorProfile
from .research import ResearchIndex

COMMON_FIELDS = [
    ConnectorField(
        key="description",
        label="What happened",
        required=True,
        input_type="textarea",
        description="A factual description of the issue and when it was observed.",
    ),
    ConnectorField(
        key="locality",
        label="Locality or landmark",
        required=True,
        input_type="location",
        description="A coarse locality or nearby landmark; do not use an exact home address.",
    ),
    ConnectorField(
        key="evidence",
        label="Evidence files",
        required=False,
        input_type="file",
        description="Photos, notices, or receipts that support the description.",
    ),
]

SUPPLEMENTAL_CONNECTORS = {
    "bwssb": (
        "Bangalore Water Supply and Sewerage Board",
        "https://bwssb.gov.in/",
        "Water supply, sewerage, and service complaints",
    ),
    "bescom": (
        "Bangalore Electricity Supply Company",
        "https://bescom.karnataka.gov.in/",
        "Electricity outages, billing, and service complaints",
    ),
}


def _official_api_profile(
    index: ResearchIndex,
    authority_id: str,
    intake_url: str,
) -> ConnectorProfile:
    authority = index.authority_records.get(authority_id)
    authority_name = authority.name if authority else authority_id
    return ConnectorProfile(
        authority_id=authority_id,
        name=f"{authority_name} via certified official API",
        contact_route=(
            "Certified official API connector. The endpoint and credential are deployment "
            "managed; no browser or CAPTCHA bypass is used."
        ),
        intake_url=intake_url,
        provider="official_api",
        capabilities=["lookup", "prepare", "submit"],
        submission_enabled=True,
        verified_at=datetime.now(UTC),
        requirements=list(COMMON_FIELDS),
    )


def list_connectors(
    index: ResearchIndex,
    query: str | None = None,
    *,
    ipgrs_enabled: bool = False,
    ipgrs_url: str = "https://ipgrs.karnataka.gov.in/Citizens/GrievanceSelfService",
    official_api_authority_id: str = "gba",
    official_api_configured: bool = False,
    official_api_url: str | None = None,
) -> list[ConnectorProfile]:
    profiles = []
    verified_at = datetime.now(UTC)
    for authority_id, authority in index.authority_records.items():
        if query:
            haystack = " ".join(
                [authority.name, authority.short_name, *authority.responsibilities]
            ).casefold()
            if query.casefold() not in haystack:
                continue
        profile = ConnectorProfile(
                authority_id=authority_id,
                name=authority.name,
                contact_route=authority.contact_route,
                intake_url=authority.url,
                provider="local_fixture",
                capabilities=["lookup", "prepare"],
                submission_enabled=False,
                verified_at=authority.verified_at or verified_at,
                requirements=list(COMMON_FIELDS),
            )
        if authority_id == official_api_authority_id and official_api_configured:
            profile = _official_api_profile(
                index,
                authority_id,
                official_api_url or authority.url,
            )
        elif authority_id == "gba" and ipgrs_enabled:
            profile = get_connector(
                index,
                authority_id,
                ipgrs_enabled=True,
                ipgrs_url=ipgrs_url,
                official_api_authority_id=official_api_authority_id,
                official_api_configured=official_api_configured,
                official_api_url=official_api_url,
            ) or profile
        profiles.append(profile)
    return profiles


def get_connector(
    index: ResearchIndex,
    authority_id: str,
    *,
    ipgrs_enabled: bool = False,
    ipgrs_url: str = "https://ipgrs.karnataka.gov.in/Citizens/GrievanceSelfService",
    official_api_authority_id: str = "gba",
    official_api_configured: bool = False,
    official_api_url: str | None = None,
) -> ConnectorProfile | None:
    if authority_id == official_api_authority_id and official_api_configured:
        authority = index.authority_records.get(authority_id)
        if authority is None and authority_id not in SUPPLEMENTAL_CONNECTORS:
            return None
        return _official_api_profile(
            index,
            authority_id,
            official_api_url
            or (authority.url if authority else SUPPLEMENTAL_CONNECTORS[authority_id][1]),
        )
    if authority_id == "gba" and ipgrs_enabled:
        return ConnectorProfile(
            authority_id="gba",
            name="Greater Bengaluru Authority via Karnataka iPGRS",
            contact_route="Karnataka Praja Seve iPGRS browser connector",
            intake_url=ipgrs_url,
            provider="browser",
            capabilities=["lookup", "prepare", "submit"],
            submission_enabled=True,
            verified_at=datetime.now(UTC),
            requirements=[
                *COMMON_FIELDS,
                ConnectorField(
                    key="district",
                    label="District code or name",
                    required=True,
                    input_type="identifier",
                    description="The iPGRS district selector value for the incident.",
                ),
                ConnectorField(
                    key="taluk",
                    label="Taluk code or name",
                    required=True,
                    input_type="identifier",
                    description="The iPGRS taluk selector value for the incident.",
                ),
                ConnectorField(
                    key="address",
                    label="Exact incident address",
                    required=True,
                    input_type="location",
                description=(
                    "The address entered in the official form; confirm it before filing."
                ),
                ),
                ConnectorField(
                    key="pincode",
                    label="PIN code",
                    required=True,
                    input_type="identifier",
                    description="The six-digit PIN code for the incident location.",
                ),
                ConnectorField(
                    key="mobile",
                    label="Resident mobile number",
                    required=True,
                    input_type="identifier",
                    description="The mobile number used for the official OTP challenge.",
                ),
                ConnectorField(
                    key="summary_id",
                    label="iPGRS service route",
                    required=False,
                    input_type="identifier",
                    description=(
                        "Optional service summary ID; the resident can choose it in the portal."
                    ),
                ),
            ],
        )
    profile = next(
        (
            profile
            for profile in list_connectors(
                index,
                ipgrs_enabled=ipgrs_enabled,
                ipgrs_url=ipgrs_url,
                official_api_authority_id=official_api_authority_id,
                official_api_configured=official_api_configured,
                official_api_url=official_api_url,
            )
            if profile.authority_id == authority_id
        ),
        None,
    )
    if profile:
        return profile
    supplemental = SUPPLEMENTAL_CONNECTORS.get(authority_id)
    if not supplemental:
        return None
    name, url, responsibility = supplemental
    return ConnectorProfile(
        authority_id=authority_id,
        name=name,
        contact_route=(
            f"Use the verified {authority_id.upper()} service route; confirm the current "
            "intake portal before sending."
        ),
        intake_url=url,
        provider="local_fixture",
        capabilities=["lookup", "prepare"],
        submission_enabled=False,
        verified_at=datetime.now(UTC),
        requirements=[
            *COMMON_FIELDS,
            ConnectorField(
                key="responsibility",
                label=responsibility,
                required=False,
                input_type="text",
                description=responsibility,
            ),
        ],
    )
