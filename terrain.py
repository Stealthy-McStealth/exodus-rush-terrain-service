"""
Terrain Logic Module
Handles terrain data, validation, and caching
"""
import json
import logging
from typing import List, Optional
from pydantic import BaseModel, Field
import redis.asyncio as redis

logger = logging.getLogger(__name__)


class Position(BaseModel):
    """2D position coordinate"""
    x: int = Field(..., ge=0, description="X coordinate")
    y: int = Field(..., ge=0, description="Y coordinate")


class ValidationRequest(BaseModel):
    """Path validation request"""
    character_id: str = Field(..., description="Character identifier")
    path: List[Position] = Field(..., min_length=1, description="List of positions forming the path")


class UpdateRequest(BaseModel):
    """Terrain update request"""
    sea_state: str = Field(..., description="Sea state: closed, splitting, split")
    timestamp: Optional[str] = None


class TerrainManager:
    """Manages terrain data and validation logic"""

    def __init__(self, redis_host: str = "redis-cache", redis_port: int = 6379, cache_ttl: int = 300):
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.cache_ttl = cache_ttl
        self.redis_client: Optional[redis.Redis] = None
        self.redis_available = False
        self.sea_state = "closed"  # Default state

        # Initialize terrain map (simple 2D grid)
        # 100x100 grid: L=Land, W=Water, S=Red Sea, M=Mountain
        self.terrain_grid = self._initialize_terrain()

    def _initialize_terrain(self) -> List[List[str]]:
        """
        Initialize a simple terrain grid

        Layout:
        - Left side (x<40): Land (Egypt)
        - Middle (40<=x<=60): Red Sea
        - Right side (x>60): Land (Promised Land)
        - Some mountains scattered
        """
        grid = []
        for y in range(100):
            row = []
            for x in range(100):
                if 40 <= x <= 60:
                    # Red Sea region
                    row.append("S")
                elif x < 20 or x > 80:
                    # Far edges - some mountains
                    if (x + y) % 13 == 0:
                        row.append("M")
                    else:
                        row.append("L")
                else:
                    # Land regions
                    row.append("L")
            grid.append(row)
        return grid

    async def initialize(self):
        """Initialize Redis connection"""
        try:
            self.redis_client = redis.Redis(
                host=self.redis_host,
                port=self.redis_port,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2
            )
            # Test connection
            await self.redis_client.ping()
            self.redis_available = True
            logger.info(f"Redis connection established: {self.redis_host}:{self.redis_port}")
        except Exception as e:
            logger.warning(f"Redis unavailable: {e}. Running without cache.")
            self.redis_available = False
            self.redis_client = None

    async def close(self):
        """Close Redis connection"""
        if self.redis_client:
            await self.redis_client.close()
            logger.info("Redis connection closed")

    async def get_from_cache(self, key: str) -> Optional[str]:
        """Get value from Redis cache"""
        if not self.redis_available:
            return None
        try:
            value = await self.redis_client.get(key)
            if value:
                logger.debug(f"Cache hit: {key}")
            return value
        except Exception as e:
            logger.warning(f"Cache read error: {e}")
            return None

    async def set_in_cache(self, key: str, value: str):
        """Set value in Redis cache"""
        if not self.redis_available:
            return
        try:
            await self.redis_client.setex(key, self.cache_ttl, value)
            logger.debug(f"Cache set: {key}")
        except Exception as e:
            logger.warning(f"Cache write error: {e}")

    async def invalidate_cache(self):
        """Invalidate terrain-related caches"""
        if not self.redis_available:
            return
        try:
            # Delete all terrain map caches
            keys = await self.redis_client.keys("terrain:map:*")
            if keys:
                await self.redis_client.delete(*keys)
                logger.info(f"Invalidated {len(keys)} cache entries")
        except Exception as e:
            logger.warning(f"Cache invalidation error: {e}")

    async def validate_path(self, path: List[Position]) -> bool:
        """
        Validate if path is walkable

        Checks:
        1. All positions are within bounds
        2. No obstacles (mountains, water when sea not split)
        3. Path is continuous (adjacent cells)
        """
        if not path:
            return False

        for i, pos in enumerate(path):
            # Check bounds
            if pos.x < 0 or pos.x >= 100 or pos.y < 0 or pos.y >= 100:
                logger.warning(f"Position out of bounds: {pos}")
                return False

            # Check terrain type
            terrain_type = self.terrain_grid[pos.y][pos.x]

            if terrain_type == "M":
                # Mountain - never walkable
                logger.warning(f"Path blocked by mountain at {pos}")
                return False

            if terrain_type == "W":
                # Water - not walkable
                logger.warning(f"Path blocked by water at {pos}")
                return False

            if terrain_type == "S":
                # Red Sea - walkable only if split (checked by caller)
                # This validation assumes caller has verified sea state
                pass

            # Check continuity (positions should be adjacent)
            if i > 0:
                prev_pos = path[i - 1]
                dx = abs(pos.x - prev_pos.x)
                dy = abs(pos.y - prev_pos.y)
                # Allow diagonal movement, max distance 1
                if dx > 1 or dy > 1:
                    logger.warning(f"Path not continuous: {prev_pos} -> {pos}")
                    return False

        return True

    async def get_map(self, x: int, y: int, width: int, height: int) -> List[List[str]]:
        """
        Get terrain map for region

        Uses caching for performance
        """
        cache_key = f"terrain:map:{x}:{y}:{width}:{height}"

        # Try cache first
        cached = await self.get_from_cache(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except:
                pass

        # Generate map data
        map_data = []
        for row_idx in range(y, min(y + height, 100)):
            row = []
            for col_idx in range(x, min(x + width, 100)):
                if row_idx < 0 or col_idx < 0 or row_idx >= 100 or col_idx >= 100:
                    row.append("X")  # Out of bounds
                else:
                    terrain_type = self.terrain_grid[row_idx][col_idx]
                    # If Red Sea and sea is split, show as walkable
                    if terrain_type == "S" and self.sea_state == "split":
                        row.append("L")  # Show as land when split
                    else:
                        row.append(terrain_type)
            map_data.append(row)

        # Cache result
        await self.set_in_cache(cache_key, json.dumps(map_data))

        return map_data

    async def update_sea_state(self, new_state: str):
        """Update internal sea state tracking"""
        old_state = self.sea_state
        self.sea_state = new_state
        logger.info(f"Sea state updated: {old_state} -> {new_state}")

        # Store in Redis if available
        if self.redis_available:
            try:
                await self.redis_client.set("terrain:sea_state", new_state)
            except Exception as e:
                logger.warning(f"Failed to store sea state in Redis: {e}")
