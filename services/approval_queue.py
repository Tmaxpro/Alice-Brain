"""
ALICE Brain — Approval Queue (services/approval_queue.py)
─────────────────────────────────────────────────────────
Mécanisme d'attente asynchrone pour la validation humaine des actions critiques.

Fonctionnement :
  1. Le Dispatcher crée une asyncio.Queue pour chaque action critique.
  2. Le nœud wait_approval attend sur cette queue (avec timeout).
  3. L'endpoint POST /api/actions/{id}/approve appelle signal_approval().
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# ── Registre global : action_id → asyncio.Queue ──
approval_queues: dict[str, asyncio.Queue[bool]] = {}


async def wait_for_approval(action_id: str, timeout: int = 300) -> bool:
    """
    Bloque jusqu'à ce qu'un analyste approuve/rejette l'action
    ou que le timeout expire.
    Retourne True si approuvé, False sinon.
    """
    queue: asyncio.Queue[bool] = asyncio.Queue()
    approval_queues[action_id] = queue

    try:
        result = await asyncio.wait_for(queue.get(), timeout=timeout)
        logger.info("Action %s — approval received: %s", action_id, result)
        return result
    except asyncio.TimeoutError:
        logger.warning("Action %s — approval timeout after %ds", action_id, timeout)
        return False
    finally:
        approval_queues.pop(action_id, None)


async def signal_approval(action_id: str, approved: bool) -> bool:
    """
    Appelé par l'endpoint FastAPI POST /api/actions/{id}/approve.
    Retourne True si la queue existait, False sinon.
    """
    if action_id in approval_queues:
        await approval_queues[action_id].put(approved)
        return True
    logger.warning("signal_approval: action_id %s not found in queues", action_id)
    return False
