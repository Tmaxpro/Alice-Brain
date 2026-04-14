import logging
from typing import Dict, Any, List
from datetime import datetime
from models.incident import IncidentState
from models.alert import Alert
from config import settings

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_anthropic import ChatAnthropic

logger = logging.getLogger(__name__)

class DetectionAgent:
    def __init__(self):
        # We initialize Claude outside standard __init__ if needed, but here is fine.
        # It handles retries natively in Langchain/Anthropic integration
        self.llm = ChatAnthropic(
            model="claude-3-5-sonnet-20241022",
            temperature=0,
            max_tokens=1024,
            anthropic_api_key=settings.ANTHROPIC_API_KEY,
            max_retries=3
        ) if settings.ANTHROPIC_API_KEY and settings.ANTHROPIC_API_KEY != "your_anthropic_api_key_here" else None

    async def detect_anomalies(self, recent_logs: List[Dict[str, Any]]) -> List[Alert]:
        """
        Takes raw recent logs and identifies alerts logic.
        (Note: the real polling logic will be in main.py APScheduler which passes logs here,
        or this agent can poll directly. The architecture requests: "Poll Elasticsearch toutes les 30 secondes")
        We'll do rules-based detection then pass to Claude for confirmation.
        """
        alerts = []
        # Group failed logins by IP
        failed_by_ip = {}
        for log in recent_logs:
            msg = log.get("message", "")
            if "Failed password" in msg:
                ip = log.get("source_ip", "unknown")
                if ip not in failed_by_ip:
                    failed_by_ip[ip] = []
                failed_by_ip[ip].append(log)

        for ip, logs in failed_by_ip.items():
            if len(logs) > 5: # Threshold: >5 failures in 60s
                # Call Claude to confirm brute force
                confidence_score = 0.9
                severity = "HIGH"
                if self.llm:
                    prompt = f"Analyse ces logs système. S'agit-il d'une attaque brute force SSH ? Logs: {str(logs[:10])}. Réponds uniquement par OUI ou NON, suivi du niveau de confiance sur 100."
                    try:
                        resp = await self.llm.ainvoke([HumanMessage(content=prompt)])
                        content = resp.content.lower()
                        if "oui" in content:
                           confidence_score = 0.95
                    except Exception as e:
                        logger.error(f"Claude analysis failed: {e}")
                
                alert = Alert(
                    type="brute_force_ssh",
                    severity=severity,
                    source_ip=ip,
                    target_host=logs[0].get("host", {}).get("name", "unknown"),
                    raw_logs=logs,
                    confidence_score=confidence_score
                )
                alerts.append(alert)
        return alerts

    async def run(self, state: IncidentState) -> Dict:
        """
        LangGraph node execution.
        Normally detection is the entrypoint. The orchestrator receives an alert manually or natively.
        If state has an alert, we just pass. If we needed to poll here, we could.
        """
        if not state.alert:
            # We don't have an alert. This might happen if the graph started empty.
            pass
        logger.info(f"DetectionAgent: Analyzed state {state.id}")
        return {"status": "investigating"}

detection_agent = DetectionAgent()
