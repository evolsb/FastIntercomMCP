"""Real-time progress broadcasting system for background process visibility.

This module provides a unified progress broadcasting system that enables real-time
monitoring of long-running operations like sync processes and integration tests.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ProgressType(Enum):
    """Types of progress events."""

    SYNC_START = "sync_start"
    SYNC_PROGRESS = "sync_progress"
    SYNC_COMPLETE = "sync_complete"
    API_REQUEST = "api_request"
    DATABASE_OPERATION = "database_operation"
    ERROR = "error"
    INFO = "info"
    TEST_START = "test_start"
    TEST_PROGRESS = "test_progress"
    TEST_COMPLETE = "test_complete"


@dataclass
class ProgressEvent:
    """Structured progress event for real-time monitoring."""

    timestamp: str
    event_type: ProgressType
    message: str
    current: int | None = None
    total: int | None = None
    percentage: float | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data["event_type"] = self.event_type.value
        return data


class ProgressBroadcaster:
    """Manages progress broadcasting to multiple channels for real-time monitoring."""

    def __init__(self, log_dir: str | None = None, session_id: str | None = None):
        """Initialize the progress broadcaster.

        Args:
            log_dir: Directory for progress log files
            session_id: Unique identifier for this session
        """
        self.log_dir = Path(log_dir) if log_dir else None
        self.session_id = session_id or f"session-{int(time.time())}"
        self.progress_file = None
        self.status_file = None
        self.console_enabled = True
        self.file_enabled = bool(log_dir)

        # Progress tracking state
        self._callbacks: list[Callable] = []
        self._last_console_update = 0
        self._console_update_interval = 2.0  # seconds
        self._current_operation = None
        self._operation_start_time = None

        # Setup progress log files if directory provided
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self.progress_file = self.log_dir / "progress.jsonl"
            self.status_file = self.log_dir / "status.json"

    def add_callback(self, callback: Callable[[ProgressEvent], None]):
        """Add a progress callback function."""
        self._callbacks.append(callback)

    def start_operation(
        self, operation_type: str, description: str, estimated_items: int | None = None
    ):
        """Start tracking a new operation."""
        self._current_operation = operation_type
        self._operation_start_time = time.time()

        # Create and broadcast start event
        event = self.create_event(
            ProgressType.SYNC_START if "sync" in operation_type.lower() else ProgressType.INFO,
            f"Starting {operation_type}: {description}",
            total=estimated_items,
            operation=operation_type,
        )
        self.broadcast(event)

        # Update status file
        self._update_status_file("running", operation_type, description)

    def update_progress(
        self, message: str, current: int | None = None, total: int | None = None, **metadata
    ):
        """Update progress for the current operation."""
        event_type = (
            ProgressType.SYNC_PROGRESS
            if self._current_operation and "sync" in self._current_operation.lower()
            else ProgressType.INFO
        )

        event = self.create_event(
            event_type,
            message,
            current=current,
            total=total,
            operation=self._current_operation,
            **metadata,
        )
        self.broadcast(event)

    def complete_operation(self, message: str, **metadata):
        """Mark the current operation as complete."""
        elapsed = time.time() - self._operation_start_time if self._operation_start_time else 0

        event_type = (
            ProgressType.SYNC_COMPLETE
            if self._current_operation and "sync" in self._current_operation.lower()
            else ProgressType.INFO
        )

        event = self.create_event(
            event_type,
            message,
            operation=self._current_operation,
            elapsed_seconds=elapsed,
            **metadata,
        )
        self.broadcast(event)

        # Update status file
        self._update_status_file("completed", self._current_operation, message)

        # Reset operation state
        self._current_operation = None
        self._operation_start_time = None

    def broadcast(self, event: ProgressEvent):
        """Broadcast progress event to all channels."""
        # Log to structured logger with enhanced metadata
        logger.info(
            event.message,
            extra={
                "progress_event": event.to_dict(),
                "event_type": event.event_type.value,
                "session_id": self.session_id,
            },
        )

        # Write to progress file for real-time monitoring
        if self.file_enabled and self.progress_file:
            try:
                with open(self.progress_file, "a") as f:
                    f.write(json.dumps(event.to_dict()) + "\n")
                    f.flush()  # Ensure immediate write for real-time access
            except Exception as e:
                logger.warning(f"Failed to write progress file: {e}")

        # Console output (throttled for readability)
        if self.console_enabled:
            current_time = time.time()
            should_update = (
                current_time - self._last_console_update
            ) >= self._console_update_interval

            # Always show start/complete events immediately
            force_update = event.event_type in [
                ProgressType.SYNC_START,
                ProgressType.SYNC_COMPLETE,
                ProgressType.ERROR,
            ]

            if should_update or force_update:
                self._update_console(event)
                self._last_console_update = current_time

        # Custom callbacks
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")

    def _update_console(self, event: ProgressEvent):
        """Update console with progress information."""
        if (
            event.event_type in [ProgressType.SYNC_PROGRESS, ProgressType.TEST_PROGRESS]
            and event.percentage
        ):
            # Create progress bar for operations with known progress
            bar_width = 40
            filled_width = int(bar_width * (event.percentage / 100))
            bar = "█" * filled_width + "░" * (bar_width - filled_width)

            status = f"\r[{bar}] {event.percentage:.1f}% - {event.message}"
            if event.current and event.total:
                status += f" ({event.current:,}/{event.total:,})"

            print(status, end="", flush=True)
        else:
            # Regular message with timestamp for other events
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"\n[{timestamp}] {event.message}")

    def _update_status_file(self, status: str, operation: str | None, description: str):
        """Update the status file for external monitoring."""
        if not self.status_file:
            return

        status_data = {
            "session_id": self.session_id,
            "timestamp": datetime.utcnow().isoformat(),
            "status": status,
            "operation": operation,
            "description": description,
            "progress_file": str(self.progress_file) if self.progress_file else None,
        }

        try:
            with open(self.status_file, "w") as f:
                json.dump(status_data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to update status file: {e}")

    def create_event(
        self,
        event_type: ProgressType,
        message: str,
        current: int | None = None,
        total: int | None = None,
        **metadata,
    ) -> ProgressEvent:
        """Create a progress event with calculated percentage."""
        percentage = None
        if current is not None and total is not None and total > 0:
            percentage = (current / total) * 100

        return ProgressEvent(
            timestamp=datetime.utcnow().isoformat(),
            event_type=event_type,
            message=message,
            current=current,
            total=total,
            percentage=percentage,
            metadata=metadata if metadata else None,
        )


# Global broadcaster instance for easy access
_global_broadcaster: ProgressBroadcaster | None = None


def setup_global_progress_broadcaster(
    log_dir: str | None = None, session_id: str | None = None
) -> ProgressBroadcaster:
    """Setup the global progress broadcaster."""
    global _global_broadcaster
    _global_broadcaster = ProgressBroadcaster(log_dir, session_id)
    return _global_broadcaster


def get_progress_broadcaster() -> ProgressBroadcaster | None:
    """Get the global progress broadcaster."""
    return _global_broadcaster


def broadcast_progress(
    event_type: ProgressType,
    message: str,
    current: int | None = None,
    total: int | None = None,
    **metadata,
):
    """Convenience function to broadcast progress using global broadcaster."""
    broadcaster = get_progress_broadcaster()
    if broadcaster:
        event = broadcaster.create_event(event_type, message, current, total, **metadata)
        broadcaster.broadcast(event)
    else:
        # Fallback to regular logging if no broadcaster
        logger.info(f"Progress: {message}")


def start_operation(operation_type: str, description: str, estimated_items: int | None = None):
    """Start tracking a new operation using global broadcaster."""
    broadcaster = get_progress_broadcaster()
    if broadcaster:
        broadcaster.start_operation(operation_type, description, estimated_items)


def update_progress(message: str, current: int | None = None, total: int | None = None, **metadata):
    """Update progress using global broadcaster."""
    broadcaster = get_progress_broadcaster()
    if broadcaster:
        broadcaster.update_progress(message, current, total, **metadata)


def complete_operation(message: str, **metadata):
    """Complete the current operation using global broadcaster."""
    broadcaster = get_progress_broadcaster()
    if broadcaster:
        broadcaster.complete_operation(message, **metadata)
