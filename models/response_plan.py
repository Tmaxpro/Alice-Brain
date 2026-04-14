from pydantic import BaseModel
from typing import Dict, List
from datetime import datetime
from models.action import Action

class ResponsePlan(BaseModel):
    incident_id: str
    severity: str
    phases: Dict[str, List[Action]]
    actions_auto: List[Action]
    actions_requires_approval: List[Action]
    estimated_impact: str
    created_at: str
