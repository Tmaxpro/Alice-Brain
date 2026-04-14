"""
ALICE Brain — Configuration centralisée via pydantic-settings.
Charge automatiquement le fichier .env à la racine du projet.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── LLM Principal — MiniMax via NVIDIA NIM (OBLIGATOIRE) ──
    NVIDIA_API_KEY: str

    # ── LLM Fallback — Claude (OPTIONNEL) ──
    ANTHROPIC_API_KEY: str | None = None

    # ── Elasticsearch ──
    ES_URL: str = "http://localhost:9200"
    ES_USER: str | None = None
    ES_PASSWORD: str | None = None
    ES_VERIFY_CERTS: bool = True
    ES_INDEX_LOGS: str = "logs-*"
    ES_INDEX_INCIDENTS: str = "alice-incidents"
    ES_INDEX_ACTIONS: str = "alice-actions"
    ES_INDEX_REPORTS: str = "alice-reports"
    ES_INDEX_AGENTS: str = "alice-agents"

    # ── Agents client-side (DEPRECATED — utiliser le registre dynamique) ──
    ENDPOINT_AGENT_URL: str = "http://localhost:8001"
    NETWORK_AGENT_URL: str = "http://localhost:8002"
    NOTIF_AGENT_URL: str = "http://localhost:8004"

    # ── Brain WebSocket ──
    BRAIN_WS_HOST: str = "0.0.0.0"       # Adresse exposée aux agents pour le WS

    # ── Enrichissement ──
    ABUSEIPDB_KEY: str | None = None

    # ── Comportement ──
    ALICE_SIMULATION_MODE: bool = True
    DETECTION_POLL_INTERVAL: int = 30  # secondes
    DEDUP_WINDOW_MINUTES: int = 5

    # ── IPs protégées (jamais bloquées) ──
    PROTECTED_IPS: list[str] = ["127.0.0.1", "localhost"]

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
