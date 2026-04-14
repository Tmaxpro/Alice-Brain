"""
ALICE Brain — Agent Registry (services/agent_registry.py)
─────────────────────────────────────────────────────────
Registre dynamique de tous les Alice-Agents connectés.

Maintient un état en mémoire synchronisé avec Elasticsearch (index "alice-agents").
Gère l'enregistrement, le heartbeat, la détection d'agents inaccessibles,
et le routage intelligent par IP / sous-réseau / capability.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from fastapi import WebSocket

from config import settings
from services.elasticsearch import es_service
from services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)

# ───────────────────────────────────────
#  MODÈLES
# ───────────────────────────────────────

class AgentMetrics(BaseModel):
    """Métriques système remontées par l'agent via heartbeat."""
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    disk_percent: float = 0.0
    active_connections: int = 0


class AgentInfo(BaseModel):
    """Représentation complète d'un Alice-Agent enregistré."""
    agent_id: str
    hostname: str
    ip: str
    os: str = "unknown"
    sub_agents: dict[str, str] = {}       # "endpoint" → "http://x.x.x.x:8001"
    capabilities: list[str] = []
    token: str = ""
    status: Literal["online", "offline", "unreachable"] = "online"
    last_heartbeat: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    registered_at: str = ""
    metrics: AgentMetrics = Field(default_factory=AgentMetrics)

    # Référence WebSocket — exclue de la sérialisation (non JSON-serializable)
    ws_connection: Any | None = Field(default=None, exclude=True)

    model_config = {"arbitrary_types_allowed": True}


# ───────────────────────────────────────
#  REGISTRE
# ───────────────────────────────────────

