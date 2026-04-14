"""
ALICE Brain — API Actions (api/actions.py)
─────────────────────────────────────────
Endpoint de validation humaine des actions critiques.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.approval_queue import signal_approval

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/actions", tags=["actions"])


class ApprovalRequest(BaseModel):
    approved: bool = True


@router.post("/{action_id}/approve")
async def approve_action(action_id: str, body: ApprovalRequest):
    """
    Approuver ou rejeter une action critique.
    Body: {"approved": true}  ou  {"approved": false}
    """
    found = await signal_approval(action_id, body.approved)

    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"Action {action_id} not found in pending queue (already processed or expired).",
        )

    status = "approved" if body.approved else "rejected"
    logger.info("Action %s — %s by analyst", action_id, status)
    return {"action_id": action_id, "status": status}
