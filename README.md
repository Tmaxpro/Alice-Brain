# ALICE Brain

Backend FastAPI pour orchestrer des agents SOC (détection, investigation, réponse, reporting), piloter les incidents et exposer des APIs + WebSockets en temps réel.

## Fonctionnalités principales

- API REST pour incidents, actions et gestion des agents.
- WebSocket pour push temps réel des incidents et communication agent <-> brain.
- Polling de détection périodique via APScheduler.
- Intégration Elasticsearch pour stockage et recherche.
- Documentation OpenAPI/Swagger intégrée.

## Stack technique

- Python 3.11+
- FastAPI + Uvicorn
- Elasticsearch 8.x
- APScheduler
- Docker / Docker Compose (optionnel)

## Prérequis

- Python `3.11` ou supérieur.
- `pip` installé.
- Un accès Elasticsearch (local ou via Docker Compose).
- Une clé NVIDIA NIM (`NVIDIA_API_KEY`) obligatoire.
- (Optionnel) clé Anthropic pour fallback LLM.

## Installation locale

1. Cloner le dépôt puis se placer dans le dossier du projet.
2. Créer un environnement virtuel :

```bash
python -m venv .venv
```

3. Activer l'environnement :

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1

# Linux / macOS
source .venv/bin/activate
```

4. Installer les dépendances :

```bash
pip install -r requirements.txt
```

## Configuration

1. Copier le fichier d'exemple :

```bash
cp .env.example .env
```

Sous Windows PowerShell :

```powershell
Copy-Item .env.example .env
```

2. Renseigner au minimum dans `.env` :

- `NVIDIA_API_KEY` (obligatoire)
- `ES_URL` (par défaut `http://localhost:9200` en local, `http://elasticsearch:9200` via Docker)

Variables utiles supplémentaires :

- `ANTHROPIC_API_KEY` (fallback LLM)
- `ALICE_SIMULATION_MODE` (`true`/`false`)
- `DETECTION_POLL_INTERVAL`
- `DEDUP_WINDOW_MINUTES`
- `ABUSEIPDB_KEY` (enrichissement optionnel)

## Lancer le projet (local)

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

API disponible sur : [http://localhost:8000](http://localhost:8000)

## Documentation API (Swagger / OpenAPI)

- Swagger UI : [http://localhost:8000/swagger](http://localhost:8000/swagger)
- ReDoc : [http://localhost:8000/redoc](http://localhost:8000/redoc)
- OpenAPI JSON : [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

La route racine `/` redirige automatiquement vers Swagger.

## Lancer avec Docker Compose

Le projet contient un `docker-compose.yml` qui démarre :

- `elasticsearch` (port `9200`)
- `kibana` (port `5601`)
- `alice-brain` (port `8000`)

Commande :

```bash
docker compose up --build
```

## Endpoints utiles

- `GET /api/health` : état de l'application, Elasticsearch et agents.
- `GET /swagger` : interface interactive pour tester les routes.
- `WS /ws/incidents` : flux incidents temps réel.
- `WS /ws/agent/{agent_id}` : canal WebSocket agent dédié.

## Structure rapide du projet

- `main.py` : point d'entrée FastAPI, lifecycle et routing.
- `api/` : routes REST et WebSocket.
- `agents/` : logique des agents SOC (détection, orchestration, etc.).
- `services/` : services techniques (Elasticsearch, registre d'agents, communication).
- `models/` : modèles de données.

## Notes

- Des fichiers `__pycache__` peuvent apparaître localement pendant l'exécution Python. Ils ne sont pas nécessaires au fonctionnement du projet.
- Pour un usage production, désactiver `--reload`, durcir la configuration CORS et sécuriser Elasticsearch.
