"""Simplified rate limiter module with stub implementations."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting - simplified stub."""
    
    max_requests_per_second: float = 5.0
    burst_size: int = 10
    adaptive_learning: bool = False


class AdaptiveRateLimiter:
    """Simplified rate limiter - stub implementation."""
    
    def __init__(self, config: RateLimitConfig | None = None):
        self.config = config or RateLimitConfig()
        self._performance_callbacks: list[Callable] = []
        self._last_request_time = 0.0
        logger.debug("AdaptiveRateLimiter initialized with simplified implementation")
    
    async def acquire(self, priority: str = "normal"):
        """Simple rate limiting - just add a small delay."""
        import time
        
        current_time = time.time()
        min_interval = 1.0 / self.config.max_requests_per_second
        
        if current_time - self._last_request_time < min_interval:
            delay = min_interval - (current_time - self._last_request_time)
            logger.debug(f"Rate limiting: waiting {delay:.2f}s")
            await asyncio.sleep(delay)
        
        self._last_request_time = time.time()
    
    def report_successful_request(self):
        """Report successful request - stub implementation."""
        logger.debug("Request successful (simplified rate limiter)")
    
    def report_rate_limit_hit(self, retry_after: float | None = None):
        """Report rate limit hit - stub implementation."""
        if retry_after:
            logger.warning(f"Rate limit hit - server requested {retry_after}s delay")
        else:
            logger.warning("Rate limit hit - using default backoff")
    
    def add_performance_callback(self, callback: Callable):
        """Add performance callback - stub implementation."""
        self._performance_callbacks.append(callback)
        logger.debug("Performance callback added (simplified mode)")
    
    def get_stats(self) -> dict[str, Any]:
        """Return stub rate limiting stats."""
        return {
            "status": "simplified_mode",
            "adaptive_learning": False,
            "max_requests_per_second": self.config.max_requests_per_second,
            "performance": {
                "efficiency_percentage": 100.0,
                "total_requests": 0,
                "rate_limits_hit": 0,
            },
            "recommendations": [
                "Running in simplified mode - adaptive rate limiting not available"
            ],
        }