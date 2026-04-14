from elasticsearch import AsyncElasticsearch
from elasticsearch.exceptions import ConnectionError, NotFoundError
from config import settings
from typing import Dict, Any, List, Optional
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class ElasticService:
    def __init__(self):
        self.client = AsyncElasticsearch(settings.ES_URL)

    async def check_health(self) -> bool:
        try:
            return await self.client.ping()
        except Exception:
            return False

    async def get_recent_failed_logins(self, minutes: int = 5) -> List[Dict[str, Any]]:
        """
        Poll ES for SSH brute force patterns.
        """
        try:
            query = {
                "query": {
                    "bool": {
                        "must": [
                            {"match_phrase": {"message": "Failed password"}},
                            {"range": {"@timestamp": {"gte": f"now-{minutes}m"}}}
                        ]
                    }
                },
                "size": 1000
            }
            # Catching NotFoundError for the mockup if index does not exist
            response = await self.client.search(index="logs-*", body=query, ignore_unavailable=True)
            return [hit["_source"] for hit in response.get("hits", {}).get("hits", [])]
        except (ConnectionError, NotFoundError) as e:
            logger.error(f"ES Connection Error: {e}. Returning empty list for simulation.")
            return []
        except Exception as e:
            logger.error(f"Error querying ES: {e}")
            return []
            
    async def get_logs_for_ip(self, ip_address: str, hours: int = 24) -> List[Dict[str, Any]]:
        """
        Retrieve logs associated with a specific IP over the last N hours.
        """
        try:
            query = {
                "query": {
                    "bool": {
                        "should": [
                            {"match": {"source_ip": ip_address}},
                            {"match": {"message": ip_address}}
                        ],
                        "minimum_should_match": 1,
                        "filter": [
                            {"range": {"@timestamp": {"gte": f"now-{hours}h"}}}
                        ]
                    }
                },
                "size": 500
            }
            response = await self.client.search(index="logs-*", body=query, ignore_unavailable=True)
            return [hit["_source"] for hit in response.get("hits", {}).get("hits", [])]
        except Exception as e:
            logger.warning(f"Error getting logs for IP {ip_address}: {e}")
            return []

    async def index_document(self, index: str, document: Dict[str, Any], doc_id: Optional[str] = None):
        """
        Index a generic document.
        """
        try:
            if doc_id:
                await self.client.index(index=index, id=doc_id, body=document)
            else:
                await self.client.index(index=index, body=document)
        except Exception as e:
            logger.error(f"Failed to index document in {index}: {e}")
            
    async def get_document(self, index: str, doc_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = await self.client.get(index=index, id=doc_id)
            return response.get("_source")
        except NotFoundError:
            return None
        except Exception as e:
            logger.error(f"Failed to get document {doc_id} from {index}: {e}")
            return None

es_service = ElasticService()
