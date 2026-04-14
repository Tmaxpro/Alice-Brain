"""
ALICE Brain — Detection Agent (agents/detection.py)
──────────────────────────────────────────────────
Poll Elasticsearch toutes les DETECTION_POLL_INTERVAL secondes.
Détecte : brute force SSH, port scan, connexion hors horaires, privilege escalation.
Utilise le LLM pour confirmer et scorer la confiance.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage

from config import settings
from models.alert import Alert
from services.elasticsearch import es_service
from services.llm_factory import llm

logger = logging.getLogger(__name__)

# ── Prompt de confirmation LLM ──
CONFIRM_PROMPT = """Tu es un analyste SOC. Voici des logs suspects.
Confirme si c'est une vraie menace (true/false) et donne un confidence_score entre 0 et 1.
Réponds UNIQUEMENT en JSON strict, sans markdown :
{"is_threat": true, "confidence": 0.92, "reason": "explication courte"}"""


async def _confirm_with_llm(logs_sample: list[dict], attack_type: str) -> tuple[bool, float]:
    """
    Appelle le LLM pour confirmer une détection et obtenir un score de confiance.
    Retry 1 fois en cas de JSON malformé, puis fallback sur des valeurs par défaut.
    """
    human_msg = f"Type d'attaque suspecté : {attack_type}\nLogs (échantillon) :\n{json.dumps(logs_sample[:10], default=str)}"

    for attempt in range(2):
        try:
            resp = await llm.ainvoke([
                SystemMessage(content=CONFIRM_PROMPT),
                HumanMessage(content=human_msg),
            ])
            raw = resp.content.strip()
            # Nettoyage markdown
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            data = json.loads(raw)
            return bool(data.get("is_threat", True)), float(data.get("confidence", 0.7))

        except json.JSONDecodeError:
            if attempt == 0:
                human_msg += "\n\nERREUR : ta réponse précédente n'était pas du JSON valide. Réponds UNIQUEMENT avec le JSON demandé."
                continue
            logger.warning("LLM returned invalid JSON twice — using defaults")
        except Exception as exc:
            logger.error("LLM confirmation failed: %s", exc)
            break

    # Fallback codé en dur
    return True, 0.75


def _group_by_ip(logs: list[dict], ip_field: str = "source.ip") -> dict[str, list[dict]]:
    """Regroupe les logs par IP source."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for log in logs:
        # Support nested "source.ip" or flat "source_ip"
        ip = log.get("source", {}).get("ip") if isinstance(log.get("source"), dict) else log.get("source_ip", "")
        if not ip:
            # Fallback: extraire depuis le message
            msg = log.get("message", "")
            parts = msg.split(" from ")
            if len(parts) > 1:
                ip = parts[1].split(" ")[0]
        if ip:
            groups[ip].append(log)
    return groups


async def detect_brute_force() -> list[Alert]:
    """Détecte les brute force SSH (>5 échecs en <60s depuis la même IP)."""
    alerts: list[Alert] = []
    logs = await es_service.get_failed_logins(seconds=60)

    if not logs:
        return alerts

    by_ip = _group_by_ip(logs)
    for ip, ip_logs in by_ip.items():
        if len(ip_logs) > 5:
            is_threat, confidence = await _confirm_with_llm(ip_logs, "brute_force_ssh")
            if is_threat and confidence > 0.6:
                target = ip_logs[0].get("host", {}).get("name", "unknown") if isinstance(ip_logs[0].get("host"), dict) else "unknown"
                alerts.append(Alert(
                    type="brute_force_ssh",
                    severity="HIGH",
                    source_ip=ip,
                    target_host=target,
                    raw_logs=ip_logs[:20],
                    confidence_score=confidence,
                ))
                logger.info("ALERT brute_force_ssh from %s (%d events, conf=%.2f)", ip, len(ip_logs), confidence)

    return alerts


async def detect_port_scan() -> list[Alert]:
    """Détecte les port scans (>20 connexions refusées en <30s depuis la même IP)."""
    alerts: list[Alert] = []
    logs = await es_service.get_refused_connections(seconds=30)

    if not logs:
        return alerts

    by_ip = _group_by_ip(logs)
    for ip, ip_logs in by_ip.items():
        if len(ip_logs) > 20:
            is_threat, confidence = await _confirm_with_llm(ip_logs, "port_scan")
            if is_threat and confidence > 0.6:
                alerts.append(Alert(
                    type="port_scan",
                    severity="MEDIUM",
                    source_ip=ip,
                    target_host=ip_logs[0].get("host", {}).get("name", "unknown") if isinstance(ip_logs[0].get("host"), dict) else "unknown",
                    raw_logs=ip_logs[:20],
                    confidence_score=confidence,
                ))
    return alerts


async def detect_all() -> list[Alert]:
    """Exécute toutes les règles de détection et retourne les alertes."""
    all_alerts: list[Alert] = []

    bf_alerts = await detect_brute_force()
    all_alerts.extend(bf_alerts)

    ps_alerts = await detect_port_scan()
    all_alerts.extend(ps_alerts)

    # TODO: detect_off_hours_login, detect_priv_escalation (même pattern)

    return all_alerts


async def detect_and_inject() -> None:
    """
    Fonction appelée par APScheduler toutes les DETECTION_POLL_INTERVAL secondes.
    Détecte les anomalies et injecte les alertes dans l'orchestrateur.
    """
    from agents.orchestrator import process_alert

    logger.info("[detection] Polling Elasticsearch...")
    alerts = await detect_all()

    if not alerts:
        logger.info("[detection] No alerts detected.")
        return

    logger.info("[detection] %d alert(s) detected — injecting into graph.", len(alerts))
    for alert in alerts:
        try:
            await process_alert(alert)
        except Exception as exc:
            logger.exception("Failed to process alert %s: %s", alert.id, exc)
