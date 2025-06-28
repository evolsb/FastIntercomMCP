"""Performance optimization for API requests including batching, caching, and connection pooling."""

import asyncio
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Cache entry with expiration and metadata."""

    data: Any
    expires_at: datetime
    created_at: datetime
    hit_count: int = 0
    last_accessed: datetime = field(default_factory=datetime.now)
    size_bytes: int = 0


@dataclass
class OptimizationConfig:
    """Configuration for API optimization features."""

    # Connection pooling
    max_connections: int = 10
    max_keepalive_connections: int = 5
    keepalive_expiry: float = 30.0
    connection_timeout: float = 10.0
    read_timeout: float = 30.0

    # Request batching
    batch_enabled: bool = True
    max_batch_size: int = 50
    batch_timeout_seconds: float = 0.5
    batch_max_wait_seconds: float = 2.0

    # Caching
    cache_enabled: bool = True
    cache_max_size_mb: int = 50
    cache_default_ttl_seconds: int = 300  # 5 minutes
    cache_max_age_seconds: int = 3600  # 1 hour

    # Request optimization
    compression_enabled: bool = True
    persistent_connections: bool = True
    request_deduplication: bool = True

    # Performance monitoring
    metrics_enabled: bool = True
    slow_request_threshold_seconds: float = 5.0


@dataclass
class PerformanceMetrics:
    """Performance metrics for optimization tracking."""

    total_requests: int = 0
    cached_responses: int = 0
    batched_requests: int = 0
    deduplicated_requests: int = 0
    total_response_time_seconds: float = 0.0
    slowest_request_seconds: float = 0.0
    fastest_request_seconds: float = float("inf")
    avg_response_time_seconds: float = 0.0
    cache_hit_ratio: float = 0.0
    batch_efficiency: float = 0.0
    last_updated: datetime = field(default_factory=datetime.now)


class APICache:
    """Intelligent caching for API responses."""

    def __init__(self, config: OptimizationConfig):
        self.config = config
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._current_size_bytes = 0
        self._max_size_bytes = config.cache_max_size_mb * 1024 * 1024

    def get(self, key: str) -> Any | None:
        """Get cached value if it exists and hasn't expired."""
        if not self.config.cache_enabled:
            return None

        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None

            # Check expiration
            if datetime.now() > entry.expires_at:
                self._remove_entry(key)
                return None

            # Update access info
            entry.hit_count += 1
            entry.last_accessed = datetime.now()

            # Move to end (most recently used)
            self._cache.move_to_end(key)

            return entry.data

    def put(self, key: str, data: Any, ttl_seconds: int | None = None) -> bool:
        """Put value in cache with optional TTL."""
        if not self.config.cache_enabled:
            return False

        ttl = ttl_seconds or self.config.cache_default_ttl_seconds
        expires_at = datetime.now() + timedelta(seconds=ttl)

        # Estimate size
        try:
            data_json = json.dumps(data, default=str)
            size_bytes = len(data_json.encode("utf-8"))
        except Exception:
            size_bytes = 1024  # Fallback estimate

        with self._lock:
            # Check if we need to make space
            while (
                self._current_size_bytes + size_bytes > self._max_size_bytes
                and len(self._cache) > 0
            ):
                self._evict_lru()

            # Add/update entry
            if key in self._cache:
                old_entry = self._cache[key]
                self._current_size_bytes -= old_entry.size_bytes

            entry = CacheEntry(
                data=data,
                expires_at=expires_at,
                created_at=datetime.now(),
                size_bytes=size_bytes,
            )

            self._cache[key] = entry
            self._current_size_bytes += size_bytes

            return True

    def invalidate(self, pattern: str = None):
        """Invalidate cache entries by pattern or all if no pattern."""
        with self._lock:
            if pattern is None:
                self._cache.clear()
                self._current_size_bytes = 0
            else:
                keys_to_remove = [key for key in self._cache if pattern in key]
                for key in keys_to_remove:
                    self._remove_entry(key)

    def _remove_entry(self, key: str):
        """Remove entry and update size tracking."""
        entry = self._cache.pop(key, None)
        if entry:
            self._current_size_bytes -= entry.size_bytes

    def _evict_lru(self):
        """Evict least recently used entry."""
        if self._cache:
            key = next(iter(self._cache))  # First item is LRU
            self._remove_entry(key)

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            total_hits = sum(entry.hit_count for entry in self._cache.values())
            total_entries = len(self._cache)

            return {
                "entries_count": total_entries,
                "size_bytes": self._current_size_bytes,
                "size_mb": round(self._current_size_bytes / (1024 * 1024), 2),
                "utilization_percentage": round(
                    (self._current_size_bytes / self._max_size_bytes) * 100, 1
                ),
                "total_hits": total_hits,
                "avg_hits_per_entry": round(total_hits / total_entries, 1)
                if total_entries > 0
                else 0,
            }


