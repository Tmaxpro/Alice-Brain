"""
ALICE Brain — Modèle ResponsePlan.
Plan de réponse structuré PICERL généré par le Response Planner Agent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from models.action import Action


class ResponsePlan(BaseModel):
    incident_id: str
    severity: str
    summary: str = ""
    phases: dict[str, list[str]] = {}       # PICERL phases → listes de descriptions textuelles
    actions_auto: list[Action] = []
    actions_critical: list[Action] = []
    estimated_impact: str = ""
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
