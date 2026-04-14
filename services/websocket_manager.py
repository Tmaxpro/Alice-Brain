"""
ALICE Brain — WebSocket Manager (services/websocket_manager.py)
──────────────────────────────────────────────────────────────
Maintient les connexions WebSocket actives et expose un broadcast typé.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("WS client connected — total: %d", len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info("WS client disconnected")

    async def broadcast(self, event: str, data: dict[str, Any]) -> None:
        """Envoie un événement typé à tous les clients WS connectés."""
        if not self.active_connections:
            return

        message = json.dumps({"event": event, "data": data}, default=str)
        dead: list[WebSocket] = []

        for conn in self.active_connections:
            try:
                await conn.send_text(message)
            except Exception as exc:
                logger.warning("WS send failed, removing connection: %s", exc)
                dead.append(conn)

        for d in dead:
            self.disconnect(d)


# ── Singleton ──
ws_manager = ConnectionManager()
