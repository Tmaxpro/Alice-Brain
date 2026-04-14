import logging
from typing import Dict
from datetime import datetime
from models.incident import IncidentState
from services.elasticsearch import es_service
from services.websocket_manager import manager as ws_manager
from config import settings

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_anthropic import ChatAnthropic

logger = logging.getLogger(__name__)

class ReportAgent:
    def __init__(self):
        self.llm = ChatAnthropic(
            model="claude-3-5-sonnet-20241022",
            temperature=0,
            max_tokens=2048,
            anthropic_api_key=settings.ANTHROPIC_API_KEY,
            max_retries=3
        ) if settings.ANTHROPIC_API_KEY and settings.ANTHROPIC_API_KEY != "your_anthropic_api_key_here" else None

        self.system_prompt = """Tu es un expert en cybersécurité. Génère un rapport d'incident professionnel et auditable en français comprenant : 
- Résumé exécutif
- Chronologie détaillée
- Analyse technique
- Actions de réponse prises
- IOCs
- Recommandations de remédiation
- Leçons apprises

Format Markdown."""

    async def _generate_report(self, state: IncidentState) -> str:
        if not self.llm:
            return f"# Rapport d'incident (Auto-généré sans IA)\n\nIncident ID: {state.id}\nStatus: {state.status}\nAlert: {state.alert.type if state.alert else 'None'}"

        context = {
            "incident_id": state.id,
            "alert": state.alert.model_dump() if state.alert else None,
            "investigation": state.investigation.model_dump() if state.investigation else None,
            "actions": [a.model_dump() for a in state.actions],
            "timeline": state.timeline
        }

        try:
            resp = await self.llm.ainvoke([
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=f"Context: {context}")
            ])
            return resp.content
        except Exception as e:
            logger.error(f"ReportAgent Claude error: {e}")
            return f"# Rapport d'incident (Fallback)\n\nErreur lors de la génération avec l'IA. Incident: {state.id}"

    async def run(self, state: IncidentState) -> Dict:
        logger.info(f"ReportAgent: Generating report for incident {state.id}")
        
        # Check if we should report: status should be reporting or all actions done
        pending_actions = [a for a in state.actions if a.status == "pending_approval"]
        if pending_actions:
            # Should not happen if routing is correct, but just in case
            logger.info("Actions en attente, report repoussé")
            return {"status": "mitigating"}

        report_md = await self._generate_report(state)
        
        doc = {
            "incident_id": state.id,
            "report_markdown": report_md,
            "created_at": datetime.utcnow().isoformat()
        }
        await es_service.index_document("alice-reports", doc, state.id)
        await ws_manager.broadcast({"event": "incident_closed", "data": {"incident_id": state.id}})

        return {"report": report_md, "status": "closed"}

report_agent = ReportAgent()
