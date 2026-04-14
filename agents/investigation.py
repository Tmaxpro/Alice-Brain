import logging
import json
from typing import Dict, Any
from models.incident import IncidentState, Investigation
from services.abuseipdb import abuseipdb_client
from services.elasticsearch import es_service
from config import settings

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_anthropic import ChatAnthropic

logger = logging.getLogger(__name__)

class InvestigationAgent:
    def __init__(self):
        self.llm = ChatAnthropic(
            model="claude-3-5-sonnet-20241022",
            temperature=0,
            max_tokens=2048,
            anthropic_api_key=settings.ANTHROPIC_API_KEY,
            max_retries=3
        ) if settings.ANTHROPIC_API_KEY and settings.ANTHROPIC_API_KEY != "your_anthropic_api_key_here" else None

        self.system_prompt = """Tu es un analyste SOC senior. Analyse cet incident de sécurité et fournis au format JSON STRICT : 
{
  "summary": "Résumé en 3 phrases",
  "mitre_ttps": ["T1110", "T1078"],
  "confidence_level": "High/Medium/Low",
  "iocs": ["ip", "hash"]
}"""

    async def _analyze_with_claude(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if not self.llm:
            return {
                "summary": "IA désactivée. Résumé automatique : Attaque potentielle de type brute force ou scan.",
                "mitre_ttps": ["T1110 Brute Force"],
                "confidence_level": "Medium",
                "iocs": [context.get("alert", {}).get("source_ip")]
            }

        prompt = f"Contexte de l'incident: {json.dumps(context, default=str)}"
        try:
            resp = await self.llm.ainvoke([
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=prompt)
            ])
            # Parse JSON block
            raw = resp.content
            # Remove markdown JSON wrappers if any
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                 raw = raw.split("```")[1].strip()
            
            return json.loads(raw)
        except Exception as e:
            logger.error(f"Investigation Claude fallback: {e}")
            return {
                "summary": "Erreur lors de l'analyse par l'IA.",
                "mitre_ttps": ["Unknown"],
                "confidence_level": "Low",
                "iocs": []
            }

    async def run(self, state: IncidentState) -> Dict:
        logger.info(f"InvestigationAgent: Starting investigation for incident {state.id}")
        
        alert = state.alert
        if not alert:
            return {"status": "planner_needed"}

        # 1. Enrich AbuseIPDB
        abuse_data = await abuseipdb_client.check_ip(alert.source_ip)
        
        # 2. Corrélation temporelle (ES)
        historical_logs = await es_service.get_logs_for_ip(alert.source_ip, hours=24)
        
        # 3. Calculate Risk Score
        base_abuse_score = abuse_data.get("abuseConfidenceScore", 0)
        frequency_modifier = min(50, len(historical_logs) * 2) # Cap at 50 add
        criticality_modifier = 20 if "root" in str(alert.raw_logs) or alert.severity == "CRITICAL" else 0
        risk_score = min(100.0, base_abuse_score + frequency_modifier + criticality_modifier)

        context = {
            "alert": alert.model_dump(),
            "abuse_enrichment": abuse_data,
            "historical_logs_count": len(historical_logs),
            "calculated_risk_score": risk_score
        }

        # 4. Narrative Analysis with Claude
        analysis = await self._analyze_with_claude(context)

        # 5. Build Investigation object
        investigation = Investigation(
            alert=alert,
            enrichment={"abuseipdb": abuse_data, "historical_events_count": len(historical_logs)},
            narrative=analysis.get("summary", ""),
            mitre_ttps=analysis.get("mitre_ttps", []),
            iocs=analysis.get("iocs", []),
            risk_score=risk_score
        )

        logger.info(f"InvestigationAgent: Completed for incident {state.id}")
        return {"investigation": investigation, "status": "planning"}

investigation_agent = InvestigationAgent()
