import asyncio
import aiohttp
import json
import uuid
import sys
from datetime import datetime
from elasticsearch import AsyncElasticsearch

# Utilisation de aiohttp ou httpx pour trigger via API ou ES direct
# Nous allons injecter dans ES de faux logs.

ES_URL = "http://localhost:9200"
INDEX = "logs-auth"

async def mock_failed_logins(client, ip, target_host, count=10):
    print(f"Injecting {count} failed logins from {ip} to {target_host}...")
    for i in range(count):
        doc = {
            "@timestamp": datetime.utcnow().isoformat(),
            "message": "Failed password for root",
            "source_ip": ip,
            "host": {
                "name": target_host
            },
            "service": "sshd"
        }
        await client.index(index=INDEX, body=doc)
        print(f"Inserted doc {i+1}/{count}")

async def main():
    client = AsyncElasticsearch(ES_URL)
    
    try:
        if not await client.ping():
            print("Cannot connect to Elasticsearch at", ES_URL)
            sys.exit(1)
            
        print("Connected to Elasticsearch. Generating synthetic brute-force logs...")
        malicious_ip = "185.15.20.10"
        target_server = "prod-db-01"
        
        await mock_failed_logins(client, malicious_ip, target_server, count=8)
        
        print("Injection done. Wait up to 30 seconds for ALICE Brain Detection Agent to pick it up.")
    except Exception as e:
        print("Error:", e)
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
