"""
Terrain Service - FastAPI Application
Validates terrain safety and pathfinding for Exodus Rush
"""
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import httpx
from terrain import TerrainManager, ValidationRequest, UpdateRequest

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
SEA_STATE_SERVICE_URL = os.getenv("SEA_STATE_SERVICE_URL", "http://sea-state-service:8080")
REDIS_HOST = os.getenv("REDIS_HOST", "redis-cache")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))  # 5 minutes default

# Global terrain manager
terrain_manager = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle"""
    global terrain_manager

    # Startup
    logger.info("Starting terrain-service...")
    logger.info(f"Sea State Service URL: {SEA_STATE_SERVICE_URL}")
    logger.info(f"Redis: {REDIS_HOST}:{REDIS_PORT}")

    terrain_manager = TerrainManager(
        redis_host=REDIS_HOST,
        redis_port=REDIS_PORT,
        cache_ttl=CACHE_TTL
    )

    try:
        await terrain_manager.initialize()
        logger.info("Terrain service initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize terrain service: {e}")
        # Continue anyway - can work without Redis

    yield

    # Shutdown
    logger.info("Shutting down terrain-service...")
    await terrain_manager.close()


app = FastAPI(
    title="Terrain Service",
    description="Validates terrain safety and pathfinding for character movement",
    version="1.0.0",
    lifespan=lifespan
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)}
    )


@app.get("/health")
async def health_check():
    """Health check endpoint for Kubernetes probes"""
    try:
        redis_status = "connected" if terrain_manager.redis_available else "unavailable"
        return {
            "status": "healthy",
            "service": "terrain-service",
            "redis": redis_status,
            "version": "1.0.0"
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Service unhealthy")


@app.post("/validate")
async def validate_path(request: ValidationRequest):
    """
    Validate if a path is walkable

    Checks:
    1. Basic terrain validation (land vs water)
    2. Sea state from sea-state-service (if path crosses Red Sea)
    3. Path continuity and obstacles
    """
    logger.info(f"Validating path for character {request.character_id}: {request.path}")

    try:
        # Check if path crosses the Red Sea (coordinates around x=50)
        crosses_sea = any(40 <= pos.x <= 60 for pos in request.path)

        if crosses_sea:
            logger.info("Path crosses Red Sea - checking sea state")

            # Query sea-state-service
            async with httpx.AsyncClient(timeout=5.0) as client:
                try:
                    response = await client.get(f"{SEA_STATE_SERVICE_URL}/status")
                    response.raise_for_status()
                    sea_state = response.json()

                    logger.info(f"Sea state response: {sea_state}")

                    # Check if sea is split/walkable
                    if sea_state.get("red_sea") != "split":
                        return {
                            "valid": False,
                            "reason": "Red Sea is not split - path is underwater",
                            "sea_state": sea_state.get("red_sea", "closed"),
                            "character_id": request.character_id
                        }

                    logger.info("Sea is split - path is valid")

                except httpx.RequestError as e:
                    logger.error(f"Failed to check sea state: {e}")
                    # Fail safe - don't allow crossing if we can't verify
                    return {
                        "valid": False,
                        "reason": "Cannot verify sea state - service unavailable",
                        "character_id": request.character_id
                    }

        # Validate terrain (basic check)
        is_valid = await terrain_manager.validate_path(request.path)

        if not is_valid:
            return {
                "valid": False,
                "reason": "Path contains obstacles or invalid terrain",
                "character_id": request.character_id
            }

        # Path is valid
        return {
            "valid": True,
            "reason": "Path is clear and walkable",
            "character_id": request.character_id,
            "crosses_sea": crosses_sea
        }

    except Exception as e:
        logger.error(f"Validation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Validation failed: {str(e)}")


@app.get("/map")
async def get_terrain_map(x: int = 0, y: int = 0, width: int = 100, height: int = 100):
    """
    Return terrain map for specified region

    Uses caching for frequently accessed regions
    """
    logger.info(f"Fetching terrain map: x={x}, y={y}, width={width}, height={height}")

    try:
        terrain_data = await terrain_manager.get_map(x, y, width, height)

        return {
            "map": terrain_data,
            "dimensions": {
                "x": x,
                "y": y,
                "width": width,
                "height": height
            },
            "legend": {
                "L": "Land (walkable)",
                "W": "Water (not walkable)",
                "S": "Red Sea (walkable when split)",
                "M": "Mountain (not walkable)"
            }
        }
    except Exception as e:
        logger.error(f"Failed to fetch map: {e}")
        raise HTTPException(status_code=500, detail=f"Map fetch failed: {str(e)}")


@app.post("/update")
async def update_terrain(request: UpdateRequest):
    """
    Update terrain state

    Triggered by sea state changes - updates whether Red Sea path is walkable
    """
    logger.info(f"Terrain update requested: {request.dict()}")

    try:
        # Update terrain based on sea state
        await terrain_manager.update_sea_state(request.sea_state)

        # Invalidate relevant caches
        await terrain_manager.invalidate_cache()

        return {
            "status": "updated",
            "sea_state": request.sea_state,
            "timestamp": request.timestamp or "now"
        }
    except Exception as e:
        logger.error(f"Terrain update failed: {e}")
        raise HTTPException(status_code=500, detail=f"Update failed: {str(e)}")


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "terrain-service",
        "version": "1.0.0",
        "description": "Validates terrain safety and pathfinding for character movement",
        "endpoints": [
            "POST /validate - Validate if path is walkable",
            "GET /map - Get terrain map",
            "POST /update - Update terrain state",
            "GET /health - Health check"
        ]
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8082"))
    uvicorn.run(app, host="0.0.0.0", port=port)
