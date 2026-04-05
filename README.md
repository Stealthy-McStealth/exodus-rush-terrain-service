# Terrain Service

Validates terrain safety and pathfinding for character movement in Exodus Rush.

## Overview

The terrain service is responsible for:
- Validating character movement paths
- Checking terrain walkability (land, water, mountains)
- Coordinating with sea-state-service for Red Sea crossing validation
- Caching terrain data for performance

## Technology Stack

- **Framework:** FastAPI (Python)
- **Runtime:** Python 3.11
- **Server:** Uvicorn
- **Cache:** Redis (optional)

## Architecture

The service runs as 2 replicas in Kubernetes for high availability. It integrates with:
- **sea-state-service** (port 8080) - Checks if Red Sea is split
- **redis-cache** (port 6379) - Caches terrain maps

## API Endpoints

### POST /validate
Validates if a character's path is walkable.

**Request:**
```json
{
  "character_id": "moses_123",
  "path": [
    {"x": 30, "y": 50},
    {"x": 31, "y": 50},
    {"x": 50, "y": 50}
  ]
}
```

**Response:**
```json
{
  "valid": true,
  "reason": "Path is clear and walkable",
  "character_id": "moses_123",
  "crosses_sea": true
}
```

### GET /map
Returns terrain map for a specified region.

**Parameters:**
- `x` - X coordinate (default: 0)
- `y` - Y coordinate (default: 0)
- `width` - Map width (default: 100)
- `height` - Map height (default: 100)

**Response:**
```json
{
  "map": [
    ["L", "L", "S", "L"],
    ["L", "M", "S", "L"]
  ],
  "dimensions": {
    "x": 0,
    "y": 0,
    "width": 100,
    "height": 100
  },
  "legend": {
    "L": "Land (walkable)",
    "W": "Water (not walkable)",
    "S": "Red Sea (walkable when split)",
    "M": "Mountain (not walkable)"
  }
}
```

### POST /update
Updates terrain state based on sea state changes.

**Request:**
```json
{
  "sea_state": "split",
  "timestamp": "2024-04-05T10:30:00Z"
}
```

**Response:**
```json
{
  "status": "updated",
  "sea_state": "split",
  "timestamp": "2024-04-05T10:30:00Z"
}
```

### GET /health
Health check endpoint for Kubernetes probes.

**Response:**
```json
{
  "status": "healthy",
  "service": "terrain-service",
  "redis": "connected",
  "version": "1.0.0"
}
```

## Configuration

Environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | Service port | 8082 |
| `SEA_STATE_SERVICE_URL` | Sea state service URL | http://sea-state-service:8080 |
| `REDIS_HOST` | Redis host | redis-cache |
| `REDIS_PORT` | Redis port | 6379 |
| `CACHE_TTL` | Cache TTL in seconds | 300 |
| `LOG_LEVEL` | Logging level | INFO |

## Local Development

### Prerequisites
- Python 3.11+
- Redis (optional, for caching)

### Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Run the service
python main.py
```

The service will start on http://localhost:8082

### Testing
```bash
# Test health endpoint
curl http://localhost:8082/health

# Test path validation
curl -X POST http://localhost:8082/validate \
  -H "Content-Type: application/json" \
  -d '{
    "character_id": "test",
    "path": [{"x": 10, "y": 10}, {"x": 11, "y": 10}]
  }'

# Get terrain map
curl "http://localhost:8082/map?x=0&y=0&width=10&height=10"
```

## Docker

### Build
```bash
docker build -t stealthymcstelath/exodus-rush-terrain-service:latest .
```

### Run
```bash
docker run -p 8082:8082 \
  -e SEA_STATE_SERVICE_URL=http://sea-state-service:8080 \
  -e REDIS_HOST=redis-cache \
  stealthymcstelath/exodus-rush-terrain-service:latest
```

## Kubernetes Deployment

### Deploy
```bash
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

### Verify
```bash
# Check pods
kubectl get pods -n passover -l app=terrain-service

# Check service
kubectl get svc -n passover terrain-service

# View logs
kubectl logs -n passover -l app=terrain-service --tail=100
```

### Scale
```bash
# Scale to 3 replicas
kubectl scale deployment terrain-service -n passover --replicas=3
```

## Terrain Grid Layout

The service uses a 100x100 grid:

- **x < 40:** Land (Egypt) - Left side
- **40 ≤ x ≤ 60:** Red Sea - Middle section
- **x > 60:** Land (Promised Land) - Right side
- **Mountains:** Scattered obstacles (not walkable)

When the Red Sea is **split** (via sea-state-service), the path through coordinates x=40-60 becomes walkable.

## Caching Strategy

The service uses Redis to cache:
- Terrain map queries (5-minute TTL)
- Sea state information
- Frequently accessed regions

Cache is invalidated when terrain updates occur (e.g., sea state changes).

## Error Handling

The service includes comprehensive error handling:
- Sea state service unavailable → Fail safe (don't allow crossing)
- Redis unavailable → Continue without caching
- Invalid coordinates → Return validation error
- Malformed requests → HTTP 400 with details

## Performance

- **Response time:** < 50ms (cached)
- **Response time:** < 200ms (uncached)
- **Throughput:** ~500 req/sec per replica
- **Memory usage:** ~128MB per pod
- **CPU usage:** ~0.1 cores per pod

## Monitoring

Key metrics to monitor:
- Request rate and latency
- Cache hit/miss ratio
- Sea state service call failures
- Redis connection status
- Pod health and restarts

## Related Services

- [sea-state-service](../sea-state-service) - Sea splitting state
- [character-service](../character-service) - Character movement
- [api-gateway](../api-gateway) - Request routing

## License

