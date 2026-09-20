"""Data-driven civic workflow architecture.

The language model chooses among capabilities, but capability metadata decides
which evidence, location slots, policies, and actions are required. This keeps
new resident issues data-driven instead of adding prompt-specific branches.

LangGraph is used when the optional ``architecture`` dependency group is
installed. The deterministic registry remains the local fallback so tests and
offline development do not require a graph server or external services.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypedDict

from .observability import traced

try:  # Optional architecture dependency group.
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - exercised in the minimal install
    END = START = StateGraph = None  # type: ignore[assignment]


_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    return " ".join(_NORMALIZE_RE.sub(" ", text.casefold()).split())


def _extract_location(text: str) -> str | None:
    # Questions such as “which station is nearest to <full address>?” put the
    # location after a comparison phrase rather than after “near/in/at”. Keep
    # the complete landmark/address so downstream registries can use its most
    # specific alias instead of reducing it to the first locality token.
    nearest_match = re.search(
        r"\b(?:nearest|closest)\s+(?:(?:police\s+)?station\s+)?"
        r"(?:to|near)\s+(.+?)(?:[?!]+|$)",
        text,
        re.I,
    )
    if nearest_match:
        candidate = " ".join(nearest_match.group(1).split()).strip(" .-:")
        if candidate and candidate.casefold() not in {
            "my locality",
            "the city",
            "my house",
            "my apartment",
            "my building",
        }:
            return candidate[:240]
    for match in re.finditer(
        r"\b(?:near|in|at|around|outside|beside|on)\s+([^?.!]+)", text, re.I
    ):
        candidate = " ".join(match.group(1).split()).strip()
        candidate = re.split(
            r"\b(?:after|before|with|about|where|what|which|because|during)\b",
            candidate,
            maxsplit=1,
            flags=re.I,
        )[0].strip(" .-:")
        if candidate and candidate.casefold() not in {
            "my locality",
            "the city",
            "my house",
            "my apartment",
            "my building",
            "our area",
            "our street",
            "the apartment gate",
            "the abandoned building",
            "the school",
            "the metro station",
            "the bus stop",
        }:
            return candidate[:240]
    return None


@dataclass(frozen=True)
class Capability:
    capability_id: str
    label: str
    phrases: tuple[str, ...]
    required_slots: tuple[str, ...]
    mode: str
    authority_id: str | None
    tools: tuple[str, ...]
    response_contract: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Capability:
        return cls(
            capability_id=str(raw["id"]),
            label=str(raw["label"]),
            phrases=tuple(str(item) for item in raw.get("phrases", [])),
            required_slots=tuple(str(item) for item in raw.get("required_slots", [])),
            mode=str(raw.get("mode") or "unsupported"),
            authority_id=str(raw["authority_id"]) if raw.get("authority_id") else None,
            tools=tuple(str(item) for item in raw.get("tools", [])),
            response_contract=str(raw.get("response_contract") or "answer conservatively"),
        )


@dataclass(frozen=True)
class CivicPlan:
    capability_id: str
    label: str
    confidence: float
    location: str | None
    missing_slots: tuple[str, ...]
    tools: tuple[str, ...]
    mode: str
    authority_id: str | None
    response_contract: str
    reliable_now: bool
    needs_clarification: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkflowState(TypedDict, total=False):
    text: str
    location_context: str | None
    plan: dict[str, Any]


class CapabilityRegistry:
    def __init__(self, path: str | Path | None = None):
        registry_path = Path(path) if path else self.default_path()
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
        self.version = int(raw.get("version") or 1)
        self.capabilities = tuple(
            Capability.from_dict(item)
            for item in raw.get("capabilities", [])
            if isinstance(item, dict)
        )

    @staticmethod
    def default_path() -> Path:
        return (
            Path(__file__).resolve().parents[2]
            / "data"
            / "capabilities"
            / "civic_capabilities.json"
        )

    def classify(self, text: str) -> tuple[Capability, float, list[str]]:
        normalized = _normalize(text)
        ranked: list[tuple[float, Capability, list[str]]] = []
        for capability in self.capabilities:
            matches = [phrase for phrase in capability.phrases if _normalize(phrase) in normalized]
            if not matches:
                continue
            score = min(0.99, 0.45 + max(len(_normalize(item).split()) for item in matches) * 0.08)
            if capability.mode == "deterministic":
                score += 0.04
            ranked.append((score, capability, matches))
        if not ranked:
            fallback = Capability(
                "general.civic_question",
                "General civic question",
                (),
                (),
                "unsupported",
                "gba",
                (),
                "ask for the missing civic topic or location",
            )
            return fallback, 0.15, []
        ranked.sort(key=lambda item: (item[0], len(item[2])), reverse=True)
        score, capability, matches = ranked[0]
        return capability, round(min(score, 0.99), 2), matches

    def plan(self, text: str, *, location_context: str | None = None) -> CivicPlan:
        capability, confidence, matches = self.classify(text)
        location = _extract_location(text) or location_context
        missing = tuple(
            slot
            for slot in capability.required_slots
            if (slot == "incident_location" and not location)
            or (slot == "attachment" and "upload" not in _normalize(text))
        )
        tools = capability.tools
        if "resolve_ward_and_authority" in tools and not location:
            tools = tuple(item for item in tools if item != "resolve_ward_and_authority")
        reliable = not missing and capability.mode in {"deterministic", "indexed", "draft"}
        reason = (
            f"Matched {', '.join(matches[:3])}."
            if matches
            else "No capability phrase matched; a domain-specific connector is not selected."
        )
        if missing:
            reason += " An exact incident location is required before routing."
        elif capability.mode in {"connector_required", "live"}:
            reason += " The response requires a live connector or a safe draft fallback."
        return CivicPlan(
            capability_id=capability.capability_id,
            label=capability.label,
            confidence=confidence,
            location=location,
            missing_slots=missing,
            tools=tools,
            mode=capability.mode,
            authority_id=capability.authority_id,
            response_contract=capability.response_contract,
            reliable_now=reliable,
            needs_clarification=bool(missing),
            reason=reason,
        )


class CivicWorkflow:
    """Run the capability planner through an optional LangGraph state graph."""

    def __init__(self, registry: CapabilityRegistry | None = None):
        self.registry = registry or CapabilityRegistry()
        self.graph = self._build_graph()

    def _build_graph(self) -> Any | None:
        if StateGraph is None:
            return None
        graph = StateGraph(WorkflowState)
        graph.add_node("classify_and_plan", self._classify_and_plan)
        graph.add_edge(START, "classify_and_plan")
        graph.add_edge("classify_and_plan", END)
        return graph.compile()

    def _classify_and_plan(self, state: WorkflowState) -> WorkflowState:
        plan = self.registry.plan(
            str(state.get("text") or ""),
            location_context=state.get("location_context"),
        )
        return {"plan": plan.as_dict()}

    @traced("civitas.workflow.plan")
    def plan(self, text: str, *, location_context: str | None = None) -> CivicPlan:
        if self.graph is None:
            return self.registry.plan(text, location_context=location_context)
        result = self.graph.invoke({"text": text, "location_context": location_context})
        return CivicPlan(**result["plan"])
