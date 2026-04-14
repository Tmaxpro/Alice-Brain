"""
ALICE Brain — Modèle Alert.
Représente une alerte de sécurité détectée par le Detection Agent.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Alert(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str                           # brute_force_ssh | port_scan | off_hours_login | priv_escalation
    severity: str                       # CRITICAL | HIGH | MEDIUM | LOW
    source_ip: str
    target_host: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    raw_logs: list[dict[str, Any]] = []
    confidence_score: float = 0.0       # 0.0 – 1.0
