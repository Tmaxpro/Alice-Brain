"""
ALICE Brain — Demo Injector (demo_injector.py)
────────────────────────────────────────────
Script standalone qui simule une attaque brute-force SSH
en injectant des faux logs dans Elasticsearch.

Usage :
  python demo_injector.py

Prérequis : Elasticsearch doit être accessible sur http://localhost:9200
"""

from __future__ import annotations

import asyncio
import random
import sys
from datetime import datetime, timezone

import httpx
from elasticsearch import AsyncElasticsearch

ES_URL = "http://localhost:9200"
INDEX = "filebeat-alice-demo"   # Matche le pattern "logs-*"
ALICE_API = "http://localhost:8000"

MALICIOUS_IP = "192.168.100.50"
TARGET_HOST = "bastion-01"
TARGET_HOST_IP = "10.0.0.1"
TOTAL_FAILURES = 52


def _make_failed_log(ip: str, host: str) -> dict:
    """Construit un document ES type syslog d'échec SSH."""
    return {
        "@timestamp": datetime.now(timezone.utc).isoformat(),
        "message": f"Failed password for root from {ip} port {random.randint(40000, 65535)} ssh2",
        "host": {"name": host, "ip": TARGET_HOST_IP},
        "source": {"ip": ip},
        "log": {"file": {"path": "/var/log/auth.log"}},
        "event": {"category": "authentication", "outcome": "failure"},
        "tags": ["alice-demo", "ssh"],
    }


def _make_success_log(ip: str, host: str) -> dict:
    """Construit un document ES de connexion SSH réussie."""
    return {
        "@timestamp": datetime.now(timezone.utc).isoformat(),
        "message": f"Accepted password for root from {ip} port {random.randint(40000, 65535)} ssh2",
        "host": {"name": host, "ip": TARGET_HOST_IP},
        "source": {"ip": ip},
        "log": {"file": {"path": "/var/log/auth.log"}},
        "event": {"category": "authentication", "outcome": "success"},
        "tags": ["alice-demo", "ssh"],
    }


async def main() -> None:
    print("=" * 60)
    print("  ALICE Brain — Demo Injector")
    print("  Scénario : Brute-force SSH")
    print("=" * 60)

    # ── Connexion ES ──
    es = AsyncElasticsearch(ES_URL)
    try:
        if not await es.ping():
            print(f"❌ Impossible de joindre Elasticsearch sur {ES_URL}")
            sys.exit(1)
    except Exception as exc:
        print(f"❌ Elasticsearch non disponible : {exc}")
        sys.exit(1)

    print(f"✅ Connecté à Elasticsearch ({ES_URL})")
    print(f"📦 Index cible : {INDEX}")
    print(f"🎯 IP attaquante : {MALICIOUS_IP}")
    print(f"🖥️  Cible : {TARGET_HOST}")
    print()

    # ── Phase 1 : Injection des échecs ──
    print(f"🔨 Injection de {TOTAL_FAILURES} tentatives SSH échouées...")
    for i in range(1, TOTAL_FAILURES + 1):
        doc = _make_failed_log(MALICIOUS_IP, TARGET_HOST)
        await es.index(index=INDEX, body=doc)
        delay = random.uniform(0.3, 0.9)
        print(f"  [{i:2d}/{TOTAL_FAILURES}] Failed password — delay {delay:.2f}s")
        await asyncio.sleep(delay)

    print()
    print("⏳ Pause de 3 secondes (l'attaquant persiste)...")
    await asyncio.sleep(3)

    # ── Phase 2 : Connexion réussie ──
    print("🔓 Injection de la connexion réussie (malgré les échecs)...")
    success_doc = _make_success_log(MALICIOUS_IP, TARGET_HOST)
    await es.index(index=INDEX, body=success_doc)
    print("  ✅ Accepted password for root")

    await es.close()
    print()

    # ── Phase 3 : Trigger manuel via l'API ──
    print("📡 Envoi d'une alerte manuelle à ALICE Brain...")
    manual_alert = {
        "type": "brute_force_ssh",
        "severity": "HIGH",
        "source_ip": MALICIOUS_IP,
        "target_host": TARGET_HOST,
        "confidence_score": 0.95,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{ALICE_API}/api/alerts/manual", json=manual_alert)
            if resp.status_code == 200:
                data = resp.json()
                print(f"  ✅ Alerte acceptée — alert_id={data.get('alert_id')}")
            else:
                print(f"  ⚠️  API returned {resp.status_code}: {resp.text}")
    except Exception as exc:
        print(f"  ⚠️  Could not reach ALICE API ({ALICE_API}): {exc}")
        print("     → Le polling automatique (30s) detectera les logs dans ES.")

    print()
    print("=" * 60)
    print("  ✅ Injection terminée !")
    print("  → Vérifiez les logs de alice-brain pour le pipeline LangGraph")
    print("  → GET http://localhost:8000/api/incidents pour voir les résultats")
    print("  → WS ws://localhost:8000/ws/incidents pour le temps réel")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