class RequestBatcher:
    """Batches similar requests for efficiency."""

    def __init__(self, config: OptimizationConfig):
        self.config = config
        self._pending_batches: dict[str, list] = {}
        self._batch_timers: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def add_request(
        self, batch_key: str, request_data: Any, callback: Callable[[list], Any]
    ) -> Any:
        """Add request to batch and return result when batch executes."""
        if not self.config.batch_enabled:
            # Execute immediately if batching disabled
            return await callback([request_data])

        async with self._lock:
            # Initialize batch if needed
            if batch_key not in self._pending_batches:
                self._pending_batches[batch_key] = []
                self._schedule_batch_execution(batch_key, callback)

            # Add request to batch
            self._pending_batches[batch_key].append(request_data)

            # Execute immediately if batch is full
            if len(self._pending_batches[batch_key]) >= self.config.max_batch_size:
                return await self._execute_batch(batch_key, callback)

        # Wait for batch execution
        return await self._wait_for_batch_result(batch_key, request_data)

    def _schedule_batch_execution(self, batch_key: str, callback: Callable):
        """Schedule batch execution after timeout."""

        async def execute_after_timeout():
            await asyncio.sleep(self.config.batch_timeout_seconds)
            async with self._lock:
                if batch_key in self._pending_batches:
                    await self._execute_batch(batch_key, callback)

        task = asyncio.create_task(execute_after_timeout())
        self._batch_timers[batch_key] = task

    async def _execute_batch(self, batch_key: str, callback: Callable) -> Any:
        """Execute a batch of requests."""
        batch_requests = self._pending_batches.pop(batch_key, [])

        # Cancel timer if exists
        timer = self._batch_timers.pop(batch_key, None)
        if timer and not timer.done():
            timer.cancel()

        if not batch_requests:
            return None

        # Execute batch
        logger.debug(f"Executing batch {batch_key} with {len(batch_requests)} requests")
        return await callback(batch_requests)

    async def _wait_for_batch_result(self, batch_key: str, request_data: Any) -> Any:
        """Wait for batch execution and extract result for specific request."""
        # This is a simplified implementation
        # In practice, you'd need more sophisticated result mapping
        max_wait = self.config.batch_max_wait_seconds
        waited = 0
        interval = 0.1

        while waited < max_wait:
            async with self._lock:
                if batch_key not in self._pending_batches:
                    # Batch was executed
                    break

            await asyncio.sleep(interval)
            waited += interval

        # Return the request data (simplified - real implementation would return actual result)
        return request_data


