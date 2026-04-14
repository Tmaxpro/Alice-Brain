"""
ALICE Brain — Modèle Action.
Représente une action de remédiation (automatique ou critique).
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class Action(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str               # block_ip | isolate_host | kill_process | send_notification | collect_forensics | disable_account | reset_firewall_rules
    target_agent: str       # endpoint_agent | network_agent | notif_agent
    params: dict[str, Any] = {}
    reason: str = ""
    requires_approval: bool = False
    approved: bool = False
    executed: bool = False
    status: str = "pending"  # pending | pending_approval | approved | executing | executed | failed | rejected | timeout
    output: str = ""
