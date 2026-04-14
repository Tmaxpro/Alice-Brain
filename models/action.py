from pydantic import BaseModel, Field
from typing import Dict, Any, Optional

class Action(BaseModel):
    id: str
    type: str # block_ip | isolate_host | kill_process | send_notification
    target_agent: str # endpoint_agent | network_agent | notif_agent
    params: Dict[str, Any]
    requires_approval: bool
    approved: bool = False
    executed: bool = False
    status: str = "pending" # pending, pending_approval, executed, failed

# Fin de action.py
