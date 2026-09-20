"""Structured, source-backed police-station routing for Bengaluru.

This resolver intentionally does not use the general civic-document search index.
Operational routing needs records with station identity, contact details,
location aliases, provenance, and an explicit uncertainty path. The registry is
kept as data so it can be refreshed independently of the language model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    normalized = " ".join(_NORMALIZE_RE.sub(" ", text.casefold()).split())
    # Treat natural-language possessives such as "Microsoft's office" like
    # the registry phrase "Microsoft office".
    return re.sub(r"\b([a-z0-9]+) s\b", r"\1", normalized)


@dataclass(frozen=True)
class PoliceStationRecord:
    station_id: str
    name: str
    address: str
    locality: str
    division: str
    phone: str | None
    mobile: str | None
    aliases: tuple[str, ...]
    source_urls: tuple[str, ...]
    source_title: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PoliceStationRecord:
        return cls(
            station_id=str(raw["station_id"]),
            name=str(raw["name"]),
            address=str(raw["address"]),
            locality=str(raw["locality"]),
            division=str(raw.get("division") or "Unknown"),
            phone=str(raw["phone"]) if raw.get("phone") else None,
            mobile=str(raw["mobile"]) if raw.get("mobile") else None,
            aliases=tuple(str(alias) for alias in raw.get("aliases", [])),
            source_urls=tuple(str(url) for url in raw.get("source_urls", [])),
            source_title=str(raw.get("source_title") or "Police station directory"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "station_id": self.station_id,
            "name": self.name,
            "address": self.address,
            "locality": self.locality,
            "division": self.division,
            "phone": self.phone,
            "mobile": self.mobile,
            "source_urls": list(self.source_urls),
            "source_title": self.source_title,
        }


class PoliceStationRegistry:
    """Resolve a free-form Bengaluru location to ranked station records."""

    def __init__(self, path: str | Path | None = None):
        registry_path = Path(path) if path else self.default_path()
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
        self.dataset_id = str(raw.get("dataset_id") or "bengaluru-police-station-routing")
        self.title = str(raw.get("title") or "Bengaluru police-station routing records")
        self.authority = str(raw.get("authority") or "Bengaluru City Police")
        self.retrieved_at = str(raw.get("retrieved_at") or "")
        self.policy = dict(raw.get("policy") or {})
        self.stations = tuple(
            PoliceStationRecord.from_dict(item)
            for item in raw.get("stations", [])
            if isinstance(item, dict)
        )

    @staticmethod
    def default_path() -> Path:
        return Path(__file__).resolve().parents[2] / "data" / "police" / "bengaluru_stations.json"

    def _score(self, query: str, station: PoliceStationRecord) -> tuple[float, list[str]]:
        normalized = _normalize(query)
        matches: list[str] = []
        best = 0.0
        for alias in station.aliases:
            normalized_alias = _normalize(alias)
            if not normalized_alias:
                continue
            if normalized_alias in normalized:
                matches.append(alias)
                # Longer, specific landmarks outrank broad localities.
                score = min(0.98, 0.64 + (len(normalized_alias.split()) * 0.07))
                best = max(best, score)
        return best, matches

    def resolve(self, location_text: str) -> dict[str, Any]:
        query = " ".join(str(location_text).split()).strip()
        ranked: list[tuple[float, PoliceStationRecord, list[str]]] = []
        for station in self.stations:
            score, matches = self._score(query, station)
            if score:
                ranked.append((score, station, matches))
        ranked.sort(key=lambda item: (-item[0], item[1].name))

        if not ranked:
            return self._unresolved(query)

        top_score, top_station, top_matches = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        ambiguous = len(ranked) > 1 and (top_score - second_score) < 0.16
        candidates = [
            self._candidate(station, score, matches, rank=index + 1)
            for index, (score, station, matches) in enumerate(ranked[:4])
        ]
        best = candidates[0]
        best["match_explanation"] = self._explanation(top_matches, top_station)
        status = "ambiguous" if ambiguous else "ok"
        return {
            "status": status,
            "location_text": query,
            "best_match": best,
            "candidates": candidates,
            "needs_confirmation": ambiguous,
            "clarifying_question": (
                "Was the incident inside HSR Layout, or near the Microsoft campus at "
                "Prestige Ferns Galaxy on the Outer Ring Road?"
                if ambiguous
                else None
            ),
            "fallback": self._fallback(),
            "provenance": self._provenance(),
        }

    def _candidate(
        self,
        station: PoliceStationRecord,
        score: float,
        matches: list[str],
        *,
        rank: int,
    ) -> dict[str, Any]:
        return {
            **station.as_dict(),
            "rank": rank,
            "confidence": round(score, 2),
            "match_basis": matches,
        }

    @staticmethod
    def _explanation(matches: list[str], station: PoliceStationRecord) -> str:
        if not matches:
            return f"The resolved location falls in the {station.locality} station candidate."
        return f"Matched {', '.join(matches[:3])} to the {station.name} record."

    def _unresolved(self, query: str) -> dict[str, Any]:
        return {
            "status": "needs_clarification",
            "location_text": query,
            "best_match": None,
            "candidates": [],
            "needs_confirmation": True,
            "clarifying_question": (
                "What is the exact street, sector, nearby landmark, or map pin where the "
                "incident happened?"
            ),
            "fallback": self._fallback(),
            "provenance": self._provenance(),
        }

    def _fallback(self) -> dict[str, Any]:
        return {
            "guidance": self.policy.get("uncertain_location"),
            "emergency": self.policy.get("emergency"),
            "source_title": self.policy.get("source_title"),
            "source_url": self.policy.get("source_url"),
        }

    def _provenance(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "title": self.title,
            "authority": self.authority,
            "retrieved_at": self.retrieved_at,
            "record_count": len(self.stations),
        }
