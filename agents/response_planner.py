import logging
import json
import uuid
from datetime import datetime
from pydantic import ValidationError
from typing import Dict, Any

from models.incident import IncidentState
from models.response_plan import ResponsePlan
from models.action import Action
from config import settings

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_anthropic import ChatAnthropic

logger = logging.getLogger(__name__)

class ResponsePlannerAgent:
    def __init__(self):
        self.llm = ChatAnthropic(
            model="claude-3-5-sonnet-20241022",
            temperature=0,
            max_tokens=2048,
            anthropic_api_key=settings.ANTHROPIC_API_KEY,
            max_retries=3
        ) if settings.ANTHROPIC_API_KEY and settings.ANTHROPIC_API_KEY != "your_anthropic_api_key_here" else None

        self.system_prompt = """Tu es un expert en réponse aux incidents. Génère un plan de réponse structuré selon le framework PICERL pour cet incident.
Retourne STRICTEMENT le JSON suivant, sans aucun autre texte.

{
  "severity": "HIGH",
  "estimated_impact": "Medium",
  "phases": {
    "containment": [{"type": "block_ip", "target_agent": "firewall", "params": {"ip": "1.2.3.4"}, "requires_approval": false}]
  },
  "actions_auto": [
    {"type": "block_ip", "target_agent": "firewall", "params": {"ip": "1.2.3.4"}, "requires_approval": false}
  ],
  "actions_requires_approval": [
    {"type": "isolate_host", "target_agent": "edr", "params": {"hostname": "server-1"}, "requires_approval": true}
  ]
}

Actions automatiques possibles : block_ip, send_notification, collect_forensics
Actions critiques possibles : isolate_host, kill_process, disable_account, reset_firewall_rules
"""

    async def _generate_plan(self, incident_id: str, context: Dict[str, Any]) -> ResponsePlan:
        # Construct fallback plan
        fallback_actions_auto = [
            Action(id=str(uuid.uuid4()), type="send_notification", target_agent="notif_agent", params={"msg": "Incident detected"}, requires_approval=False)
        ]
        fallback_plan = ResponsePlan(
            incident_id=incident_id,
            severity="MEDIUM",
            phases={"containment": fallback_actions_auto},
            actions_auto=fallback_actions_auto,
            actions_requires_approval=[],
            estimated_impact="Unknown",
            created_at=datetime.utcnow().isoformat()
        )

        if not self.llm:
            return fallback_plan

        try:
            resp = await self.llm.ainvoke([
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=f"Context: {json.dumps(context, default=str)}")
            ])
            raw = resp.content
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                 raw = raw.split("```")[1].strip()
            
            data = json.loads(raw)
            
            # Map parse dict to Action models
            def parse_actions(action_dicts, req_approval):
                parsed = []
                for a in action_dicts:
                    parsed.append(Action(
                        id=str(uuid.uuid4()),
                        type=a.get("type", "unknown"),
                        target_agent=a.get("target_agent", "unknown"),
                        params=a.get("params", {}),
                        requires_approval=req_approval
                    ))
                return parsed

            acts_auto = parse_actions(data.get("actions_auto", []), False)
            acts_crit = parse_actions(data.get("actions_requires_approval", []), True)

            phases_parsed = {}
            for phase, p_acts in data.get("phases", {}).items():
                phases_parsed[phase] = parse_actions(p_acts, False)

            return ResponsePlan(
                incident_id=incident_id,
                severity=data.get("severity", "MEDIUM"),
                phases=phases_parsed,
                actions_auto=acts_auto,
                actions_requires_approval=acts_crit,
                estimated_impact=data.get("estimated_impact", "Unknown"),
                created_at=datetime.utcnow().isoformat()
            )
        except Exception as e:
            logger.error(f"ResponsePlanner Claude fallback: {e}")
            return fallback_plan

    async def run(self, state: IncidentState) -> Dict:
        logger.info(f"ResponsePlannerAgent: Generating plan for incident {state.id}")
        
        if not state.investigation:
            return {"status": "dispatcher_ready"}

        context = {
            "narrative": state.investigation.narrative,
            "ttps": state.investigation.mitre_ttps,
            "risk_score": state.investigation.risk_score
        }

        plan = await self._generate_plan(state.id, context)
        logger.info(f"ResponsePlannerAgent: Plan generated for incident {state.id}")
        
        # Merge actions to global state actions
        all_actions = state.actions.copy()
        all_actions.extend(plan.actions_auto)
        all_actions.extend(plan.actions_requires_approval)

        return {"response_plan": plan, "actions": all_actions, "status": "dispatching"}

response_planner_agent = ResponsePlannerAgent()
