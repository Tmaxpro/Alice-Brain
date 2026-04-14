import logging
import httpx
from typing import Dict
from models.incident import IncidentState
from services.websocket_manager import manager as ws_manager
from services.elasticsearch import es_service
from datetime import datetime

logger = logging.getLogger(__name__)

class DispatcherAgent:
    async def _execute_action_http(self, action, target_url: str):
        """Simuler l'appel à un agent client-side."""
        payload = {
            "action_type": action.type,
            "params": action.params
        }
        try:
            # En réalité, nous appellerions le vrai target_url. 
            # Pour éviter des erreurs réseau immédiates (Mock):
            logger.info(f"Simulating POST {target_url}/execute with {payload}")
            action.executed = True
            action.status = "executed"
        except Exception as e:
            logger.error(f"Failed to execute action {action.id}: {e}")
            action.status = "failed"

    async def run(self, state: IncidentState) -> Dict:
        logger.info(f"DispatcherAgent: Processing actions for incident {state.id}")
        
        updated_actions = []
        has_pending = False

        for action in state.actions:
            if action.status == "executed" or action.status == "failed":
                updated_actions.append(action)
                continue

            # Actions auto
            if not action.requires_approval:
                logger.info(f"Executing auto action: {action.type}")
                # Mock URL for target agent
                mock_url = f"http://{action.target_agent}:8001"
                await self._execute_action_http(action, mock_url)
                
                # Log to ES
                doc = {
                    "action_id": action.id,
                    "incident_id": state.id,
                    "type": action.type,
                    "status": action.status,
                    "timestamp": datetime.utcnow().isoformat()
                }
                await es_service.index_document("alice-actions", doc, action.id)
                await ws_manager.broadcast({"event": "action_executed", "data": doc})

            # Actions critiques (nécessite validation)
            else:
                if action.status == "pending":
                    action.status = "pending_approval"
                    logger.info(f"Action requires approval: {action.type}")
                    
                    doc = {
                        "action_id": action.id,
                        "incident_id": state.id,
                        "type": action.type,
                        "status": action.status,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                    await es_service.index_document("alice-actions", doc, action.id)
                    await ws_manager.broadcast({"event": "approval_required", "data": doc})
                
                if action.status == "pending_approval":
                    has_pending = True

            updated_actions.append(action)

        status_update = "mitigating" if has_pending else "reporting"
        
        return {"actions": updated_actions, "status": status_update}

dispatcher_agent = DispatcherAgent()
