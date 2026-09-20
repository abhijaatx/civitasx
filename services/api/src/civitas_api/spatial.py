"""Optional PostGIS-backed jurisdiction and civic-asset resolution.

The local JSON registries remain the offline fallback. Production deployments
can load the same records into PostGIS and resolve a resident's coordinates
against authoritative polygons instead of relying on landmark aliases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    import psycopg
except ImportError:  # pragma: no cover - minimal install fallback
    psycopg = None  # type: ignore[assignment]


POSTGIS_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS civic_jurisdictions (
    id text PRIMARY KEY,
    kind text NOT NULL,
    name text NOT NULL,
    authority_id text,
    contact_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    boundary geometry(MultiPolygon, 4326),
    point geometry(Point, 4326),
    valid_from timestamptz,
    valid_until timestamptz,
    content_hash text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS civic_jurisdictions_boundary_gix
    ON civic_jurisdictions USING gist (boundary);
CREATE INDEX IF NOT EXISTS civic_jurisdictions_point_gix
    ON civic_jurisdictions USING gist (point);
"""


@dataclass(frozen=True)
class SpatialMatch:
    record_id: str
    name: str
    kind: str
    authority_id: str | None
    contact: dict[str, Any]
    source: dict[str, Any]


class PostGISJurisdictionStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def ensure_schema(self) -> None:
        if psycopg is None:
            raise RuntimeError("Install the architecture extra to use PostGIS")
        with psycopg.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(POSTGIS_SCHEMA_SQL)
            connection.commit()

    def resolve_point(
        self,
        latitude: float,
        longitude: float,
        *,
        kind: str | None = None,
    ) -> list[SpatialMatch]:
        if psycopg is None:
            raise RuntimeError("Install the architecture extra to use PostGIS")
        kind_filter = "AND kind = %(kind)s" if kind else ""
        query = f"""
            SELECT id, name, kind, authority_id, contact_json, source_json
            FROM civic_jurisdictions
            WHERE boundary IS NOT NULL
              AND ST_Contains(boundary, ST_SetSRID(ST_Point(%(longitude)s, %(latitude)s), 4326))
              {kind_filter}
            ORDER BY updated_at DESC
        """
        params: dict[str, Any] = {"latitude": latitude, "longitude": longitude}
        if kind:
            params["kind"] = kind
        with psycopg.connect(self.dsn) as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            SpatialMatch(
                record_id=str(row[0]),
                name=str(row[1]),
                kind=str(row[2]),
                authority_id=str(row[3]) if row[3] else None,
                contact=dict(row[4] or {}),
                source=dict(row[5] or {}),
            )
            for row in rows
        ]
