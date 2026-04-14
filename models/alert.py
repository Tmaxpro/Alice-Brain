from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
import uuid

class Alert(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    source_ip: str
    target_host: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    raw_logs: List[dict] = []
    confidence_score: float = 0.0