class ConnectionPool:
    """Optimized HTTP connection pool."""

    def __init__(self, config: OptimizationConfig):
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with optimized settings."""
        async with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = self._create_optimized_client()
            return self._client

    def _create_optimized_client(self) -> httpx.AsyncClient:
        """Create HTTP client with performance optimizations."""
        # Connection limits
        limits = httpx.Limits(
            max_connections=self.config.max_connections,
            max_keepalive_connections=self.config.max_keepalive_connections,
            keepalive_expiry=self.config.keepalive_expiry,
        )

        # Timeouts
        timeout = httpx.Timeout(
            connect=self.config.connection_timeout,
            read=self.config.read_timeout,
            write=10.0,
            pool=5.0,
        )

        # Create client
        client = httpx.AsyncClient(
            limits=limits,
            timeout=timeout,
            http2=True,  # Enable HTTP/2 for better performance
            verify=True,
        )

        logger.info("Created optimized HTTP client with connection pooling")
        return client

    async def close(self):
        """Close the connection pool."""
        async with self._lock:
            if self._client and not self._client.is_closed:
                await self._client.aclose()
                self._client = None


class APIOptimizer:
    """Main optimization coordinator combining all optimization features."""

    def __init__(self, config: OptimizationConfig = None):
        self.config = config or OptimizationConfig()
        self.metrics = PerformanceMetrics()

        # Components
        self.cache = APICache(self.config)
        self.batcher = RequestBatcher(self.config)
        self.connection_pool = ConnectionPool(self.config)

        # Request deduplication
        self._in_flight_requests: dict[str, asyncio.Future] = {}
        self._dedup_lock = asyncio.Lock()

        # Performance monitoring
        self._request_times: list[float] = []
        self._metrics_lock = threading.Lock()

    async def make_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] = None,
        data: Any = None,
        cache_key: str = None,
        cache_ttl: int = None,
        priority: str = "normal",
        timeout: float = None,
    ) -> Any:
        """Make an optimized API request with caching, deduplication, etc.

        Args:
            method: HTTP method
            url: Request URL
            headers: Request headers
            data: Request data
            cache_key: Key for caching (if None, no caching)
            cache_ttl: Cache TTL in seconds
            priority: Request priority

        Returns:
            Response data
        """
        start_time = time.time()

        # Check cache first
        if cache_key and method.upper() == "GET":
            cached_result = self.cache.get(cache_key)
            if cached_result is not None:
                self._update_metrics(start_time, cached=True)
                logger.debug(f"Cache hit for {cache_key}")
                return cached_result

        # Request deduplication
        if self.config.request_deduplication and method.upper() == "GET":
            dedup_key = self._create_dedup_key(method, url, headers, data)

            async with self._dedup_lock:
                if dedup_key in self._in_flight_requests:
                    logger.debug(f"Deduplicating request {dedup_key}")
                    self.metrics.deduplicated_requests += 1
                    return await self._in_flight_requests[dedup_key]

                # Create future for this request
                future = asyncio.Future()
                self._in_flight_requests[dedup_key] = future

        try:
            # Make the actual request
            client = await self.connection_pool.get_client()

            request_kwargs = {"method": method, "url": url, "headers": headers or {}}

            if data is not None:
                if method.upper() in ["POST", "PUT", "PATCH"]:
                    request_kwargs["json"] = data
                else:
                    request_kwargs["params"] = data

            if timeout is not None:
                request_kwargs["timeout"] = timeout

            response = await client.request(**request_kwargs)
            response.raise_for_status()

            result = response.json()

            # Cache the result if requested
            if cache_key and method.upper() == "GET":
                self.cache.put(cache_key, result, cache_ttl)

            # Update metrics
            self._update_metrics(start_time, cached=False)

            # Complete deduplication future
            if self.config.request_deduplication and method.upper() == "GET":
                async with self._dedup_lock:
                    if dedup_key in self._in_flight_requests:
                        future = self._in_flight_requests.pop(dedup_key)
                        if not future.done():
                            future.set_result(result)

            return result

        except Exception as e:
            # Handle deduplication future on error
            if self.config.request_deduplication and method.upper() == "GET":
                async with self._dedup_lock:
                    if dedup_key in self._in_flight_requests:
                        future = self._in_flight_requests.pop(dedup_key)
                        if not future.done():
                            future.set_exception(e)
            raise

    def _create_dedup_key(
        self, method: str, url: str, headers: dict[str, str] = None, data: Any = None
    ) -> str:
        """Create a key for request deduplication."""
        key_parts = [method.upper(), url]

        if headers:
            # Include relevant headers (excluding auth tokens, timestamps, etc.)
            relevant_headers = {
                k: v
                for k, v in headers.items()
                if k.lower() not in ["authorization", "user-agent", "x-request-id"]
            }
            if relevant_headers:
                key_parts.append(json.dumps(relevant_headers, sort_keys=True))

        if data:
            key_parts.append(json.dumps(data, sort_keys=True, default=str))

        key_string = "|".join(key_parts)
        return hashlib.md5(key_string.encode()).hexdigest()

    def _update_metrics(self, start_time: float, cached: bool = False):
        """Update performance metrics."""
        response_time = time.time() - start_time

        with self._metrics_lock:
            self.metrics.total_requests += 1

            if cached:
                self.metrics.cached_responses += 1
            else:
                self.metrics.total_response_time_seconds += response_time
                self.metrics.slowest_request_seconds = max(
                    self.metrics.slowest_request_seconds, response_time
                )
                self.metrics.fastest_request_seconds = min(
                    self.metrics.fastest_request_seconds, response_time
                )

            # Calculate averages
            non_cached_requests = (
                self.metrics.total_requests - self.metrics.cached_responses
            )
            if non_cached_requests > 0:
                self.metrics.avg_response_time_seconds = (
                    self.metrics.total_response_time_seconds / non_cached_requests
                )

            self.metrics.cache_hit_ratio = (
                (self.metrics.cached_responses / self.metrics.total_requests)
                if self.metrics.total_requests > 0
                else 0.0
            )

            self.metrics.last_updated = datetime.now()

    def get_performance_stats(self) -> dict[str, Any]:
        """Get comprehensive performance statistics."""
        cache_stats = self.cache.get_stats()

        return {
            "requests": {
                "total": self.metrics.total_requests,
                "cached": self.metrics.cached_responses,
                "batched": self.metrics.batched_requests,
                "deduplicated": self.metrics.deduplicated_requests,
            },
            "performance": {
                "avg_response_time_seconds": round(
                    self.metrics.avg_response_time_seconds, 3
                ),
                "fastest_request_seconds": round(
                    self.metrics.fastest_request_seconds, 3
                )
                if self.metrics.fastest_request_seconds != float("inf")
                else 0,
                "slowest_request_seconds": round(
                    self.metrics.slowest_request_seconds, 3
                ),
                "cache_hit_ratio": round(self.metrics.cache_hit_ratio, 3),
            },
            "cache": cache_stats,
            "optimizations": {
                "connection_pooling": self.config.persistent_connections,
                "request_batching": self.config.batch_enabled,
                "request_deduplication": self.config.request_deduplication,
                "compression": self.config.compression_enabled,
            },
            "recommendations": self._generate_performance_recommendations(),
        }

    def _generate_performance_recommendations(self) -> list[str]:
        """Generate performance optimization recommendations."""
        recommendations = []

        if self.metrics.cache_hit_ratio < 0.3 and self.metrics.total_requests > 100:
            recommendations.append(
                "Low cache hit ratio - consider increasing cache TTL or size"
            )

        if (
            self.metrics.avg_response_time_seconds
            > self.config.slow_request_threshold_seconds
        ):
            recommendations.append(
                "High average response time - check network or API performance"
            )

        if self.metrics.deduplicated_requests > self.metrics.total_requests * 0.1:
            recommendations.append(
                "High request deduplication - consider request optimization"
            )

        cache_stats = self.cache.get_stats()
        if cache_stats["utilization_percentage"] > 90:
            recommendations.append(
                "Cache near capacity - consider increasing cache size"
            )

        return recommendations

    async def close(self):
        """Clean up resources."""
        await self.connection_pool.close()
