import httpx
from typing import Dict, Any, Optional
import logging
from config import settings

logger = logging.getLogger(__name__)

class AbuseIPDBClient:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or settings.ABUSEIPDB_KEY
        self.base_url = "https://api.abuseipdb.com/api/v2/check"
        
    async def check_ip(self, ip_address: str) -> Dict[str, Any]:
        """
        Check an IP address against the AbuseIPDB database.
        """
        if not self.api_key or self.api_key == "your_abuseipdb_api_key_here":
            logger.warning("No valid AbuseIPDB API key provided. Returning mock data.")
            return {
                "abuseConfidenceScore": 85 if ip_address.startswith("192.") else 10,
                "countryCode": "US",
                "usageType": "Data Center/Web Hosting/Transit",
                "domain": "mockdomain.com"
            }
            
        headers = {
            'Accept': 'application/json',
            'Key': self.api_key
        }
        
        params = {
            'ipAddress': ip_address,
            'maxAgeInDays': '90'
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.base_url, headers=headers, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                return data.get("data", {})
        except Exception as e:
            logger.error(f"Error connecting to AbuseIPDB for IP {ip_address}: {str(e)}")
            # Fallback for resilience
            return {
                "abuseConfidenceScore": 0,
                "error": str(e),
                "note": "Failed to reach AbuseIPDB"
            }

abuseipdb_client = AbuseIPDBClient()
