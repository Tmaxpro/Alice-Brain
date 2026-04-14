import logging
from typing import TypedDict, Annotated, Sequence, Dict, Any
from langgraph.graph import StateGraph, START, END
from datetime import datetime

from models.incident import IncidentState
from agents.detection import detection_agent
from agents.investigation import investigation_agent
from agents.response_planner import response_planner_agent
from agents.dispatcher import dispatcher_agent
from agents.report import report_agent
from services.elasticsearch import es_service

logger = logging.getLogger(__name__)

def update_incident_state(left: Any, right: Any) -> Any:
    """Reducer for the state graph. For simple objects, right overwrites left."""
    return right if right is not None else left

def merge_lists(left: list, right: list) -> list:
    if not left: return right
    if not right: return left
    # simple append or deduplicate by id
    merged = {getattr(i, "id", str(i)): i for i in left}
    for r in right:
        merged[getattr(r, "id", str(r))] = r
    return list(merged.values())

class GraphState(TypedDict):
    incident: Annotated[IncidentState, update_incident_state]

class Orchestrator:
    def __init__(self):
        self.workflow = StateGraph(GraphState)
        self._build_graph()
        self.app = self.workflow.compile()
        self.running_incidents: Dict[str, IncidentState] = {}

    def _build_graph(self):
        # Nodes wrap our agent async classes
        async def node_detection(state: GraphState):
             res = await detection_agent.run(state["incident"])
             if "status" in res:
                 state["incident"].status = res["status"]
             return {"incident": state["incident"]}

        async def node_investigation(state: GraphState):
             res = await investigation_agent.run(state["incident"])
             if "investigation" in res:
                 state["incident"].investigation = res["investigation"]
             if "status" in res:
                 state["incident"].status = res["status"]
             return {"incident": state["incident"]}

        async def node_response_planner(state: GraphState):
             res = await response_planner_agent.run(state["incident"])
             if "response_plan" in res:
                 state["incident"].response_plan = res["response_plan"]
             if "actions" in res:
                 state["incident"].actions = res["actions"]
             if "status" in res:
                 state["incident"].status = res["status"]
             return {"incident": state["incident"]}

        async def node_dispatcher(state: GraphState):
             res = await dispatcher_agent.run(state["incident"])
             if "actions" in res:
                 state["incident"].actions = res["actions"]
             if "status" in res:
                 state["incident"].status = res["status"]
             return {"incident": state["incident"]}

        async def node_report(state: GraphState):
             res = await report_agent.run(state["incident"])
             if "report" in res:
                 state["incident"].report = res["report"]
             if "status" in res:
                 state["incident"].status = res["status"]
             return {"incident": state["incident"]}

        self.workflow.add_node("detection", node_detection)
        self.workflow.add_node("investigation", node_investigation)
        self.workflow.add_node("response_planner", node_response_planner)
        self.workflow.add_node("dispatcher", node_dispatcher)
        self.workflow.add_node("report", node_report)

        self.workflow.add_edge(START, "detection")

        # Routing logic
        def route_after_detection(state: GraphState):
             return "investigation" if state["incident"].status == "investigating" else END

        def route_after_investigation(state: GraphState):
             return "response_planner" if state["incident"].status == "planning" else END

        def route_after_planner(state: GraphState):
             return "dispatcher" if state["incident"].status == "dispatching" else END

        def route_after_dispatcher(state: GraphState):
             if state["incident"].status == "reporting":
                 return "report"
             return END # 'mitigating' means it waits for manual approval

        self.workflow.add_conditional_edges("detection", route_after_detection)
        self.workflow.add_conditional_edges("investigation", route_after_investigation)
        self.workflow.add_conditional_edges("response_planner", route_after_planner)
        self.workflow.add_conditional_edges("dispatcher", route_after_dispatcher)
        
        self.workflow.add_edge("report", END)

    async def process_new_alert(self, alert):
        """Call when a new alert is generated manually or via polling"""
        # Deduplication simple
        for inc_id, inc in self.running_incidents.items():
             if inc.alert and inc.alert.source_ip == alert.source_ip and inc.alert.type == alert.type:
                  # Verifier timestamp < 5 min
                  delta = datetime.utcnow() - inc.alert.timestamp
                  if delta.total_seconds() < 300:
                       logger.info(f"Deduplicated alert for {alert.source_ip}")
                       return

        new_incident = IncidentState(alert=alert)
        new_incident.timeline.append({"time": datetime.utcnow().isoformat(), "event": "Alert received"})
        self.running_incidents[new_incident.id] = new_incident
        
        # Save to ES
        await es_service.index_document("alice-incidents", new_incident.model_dump(), new_incident.id)
        
        # Run graph
        await self.app.ainvoke({"incident": new_incident})

    async def approve_action_and_resume(self, incident_id: str, action_id: str):
        if incident_id not in self.running_incidents:
            return False
        
        incident = self.running_incidents[incident_id]
        action_found = False
        for action in incident.actions:
            if action.id == action_id and action.status == "pending_approval":
                action.status = "executed"  # Simulate execution upon approval
                action.approved = True
                action.executed = True
                action_found = True
                incident.timeline.append({"time": datetime.utcnow().isoformat(), "event": f"Action {action_id} approved and executed"})
                break
        
        if not action_found:
            return False

        # Save to ES
        await es_service.index_document("alice-incidents", incident.model_dump(), incident.id)

        # Re-run graph from dispatcher
        # LangGraph state management allows invoking from a specific node with StateGraph
        # Here for simplicity we just re-feed the updated incident state. The graph will start from START (detection) -> investigate -> etc.
        # But wait, our nodes are side-effect free if they check current status.
        # To make it cleanly resume without re-running everything:
        
        incident.status = "dispatching" # Force it to continue to routing correctly.
        # Actually in Langgraph a true checkpoint saver allows resuming. Since we simulate:
        # we will directly call dispatcher then report manually or re-invoke. 
        # For simplicity without proper Checkpointer config, we manually push through:
        res = await dispatcher_agent.run(incident)
        if res["status"] == "reporting":
            await report_agent.run(incident)
            
        return True

orchestrator = Orchestrator()
