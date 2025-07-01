"""Centralized status tracking and log aggregation for FastIntercom MCP.

This module provides utilities for tracking the status of operations, aggregating
logs from multiple sources, and maintaining centralized status information that
can be easily accessed by CLI commands and monitoring tools.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProcessStatus:
    """Status information for a running process or operation."""

    process_id: str
    process_type: str  # sync, test, server
    status: str  # running, completed, failed
    start_time: str
    end_time: str | None = None
    description: str = ""
    log_files: list[str] = None
    workspace: str | None = None
    progress: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self):
        if self.log_files is None:
            self.log_files = []
        if self.progress is None:
            self.progress = {}
        if self.metadata is None:
            self.metadata = {}


class StatusTracker:
    """Manages centralized status tracking and log aggregation."""

    def __init__(self, status_dir: str | None = None):
        """Initialize the status tracker.

        Args:
            status_dir: Directory for status files. Defaults to ~/.fastintercom-status
        """
        if status_dir:
            self.status_dir = Path(status_dir)
        else:
            self.status_dir = Path.home() / ".fastintercom-status"

        self.status_dir.mkdir(parents=True, exist_ok=True)

        # Status file locations
        self.active_file = self.status_dir / "active_processes.json"
        self.completed_file = self.status_dir / "completed_processes.json"
        self.summary_file = self.status_dir / "status_summary.json"

        # Log aggregation directory
        self.logs_dir = self.status_dir / "aggregated_logs"
        self.logs_dir.mkdir(exist_ok=True)

    def start_process(
        self,
        process_id: str,
        process_type: str,
        description: str,
        workspace: str | None = None,
        **metadata,
    ) -> ProcessStatus:
        """Start tracking a new process."""
        status = ProcessStatus(
            process_id=process_id,
            process_type=process_type,
            status="running",
            start_time=datetime.now().isoformat(),
            description=description,
            workspace=workspace,
            metadata=metadata,
        )

        self._update_active_process(status)
        self._update_summary()

        logger.info(f"Started tracking process: {process_id} ({process_type})")
        return status

    def update_process(
        self,
        process_id: str,
        progress: dict[str, Any] | None = None,
        log_files: list[str] | None = None,
        **metadata,
    ) -> None:
        """Update an existing process with new information."""
        active_processes = self._load_active_processes()

        if process_id in active_processes:
            if progress:
                active_processes[process_id]["progress"].update(progress)
            if log_files:
                active_processes[process_id]["log_files"] = log_files
            if metadata:
                active_processes[process_id]["metadata"].update(metadata)

            self._save_active_processes(active_processes)
            self._update_summary()

    def complete_process(self, process_id: str, status: str = "completed", **metadata) -> None:
        """Mark a process as completed and move it to completed processes."""
        active_processes = self._load_active_processes()

        if process_id in active_processes:
            process_status = active_processes[process_id]
            process_status["status"] = status
            process_status["end_time"] = datetime.now().isoformat()

            if metadata:
                process_status["metadata"].update(metadata)

            # Move to completed processes
            completed_processes = self._load_completed_processes()
            completed_processes[process_id] = process_status
            self._save_completed_processes(completed_processes)

            # Remove from active processes
            del active_processes[process_id]
            self._save_active_processes(active_processes)

            self._update_summary()
            logger.info(f"Completed tracking process: {process_id} ({status})")

    def get_active_processes(self) -> dict[str, dict[str, Any]]:
        """Get all currently active processes."""
        return self._load_active_processes()

    def get_process_status(self, process_id: str) -> dict[str, Any] | None:
        """Get the status of a specific process."""
        # Check active processes first
        active = self._load_active_processes()
        if process_id in active:
            return active[process_id]

        # Check completed processes
        completed = self._load_completed_processes()
        if process_id in completed:
            return completed[process_id]

        return None

    def get_recent_processes(
        self, limit: int = 10, process_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Get recent processes, optionally filtered by type."""
        all_processes = []

        # Add active processes
        active = self._load_active_processes()
        for process in active.values():
            all_processes.append(process)

        # Add completed processes
        completed = self._load_completed_processes()
        for process in completed.values():
            all_processes.append(process)

        # Filter by type if specified
        if process_type:
            all_processes = [p for p in all_processes if p["process_type"] == process_type]

        # Sort by start time (most recent first)
        all_processes.sort(key=lambda x: x["start_time"], reverse=True)

        return all_processes[:limit]

    def cleanup_old_processes(self, days_old: int = 7) -> None:
        """Clean up completed processes older than specified days."""
        cutoff_time = datetime.now().timestamp() - (days_old * 24 * 60 * 60)

        completed = self._load_completed_processes()
        cleaned = {}

        for process_id, process_data in completed.items():
            try:
                start_time = datetime.fromisoformat(process_data["start_time"]).timestamp()
                if start_time >= cutoff_time:
                    cleaned[process_id] = process_data
            except (ValueError, KeyError):
                # Keep if we can't parse the date
                cleaned[process_id] = process_data

        if len(cleaned) != len(completed):
            self._save_completed_processes(cleaned)
            removed_count = len(completed) - len(cleaned)
            logger.info(f"Cleaned up {removed_count} old completed processes")

    def aggregate_logs_for_process(self, process_id: str, output_file: str | None = None) -> str:
        """Aggregate all logs for a specific process into a single file."""
        process_status = self.get_process_status(process_id)
        if not process_status:
            raise ValueError(f"Process {process_id} not found")

        if not output_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = str(self.logs_dir / f"{process_id}_{timestamp}.log")

        log_files = process_status.get("log_files", [])
        workspace = process_status.get("workspace")

        with open(output_file, "w") as outf:
            outf.write(f"Aggregated logs for process: {process_id}\n")
            outf.write(f"Process type: {process_status['process_type']}\n")
            outf.write(f"Description: {process_status['description']}\n")
            outf.write(f"Start time: {process_status['start_time']}\n")
            outf.write(f"Status: {process_status['status']}\n")
            outf.write("=" * 80 + "\n\n")

            # Aggregate log files
            for log_file in log_files:
                log_path = Path(log_file)

                # If workspace is specified and log file is relative, make it absolute
                if workspace and not log_path.is_absolute():
                    log_path = Path(workspace) / log_file

                if log_path.exists():
                    outf.write(f"\n=== LOG FILE: {log_path} ===\n")
                    try:
                        with open(log_path) as inf:
                            outf.write(inf.read())
                    except Exception as e:
                        outf.write(f"Error reading log file: {e}\n")
                    outf.write(f"\n=== END OF {log_path} ===\n\n")
                else:
                    outf.write(f"\n=== LOG FILE NOT FOUND: {log_path} ===\n\n")

        logger.info(f"Aggregated logs for {process_id} written to {output_file}")
        return output_file

    def get_status_summary(self) -> dict[str, Any]:
        """Get a summary of system status."""
        active = self._load_active_processes()
        completed = self._load_completed_processes()

        # Count processes by type and status
        type_counts = {}
        status_counts = {"running": 0, "completed": 0, "failed": 0}

        for process in active.values():
            ptype = process["process_type"]
            type_counts[ptype] = type_counts.get(ptype, 0) + 1
            status_counts["running"] += 1

        for process in completed.values():
            ptype = process["process_type"]
            status = process["status"]
            type_counts[ptype] = type_counts.get(ptype, 0) + 1
            if status in status_counts:
                status_counts[status] += 1

        # Find most recent activity
        all_processes = list(active.values()) + list(completed.values())
        recent_activity = None
        if all_processes:
            most_recent = max(all_processes, key=lambda x: x["start_time"])
            recent_activity = {
                "process_id": most_recent["process_id"],
                "type": most_recent["process_type"],
                "status": most_recent["status"],
                "start_time": most_recent["start_time"],
            }

        return {
            "timestamp": datetime.now().isoformat(),
            "active_processes": len(active),
            "total_processes": len(active) + len(completed),
            "type_counts": type_counts,
            "status_counts": status_counts,
            "recent_activity": recent_activity,
            "status_directory": str(self.status_dir),
        }

    def _load_active_processes(self) -> dict[str, dict[str, Any]]:
        """Load active processes from file."""
        if not self.active_file.exists():
            return {}

        try:
            with open(self.active_file) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Error loading active processes: {e}")
            return {}

    def _save_active_processes(self, processes: dict[str, dict[str, Any]]) -> None:
        """Save active processes to file."""
        try:
            with open(self.active_file, "w") as f:
                json.dump(processes, f, indent=2)
        except OSError as e:
            logger.error(f"Error saving active processes: {e}")

    def _load_completed_processes(self) -> dict[str, dict[str, Any]]:
        """Load completed processes from file."""
        if not self.completed_file.exists():
            return {}

        try:
            with open(self.completed_file) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Error loading completed processes: {e}")
            return {}

    def _save_completed_processes(self, processes: dict[str, dict[str, Any]]) -> None:
        """Save completed processes to file."""
        try:
            with open(self.completed_file, "w") as f:
                json.dump(processes, f, indent=2)
        except OSError as e:
            logger.error(f"Error saving completed processes: {e}")

    def _update_active_process(self, status: ProcessStatus) -> None:
        """Update a single active process."""
        active_processes = self._load_active_processes()
        active_processes[status.process_id] = asdict(status)
        self._save_active_processes(active_processes)

    def _update_summary(self) -> None:
        """Update the status summary file."""
        summary = self.get_status_summary()
        try:
            with open(self.summary_file, "w") as f:
                json.dump(summary, f, indent=2)
        except OSError as e:
            logger.error(f"Error saving status summary: {e}")


# Global status tracker instance
_global_status_tracker: StatusTracker | None = None


def get_status_tracker() -> StatusTracker:
    """Get or create the global status tracker."""
    global _global_status_tracker
    if _global_status_tracker is None:
        _global_status_tracker = StatusTracker()
    return _global_status_tracker


def start_process_tracking(
    process_id: str, process_type: str, description: str, workspace: str | None = None, **metadata
) -> ProcessStatus:
    """Start tracking a process using the global status tracker."""
    tracker = get_status_tracker()
    return tracker.start_process(process_id, process_type, description, workspace, **metadata)


def update_process_tracking(
    process_id: str,
    progress: dict[str, Any] | None = None,
    log_files: list[str] | None = None,
    **metadata,
) -> None:
    """Update process tracking using the global status tracker."""
    tracker = get_status_tracker()
    tracker.update_process(process_id, progress, log_files, **metadata)


def complete_process_tracking(process_id: str, status: str = "completed", **metadata) -> None:
    """Complete process tracking using the global status tracker."""
    tracker = get_status_tracker()
    tracker.complete_process(process_id, status, **metadata)
