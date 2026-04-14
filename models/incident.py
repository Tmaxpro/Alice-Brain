from pydantic import BaseModel, Field
from typing import Dict, List, Any, Optional
from datetime import datetime
import uuid
from models.alert import Alert
from models.action import Action
from models.response_plan import ResponsePlan

class Investigation(BaseModel):
    alert: Alert
    enrichment: Dict[str, Any] = {}
    narrative: str = ""
    mitre_ttps: List[str] = []
    iocs: List[str] = []
    risk_score: float = 0.0

class IncidentState(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    alert: Optional[Alert] = None
    investigation: Optional[Investigation] = None
    response_plan: Optional[ResponsePlan] = None
    actions: List[Action] = []
    status: str = "open" # open, investigating, planning, mitigating, closed
    timeline: List[Dict[str, Any]] = []
    report: Optional[str] = None
