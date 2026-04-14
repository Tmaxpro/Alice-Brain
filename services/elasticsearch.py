"""
ALICE Brain — Service Elasticsearch (services/elasticsearch.py)
──────────────────────────────────────────────────────────────
Client asynchrone Elasticsearch avec helpers de requête et mode dégradé.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from elasticsearch import AsyncElasticsearch
from elasticsearch.exceptions import ConnectionError, NotFoundError

from config import settings

logger = logging.getLogger(__name__)


class ElasticService:
    def __init__(self) -> None:
        self.client = AsyncElasticsearch(settings.ES_URL)

    # ──────────────── Health ────────────────

    async def check_health(self) -> bool:
        try:
            return await self.client.ping()
        except Exception:
            return False

    # ──────────────── Recherche ────────────────

    async def search(
        self,
        index: str,
        query: dict[str, Any],
        size: int = 500,
    ) -> list[dict[str, Any]]:
        """Recherche générique avec gestion d'erreur."""
        try:
            resp = await self.client.search(
                index=index, body={"query": query, "size": size}, ignore_unavailable=True
            )
            return [hit["_source"] for hit in resp.get("hits", {}).get("hits", [])]
        except (ConnectionError, NotFoundError) as exc:
            logger.error("ES search error on %s: %s", index, exc)
            return []
        except Exception as exc:
            logger.error("ES unexpected error: %s", exc)
            return []

    async def get_failed_logins(self, seconds: int = 60) -> list[dict[str, Any]]:
        """Retourne les logs 'Failed password' des N dernières secondes."""
        query = {
            "bool": {
                "must": [
                    {"match_phrase": {"message": "Failed password"}},
                    {"range": {"@timestamp": {"gte": f"now-{seconds}s"}}},
                ],
            }
        }
        return await self.search(settings.ES_INDEX_LOGS, query, size=1000)

    async def get_refused_connections(self, seconds: int = 30) -> list[dict[str, Any]]:
        """Retourne les connexions refusées des N dernières secondes."""
        query = {
            "bool": {
                "must": [
                    {"match_phrase": {"message": "Connection refused"}},
                    {"range": {"@timestamp": {"gte": f"now-{seconds}s"}}},
                ],
            }
        }
        return await self.search(settings.ES_INDEX_LOGS, query, size=1000)

    async def get_logs_for_ip(self, ip: str, hours: int = 24) -> list[dict[str, Any]]:
        """Événements associés à une IP sur les dernières N heures."""
        query = {
            "bool": {
                "should": [
                    {"match": {"source.ip": ip}},
                    {"match": {"message": ip}},
                ],
                "minimum_should_match": 1,
                "filter": [
                    {"range": {"@timestamp": {"gte": f"now-{hours}h"}}},
                ],
            }
        }
        return await self.search(settings.ES_INDEX_LOGS, query, size=500)

    # ──────────────── Indexation ────────────────

    async def index_document(
        self,
        index: str,
        document: dict[str, Any],
        doc_id: str | None = None,
    ) -> None:
        """Indexe un document dans ES."""
        try:
            kwargs: dict[str, Any] = {"index": index, "body": document}
            if doc_id:
                kwargs["id"] = doc_id
            await self.client.index(**kwargs)
        except Exception as exc:
            logger.error("Failed to index in %s: %s", index, exc)

    async def get_document(
        self, index: str, doc_id: str
    ) -> dict[str, Any] | None:
        """Récupère un document par ID."""
        try:
            resp = await self.client.get(index=index, id=doc_id)
            return resp.get("_source")
        except NotFoundError:
            return None
        except Exception as exc:
            logger.error("Failed to get %s/%s: %s", index, doc_id, exc)
            return None

    async def close(self) -> None:
        await self.client.close()


# ── Singleton ──
es_service = ElasticService()