class AgentRegistry:
    """
    Registre centralisé de tous les Alice-Agents.

    - Stockage mémoire (dict agent_id → AgentInfo)
    - Synchronisation avec Elasticsearch (index alice-agents)
    - Détection automatique des agents inaccessibles (heartbeat timeout 90s)
    - Routage intelligent par IP / sous-réseau / capability
    """

    def __init__(self) -> None:
        self.agents: dict[str, AgentInfo] = {}
        self._monitor_task: asyncio.Task | None = None
        self._es_index = getattr(settings, "ES_INDEX_AGENTS", "alice-agents")

    # ──────────── Enregistrement ────────────

    async def register(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Enregistre un nouvel agent (ou met à jour un agent existant).

        Retourne :
            {"status": "registered", "agent_token": "...", "brain_ws_url": "...", "heartbeat_interval": 30}
        """
        agent_id = data["agent_id"]
        token = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        agent = AgentInfo(
            agent_id=agent_id,
            hostname=data.get("hostname", "unknown"),
            ip=data.get("ip", "0.0.0.0"),
            os=data.get("os", "unknown"),
            sub_agents=data.get("sub_agents", {}),
            capabilities=data.get("capabilities", []),
            token=token,
            status="online",
            last_heartbeat=now,
            registered_at=data.get("registered_at", now.isoformat()),
        )

        self.agents[agent_id] = agent

        # Indexer dans Elasticsearch
        await self._index_agent(agent)

        logger.info(
            "Agent registered: id=%s, hostname=%s, ip=%s, capabilities=%s",
            agent_id, agent.hostname, agent.ip, agent.capabilities,
        )

        # Broadcaster sur le dashboard WS
        await ws_manager.broadcast("agent_registered", {
            "agent_id": agent_id,
            "hostname": agent.hostname,
            "ip": agent.ip,
            "capabilities": agent.capabilities,
        })

        # Construire l'URL WebSocket que l'agent doit utiliser
        brain_host = getattr(settings, "BRAIN_WS_HOST", "0.0.0.0")
        brain_ws_url = f"ws://{brain_host}:8000/ws/agent/{agent_id}"

        return {
            "status": "registered",
            "agent_token": token,
            "brain_ws_url": brain_ws_url,
            "heartbeat_interval": 30,
        }

    async def unregister(self, agent_id: str) -> bool:
        """Désenregistre manuellement un agent."""
        agent = self.agents.pop(agent_id, None)
        if not agent:
            return False

        # Fermer la connexion WS s'il y en a une
        if agent.ws_connection:
            try:
                await agent.ws_connection.close()
            except Exception:
                pass

        # Mettre à jour ES
        agent.status = "offline"
        await self._index_agent(agent)

        logger.info("Agent unregistered: %s", agent_id)
        await ws_manager.broadcast("agent_unregistered", {"agent_id": agent_id})
        return True

    # ──────────── Requêtes ────────────

    def get_all_agents(self) -> list[AgentInfo]:
        """Retourne tous les agents enregistrés."""
        return list(self.agents.values())

    def get_online_agents(self) -> list[AgentInfo]:
        """Retourne uniquement les agents avec status 'online'."""
        return [a for a in self.agents.values() if a.status == "online"]

    def get_agent(self, agent_id: str) -> AgentInfo | None:
        """Retourne un agent par son ID."""
        return self.agents.get(agent_id)

    def get_agent_by_ip(self, ip: str) -> AgentInfo | None:
        """Retourne l'agent installé sur la machine cible (match IP exact)."""
        for agent in self.agents.values():
            if agent.ip == ip and agent.status == "online":
                return agent
        return None

    def get_agents_by_capability(self, capability: str) -> list[AgentInfo]:
        """Retourne les agents online possédant la capability demandée."""
        return [
            a for a in self.agents.values()
            if a.status == "online" and capability in a.capabilities
        ]

    def get_agent_same_subnet(self, target_ip: str) -> AgentInfo | None:
        """
        Retourne un agent online dans le même sous-réseau /24 que target_ip.
        Priorise les agents qui partagent le plus de bits avec la cible.
        """
        try:
            target = ipaddress.ip_address(target_ip)
            target_network = ipaddress.ip_network(f"{target_ip}/24", strict=False)
        except ValueError:
            return None

        for agent in self.agents.values():
            if agent.status != "online":
                continue
            try:
                agent_addr = ipaddress.ip_address(agent.ip)
                if agent_addr in target_network:
                    return agent
            except ValueError:
                continue

        return None

    def get_best_agent_for_target(self, target_ip: str, capability: str | None = None) -> AgentInfo | None:
        """
        Routage intelligent : retourne le meilleur agent pour une cible donnée.

        Priorité :
          1. Agent installé sur la machine cible (même IP)
          2. Agent dans le même sous-réseau /24
          3. N'importe quel agent online avec la capability requise
        """
        # 1. Match exact IP
        agent = self.get_agent_by_ip(target_ip)
        if agent and (not capability or capability in agent.capabilities):
            return agent

        # 2. Même sous-réseau
        agent = self.get_agent_same_subnet(target_ip)
        if agent and (not capability or capability in agent.capabilities):
            return agent

        # 3. Fallback : n'importe quel agent avec la capability
        if capability:
            agents = self.get_agents_by_capability(capability)
            if agents:
                return agents[0]

        # 4. Dernier recours : n'importe quel agent online
        online = self.get_online_agents()
        return online[0] if online else None

    # ──────────── Heartbeat ────────────

    async def update_heartbeat(self, agent_id: str, metrics: dict[str, Any] | None = None) -> bool:
        """Met à jour le timestamp du dernier heartbeat et les métriques optionnelles."""
        agent = self.agents.get(agent_id)
        if not agent:
            return False

        agent.last_heartbeat = datetime.now(timezone.utc)
        agent.status = "online"

        if metrics:
            agent.metrics = AgentMetrics(**metrics)

        return True

    async def mark_unreachable(self, agent_id: str) -> None:
        """Marque un agent comme inaccessible (heartbeat timeout)."""
        agent = self.agents.get(agent_id)
        if not agent or agent.status == "unreachable":
            return

        agent.status = "unreachable"
        logger.warning("Agent marked UNREACHABLE: %s (last heartbeat: %s)", agent_id, agent.last_heartbeat)

        # Indexer dans ES
        await self._index_agent(agent)

        # Broadcaster sur le dashboard WS
        await ws_manager.broadcast("agent_unreachable", {
            "agent_id": agent_id,
            "hostname": agent.hostname,
            "ip": agent.ip,
            "last_heartbeat": agent.last_heartbeat.isoformat(),
        })

    # ──────────── Connexion WebSocket ────────────

    def set_ws_connection(self, agent_id: str, ws: WebSocket) -> None:
        """Associe une connexion WebSocket active à un agent."""
        agent = self.agents.get(agent_id)
        if agent:
            agent.ws_connection = ws
            agent.status = "online"
            logger.info("WS connection set for agent %s", agent_id)

    def clear_ws_connection(self, agent_id: str) -> None:
        """Supprime la référence à la connexion WebSocket d'un agent."""
        agent = self.agents.get(agent_id)
        if agent:
            agent.ws_connection = None
            logger.info("WS connection cleared for agent %s", agent_id)

    def has_ws_connection(self, agent_id: str) -> bool:
        """Vérifie si l'agent a une connexion WebSocket active."""
        agent = self.agents.get(agent_id)
        return agent is not None and agent.ws_connection is not None

    # ──────────── Monitoring (background task) ────────────

    async def start_heartbeat_monitor(self) -> None:
        """Démarre la boucle de surveillance des heartbeats en arrière-plan."""
        self._monitor_task = asyncio.create_task(self._heartbeat_monitor_loop())
        logger.info("Heartbeat monitor started (timeout=90s, check_interval=15s)")

    async def stop_heartbeat_monitor(self) -> None:
        """Arrête la boucle de surveillance."""
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

    async def _heartbeat_monitor_loop(self) -> None:
        """
        Vérifie toutes les 15 secondes si des agents n'ont pas envoyé de heartbeat
        depuis plus de 90 secondes. Si c'est le cas, les marque comme 'unreachable'.
        """
        heartbeat_timeout_seconds = 90
        check_interval_seconds = 15

        while True:
            try:
                await asyncio.sleep(check_interval_seconds)
                now = datetime.now(timezone.utc)

                for agent_id, agent in list(self.agents.items()):
                    if agent.status == "offline":
                        continue

                    elapsed = (now - agent.last_heartbeat).total_seconds()
                    if elapsed > heartbeat_timeout_seconds and agent.status == "online":
                        await self.mark_unreachable(agent_id)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Heartbeat monitor error: %s", exc)

    # ──────────── Elasticsearch ────────────

    async def _index_agent(self, agent: AgentInfo) -> None:
        """Indexe (ou met à jour) un agent dans Elasticsearch."""
        doc = agent.model_dump(exclude={"ws_connection"})
        # Convertir datetime en ISO string pour ES
        if isinstance(doc.get("last_heartbeat"), datetime):
            doc["last_heartbeat"] = doc["last_heartbeat"].isoformat()
        await es_service.index_document(self._es_index, doc, agent.agent_id)

    # ──────────── Token validation ────────────

    def validate_token(self, agent_id: str, token: str) -> bool:
        """Vérifie que le token fourni correspond bien à l'agent."""
        agent = self.agents.get(agent_id)
        return agent is not None and agent.token == token


# ── Singleton ──
agent_registry = AgentRegistry()
