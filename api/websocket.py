"""
ALICE Brain — API WebSocket (api/websocket.py)
────────────────────────────────────────────
Route WebSocket pour le push temps réel vers le dashboard.

Events possibles :
  new_alert | investigation_done | approval_required |
  action_executed | incident_closed
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.websocket_manager import ws_manager

router = APIRouter()


@router.websocket("/ws/incidents")
async def websocket_endpoint(websocket: WebSocket):
    """
    Connexion WebSocket persistante.
    Le serveur pousse les événements ; le client écoute.
    """
    await ws_manager.connect(websocket)
    try:
        while True:
            # Garder la connexion ouverte — on ne traite pas les messages entrants
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
