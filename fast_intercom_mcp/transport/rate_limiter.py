"""Intelligent rate limiting with backoff strategies for API optimization."""

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class BackoffStrategy(Enum):
    """Backoff strategy types."""

    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    FIBONACCI = "fibonacci"
    CUSTOM = "custom"


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""

    max_requests_per_window: int = 80  # Conservative limit for Intercom
    window_seconds: int = 10
    burst_limit: int = 20  # Allow bursts up to this many requests
    burst_window_seconds: int = 2
    backoff_strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    min_backoff_seconds: float = 0.1
    max_backoff_seconds: float = 60.0
    backoff_multiplier: float = 2.0
    jitter_enabled: bool = True
    adaptive_enabled: bool = True


@dataclass
class RateLimitMetrics:
    """Metrics for rate limiting performance."""

    total_requests: int = 0
    requests_delayed: int = 0
    total_delay_seconds: float = 0.0
    rate_limit_hits: int = 0
    backoff_events: int = 0
    avg_request_interval: float = 0.0
    current_rate_per_second: float = 0.0
    last_reset_time: datetime = field(default_factory=datetime.now)


class AdaptiveRateLimiter:
    """Intelligent rate limiter with adaptive backoff strategies."""

    def __init__(self, config: RateLimitConfig | None = None):
        self.config = config or RateLimitConfig()
        self.metrics = RateLimitMetrics()

        # Request tracking
        self._request_times: list[float] = []
        self._burst_request_times: list[float] = []
        self._lock = threading.Lock()

        # Backoff state
        self._consecutive_rate_limits = 0
        self._last_rate_limit_time: float | None = None
        self._current_backoff_seconds = self.config.min_backoff_seconds

        # Adaptive learning
        self._successful_request_intervals: list[float] = []
        self._last_adaptive_adjustment = time.time()
        self._adaptive_adjustment_interval = 300  # 5 minutes

        # Performance monitoring
        self._performance_callbacks: list[Callable[[RateLimitMetrics], None]] = []

    def add_performance_callback(self, callback: Callable[[RateLimitMetrics], None]) -> None:
        """Add a callback for performance monitoring."""
        self._performance_callbacks.append(callback)

    async def acquire(self, priority: str = "normal") -> float:
        """Acquire permission to make a request.

        Args:
            priority: Request priority ("high", "normal", "low")

        Returns:
            Delay time in seconds (0 if no delay needed)
        """
        time.time()
        delay_time = 0.0

        with self._lock:
            now = time.time()

            # Clean old request times
            self._clean_old_requests(now)

            # Check if we need to delay
            delay_time = self._calculate_delay(now, priority)

            if delay_time > 0:
                self.metrics.requests_delayed += 1
                self.metrics.total_delay_seconds += delay_time

        # Apply delay if needed
        if delay_time > 0:
            logger.debug(f"Rate limiting: delaying {delay_time:.2f}s")
            await asyncio.sleep(delay_time)

        # Record request
        with self._lock:
            request_time = time.time()
            self._request_times.append(request_time)
            self._burst_request_times.append(request_time)
            self.metrics.total_requests += 1

            # Update metrics
            self._update_metrics(request_time)

            # Adaptive learning
            if self._should_adapt():
                self._adapt_rate_limits()

        # Notify performance callbacks
        for callback in self._performance_callbacks:
            try:
                callback(self.metrics)
            except Exception as e:
                logger.warning(f"Performance callback failed: {e}")

        return delay_time

    def report_rate_limit_hit(self, retry_after_seconds: float | None = None):
        """Report that a rate limit was hit by the API.

        Args:
            retry_after_seconds: Server-suggested retry delay
        """
        with self._lock:
            self.metrics.rate_limit_hits += 1
            self._consecutive_rate_limits += 1
            self._last_rate_limit_time = time.time()

            # Increase backoff
            self._increase_backoff(retry_after_seconds)

            logger.warning(
                f"Rate limit hit (#{self._consecutive_rate_limits}), "
                f"backing off to {self._current_backoff_seconds:.2f}s"
            )

    def report_successful_request(self, response_time_seconds: float = 0.0):
        """Report a successful request to help with adaptive learning.

        Args:
            response_time_seconds: Time taken for the request
        """
        with self._lock:
            # Reset consecutive rate limits on success
            if self._consecutive_rate_limits > 0:
                logger.info(f"Rate limit cleared after {self._consecutive_rate_limits} hits")
                self._consecutive_rate_limits = 0
                self._current_backoff_seconds = self.config.min_backoff_seconds

            # Track successful intervals for adaptive learning
            now = time.time()
            if self._request_times:
                interval = now - self._request_times[-1]
                self._successful_request_intervals.append(interval)

                # Keep only recent intervals
                cutoff_time = now - 600  # 10 minutes
                self._successful_request_intervals = [
                    interval
                    for i, interval in enumerate(self._successful_request_intervals)
                    if now - (i * interval) > cutoff_time
                ]

    def _clean_old_requests(self, now: float):
        """Clean old request times outside the tracking windows."""
        # Clean main window
        cutoff_time = now - self.config.window_seconds
        self._request_times = [t for t in self._request_times if t > cutoff_time]

        # Clean burst window
        burst_cutoff_time = now - self.config.burst_window_seconds
        self._burst_request_times = [t for t in self._burst_request_times if t > burst_cutoff_time]

    def _calculate_delay(self, now: float, priority: str) -> float:
        """Calculate required delay before next request."""
        # Check burst limit first
        if len(self._burst_request_times) >= self.config.burst_limit:
            oldest_in_burst = self._burst_request_times[0]
            burst_delay = self.config.burst_window_seconds - (now - oldest_in_burst)
            if burst_delay > 0:
                return burst_delay + self._add_jitter(burst_delay * 0.1)

        # Check main rate limit
        if len(self._request_times) >= self.config.max_requests_per_window:
            oldest_request = self._request_times[0]
            window_delay = self.config.window_seconds - (now - oldest_request)
            if window_delay > 0:
                base_delay = window_delay

                # Apply backoff if we've hit rate limits recently
                if self._consecutive_rate_limits > 0:
                    base_delay = max(base_delay, self._current_backoff_seconds)

                return base_delay + self._add_jitter(base_delay * 0.1)

        # Check if we're in backoff period
        if (
            self._consecutive_rate_limits > 0
            and self._last_rate_limit_time
            and now - self._last_rate_limit_time < self._current_backoff_seconds
        ):
            remaining_backoff = self._current_backoff_seconds - (now - self._last_rate_limit_time)
            return max(0, remaining_backoff)

        # Priority-based minimum intervals
        min_intervals = {
            "high": 0.05,  # 20 req/sec max for high priority
            "normal": 0.1,  # 10 req/sec max for normal
            "low": 0.2,  # 5 req/sec max for low priority
        }

        min_interval = min_intervals.get(priority, 0.1)
        if self._request_times:
            last_request_interval = now - self._request_times[-1]
            if last_request_interval < min_interval:
                return min_interval - last_request_interval

        return 0.0

    def _increase_backoff(self, server_suggested_delay: float | None = None):
        """Increase backoff delay using configured strategy."""
        if server_suggested_delay:
            # Use server suggestion if available
            self._current_backoff_seconds = min(
                server_suggested_delay, self.config.max_backoff_seconds
            )
        else:
            # Apply backoff strategy
            if self.config.backoff_strategy == BackoffStrategy.LINEAR:
                self._current_backoff_seconds = min(
                    self._current_backoff_seconds + self.config.min_backoff_seconds,
                    self.config.max_backoff_seconds,
                )
            elif self.config.backoff_strategy == BackoffStrategy.EXPONENTIAL:
                self._current_backoff_seconds = min(
                    self._current_backoff_seconds * self.config.backoff_multiplier,
                    self.config.max_backoff_seconds,
                )
            elif self.config.backoff_strategy == BackoffStrategy.FIBONACCI:
                # Simplified Fibonacci-like progression
                prev_backoff = self._current_backoff_seconds
                self._current_backoff_seconds = min(
                    prev_backoff + (prev_backoff * 0.618),  # Golden ratio approximation
                    self.config.max_backoff_seconds,
                )

        self.metrics.backoff_events += 1

    def _add_jitter(self, base_delay: float) -> float:
        """Add jitter to delay to avoid thundering herd."""
        if not self.config.jitter_enabled:
            return 0.0

        import random

        return random.uniform(0, base_delay)

    def _should_adapt(self) -> bool:
        """Check if we should perform adaptive adjustment."""
        return (
            self.config.adaptive_enabled
            and time.time() - self._last_adaptive_adjustment > self._adaptive_adjustment_interval
        )

    def _adapt_rate_limits(self):
        """Adapt rate limits based on observed performance."""
        now = time.time()

        # Analyze successful request intervals
        if len(self._successful_request_intervals) >= 10:
            avg_interval = sum(self._successful_request_intervals) / len(
                self._successful_request_intervals
            )

            # If we're consistently able to make requests faster than our limit,
            # we might be able to increase the rate
            theoretical_max_rate = 1.0 / avg_interval
            current_max_rate = self.config.max_requests_per_window / self.config.window_seconds

            if theoretical_max_rate > current_max_rate * 1.2 and self._consecutive_rate_limits == 0:
                # Increase rate limit cautiously
                new_max = min(
                    self.config.max_requests_per_window + 5,
                    100,  # Never exceed 100 requests per window
                )
                logger.info(
                    f"Adaptive rate limit increase: "
                    f"{self.config.max_requests_per_window} -> {new_max}"
                )
                self.config.max_requests_per_window = new_max

            elif self._consecutive_rate_limits > 3:
                # Decrease rate limit if we're hitting limits frequently
                new_max = max(
                    self.config.max_requests_per_window - 5,
                    20,  # Never go below 20 requests per window
                )
                logger.info(
                    f"Adaptive rate limit decrease: "
                    f"{self.config.max_requests_per_window} -> {new_max}"
                )
                self.config.max_requests_per_window = new_max

        self._last_adaptive_adjustment = now

    def _update_metrics(self, request_time: float):
        """Update rate limiting metrics."""
        # Calculate current rate
        if len(self._request_times) >= 2:
            time_span = self._request_times[-1] - self._request_times[0]
            if time_span > 0:
                self.metrics.current_rate_per_second = len(self._request_times) / time_span

        # Calculate average interval
        if len(self._request_times) >= 2:
            intervals = []
            for i in range(1, len(self._request_times)):
                intervals.append(self._request_times[i] - self._request_times[i - 1])
            self.metrics.avg_request_interval = sum(intervals) / len(intervals)

    def get_stats(self) -> dict[str, Any]:
        """Get comprehensive rate limiting statistics."""
        with self._lock:
            time.time()

            # Calculate efficiency metrics
            efficiency = 1.0
            if self.metrics.total_requests > 0:
                efficiency = 1.0 - (self.metrics.requests_delayed / self.metrics.total_requests)

            # Calculate average delay
            avg_delay = 0.0
            if self.metrics.requests_delayed > 0:
                avg_delay = self.metrics.total_delay_seconds / self.metrics.requests_delayed

            return {
                "config": {
                    "max_requests_per_window": self.config.max_requests_per_window,
                    "window_seconds": self.config.window_seconds,
                    "burst_limit": self.config.burst_limit,
                    "backoff_strategy": self.config.backoff_strategy.value,
                },
                "current_state": {
                    "requests_in_window": len(self._request_times),
                    "requests_in_burst_window": len(self._burst_request_times),
                    "consecutive_rate_limits": self._consecutive_rate_limits,
                    "current_backoff_seconds": self._current_backoff_seconds,
                    "current_rate_per_second": round(self.metrics.current_rate_per_second, 2),
                },
                "performance": {
                    "total_requests": self.metrics.total_requests,
                    "requests_delayed": self.metrics.requests_delayed,
                    "efficiency_percentage": round(efficiency * 100, 1),
                    "avg_delay_seconds": round(avg_delay, 3),
                    "rate_limit_hits": self.metrics.rate_limit_hits,
                    "backoff_events": self.metrics.backoff_events,
                },
                "recommendations": self._generate_recommendations(),
            }

    def _generate_recommendations(self) -> list[str]:
        """Generate performance recommendations."""
        recommendations = []

        efficiency = 1.0
        if self.metrics.total_requests > 0:
            efficiency = 1.0 - (self.metrics.requests_delayed / self.metrics.total_requests)

        if efficiency < 0.8:
            recommendations.append("Low efficiency detected - consider reducing request rate")

        if self._consecutive_rate_limits > 5:
            recommendations.append("Frequent rate limits - API limits may have changed")

        if self.metrics.current_rate_per_second > 8:
            recommendations.append("High request rate - monitor for rate limit hits")

        if len(self._request_times) == self.config.max_requests_per_window:
            recommendations.append("Operating at rate limit capacity - consider request batching")

        return recommendations

    def reset_stats(self):
        """Reset statistics for fresh monitoring."""
        with self._lock:
            self.metrics = RateLimitMetrics()
            self._consecutive_rate_limits = 0
            self._last_rate_limit_time = None
            self._current_backoff_seconds = self.config.min_backoff_seconds
