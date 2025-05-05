"""Mock FastMCP Context for testing."""

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock


class MockContext(AsyncMock):
    """Mock implementation of FastMCP Context.

    This class tracks progress reports, completions, and errors while providing
    an API compatible with FastMCP's Context.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the mock context with tracking for different message types."""
        super().__init__(*args, **kwargs)
        self.progress_reports = []
        self.completions = []
        self.errors = []

        # Configure the AsyncMock methods to update our tracking lists
        self.report_progress.side_effect = self._track_progress
        self.complete.side_effect = self._track_completion
        self.error.side_effect = self._track_error

    async def _track_progress(self, current: float, total: float, message: str) -> None:
        """Track a progress report."""
        self.progress_reports.append(
            {"current": current, "total": total, "message": message}
        )

    async def _track_completion(self, message: str) -> None:
        """Track a completion message."""
        self.completions.append({"message": message})

    async def _track_error(self, message: str) -> None:
        """Track an error message."""
        self.errors.append({"message": message})

    def get_latest_progress(self) -> Optional[Dict[str, Any]]:
        """Get the most recent progress report."""
        return self.progress_reports[-1] if self.progress_reports else None

    def get_latest_completion(self) -> Optional[Dict[str, str]]:
        """Get the most recent completion message."""
        return self.completions[-1] if self.completions else None

    def get_latest_error(self) -> Optional[Dict[str, str]]:
        """Get the most recent error message."""
        return self.errors[-1] if self.errors else None

    def get_progress_count(self) -> int:
        """Get the number of progress reports."""
        return len(self.progress_reports)

    def get_completion_count(self) -> int:
        """Get the number of completion messages."""
        return len(self.completions)

    def get_error_count(self) -> int:
        """Get the number of error messages."""
        return len(self.errors)

    def get_final_progress(self) -> Optional[float]:
        """Get the final progress value reported."""
        return self.progress_reports[-1]["current"] if self.progress_reports else None
