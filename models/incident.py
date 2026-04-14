"""
ALICE Brain — Modèles Incident.
Investigation, IOC et IncidentState (état global LangGraph).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from models.alert import Alert
from models.action import Action
from models.response_plan import ResponsePlan


class IOC(BaseModel):
    type: str       # ip | hash | domain
    value: str
    context: str = ""


class Investigation(BaseModel):
    alert: Alert
    enrichment: dict[str, Any] = {}
    narrative: str = ""
    mitre_ttps: list[str] = []
    iocs: list[IOC] = []
    confidence: str = "MEDIUM"          # HIGH | MEDIUM | LOW
    risk_score: float = 0.0             # 0–100
    next_likely_action: str = ""


class IncidentState(BaseModel):
    """Représentation Pydantic d'un incident complet (miroir du TypedDict LangGraph)."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    alert: Alert | None = None
    investigation: Investigation | None = None
    response_plan: ResponsePlan | None = None
    actions_pending: list[Action] = []
    actions_executed: list[Action] = []
    status: str = "detecting"
    timeline: list[dict[str, Any]] = []
    report: str | None = None
    error: str | None = None
