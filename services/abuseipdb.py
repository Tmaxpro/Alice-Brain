"""
ALICE Brain — AbuseIPDB Client (services/abuseipdb.py)
────────────────────────────────────────────────────
Enrichissement IP via l'API AbuseIPDB v2.
Skip silencieux si la clé n'est pas configurée.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)


class AbuseIPDBClient:
    BASE_URL = "https://api.abuseipdb.com/api/v2/check"

    async def check_ip(self, ip_address: str) -> dict[str, Any]:
        """
        Vérifie une IP sur AbuseIPDB.
        Retourne un dict avec au minimum abuseConfidenceScore.
        """
        if not settings.ABUSEIPDB_KEY:
            logger.info("AbuseIPDB key absent — skip enrichment for %s", ip_address)
            return {"abuseConfidenceScore": 0, "note": "no_api_key"}

        headers = {"Accept": "application/json", "Key": settings.ABUSEIPDB_KEY}
        params = {"ipAddress": ip_address, "maxAgeInDays": "90"}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self.BASE_URL, headers=headers, params=params)
                resp.raise_for_status()
                return resp.json().get("data", {})
        except Exception as exc:
            logger.error("AbuseIPDB lookup failed for %s: %s", ip_address, exc)
            return {"abuseConfidenceScore": 0, "error": str(exc)}


# ── Singleton ──
abuseipdb_client = AbuseIPDBClient()
