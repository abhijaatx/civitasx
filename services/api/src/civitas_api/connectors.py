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


def list_connectors(index: ResearchIndex, query: str | None = None) -> list[ConnectorProfile]:
    profiles = []
    verified_at = datetime.now(UTC)
    for authority_id, authority in index.authority_records.items():
        if query:
            haystack = " ".join(
                [authority.name, authority.short_name, *authority.responsibilities]
            ).casefold()
            if query.casefold() not in haystack:
                continue
        profiles.append(
            ConnectorProfile(
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
        )
    return profiles


def get_connector(index: ResearchIndex, authority_id: str) -> ConnectorProfile | None:
    profile = next(
        (profile for profile in list_connectors(index) if profile.authority_id == authority_id),
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
