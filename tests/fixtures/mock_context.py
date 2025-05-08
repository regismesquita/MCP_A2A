"""Mock FastMCP Context for testing."""

from typing import Any, Dict, List, Optional, Union, Callable
from unittest.mock import AsyncMock
import logging

logger = logging.getLogger(__name__)


class MockContext(AsyncMock):
    """Mock implementation of FastMCP Context.

    This class tracks progress reports, completions, and errors while providing
    an API compatible with FastMCP's Context.
    
    Enhanced with configurable failure modes for robust testing:
    - Methods can be set to None
    - Methods can be configured to raise exceptions
    - Methods can be replaced with non-callable attributes
    """

    def __init__(self, *args, **kwargs):
        """Initialize the mock context with tracking for different message types."""
        super().__init__(*args, **kwargs)
        self.progress_reports = []
        self.completions = []
        self.errors = []

        # Configure the AsyncMock methods to update our tracking lists
        self.report_progress.side_effect = self._safe_track_progress
        self.complete.side_effect = self._safe_track_completion
        self.error.side_effect = self._safe_track_error

    async def _safe_track_progress(self, progress: float, total: float = None) -> None:
        """
        Safely track a progress report.
        
        This method is used as the side_effect for report_progress and includes
        robust handling for the case where report_progress is later set to None
        or replaced with a non-callable.
        """
        try:
            await self._track_progress(progress, total)
        except Exception as e:
            logger.warning(f"Error tracking progress: {e}")

    async def _safe_track_completion(self, message: str) -> None:
        """
        Safely track a completion message.
        
        This method is used as the side_effect for complete and includes
        robust handling for the case where complete is later set to None
        or replaced with a non-callable.
        """
        try:
            await self._track_completion(message)
        except Exception as e:
            logger.warning(f"Error tracking completion: {e}")

    async def _safe_track_error(self, message: str) -> None:
        """
        Safely track an error message.
        
        This method is used as the side_effect for error and includes
        robust handling for the case where error is later set to None
        or replaced with a non-callable.
        """
        try:
            await self._track_error(message)
        except Exception as e:
            logger.warning(f"Error tracking error: {e}")

    async def _track_progress(self, progress: float, total: float = None) -> None:
        """Track a progress report."""
        self.progress_reports.append(
            {"current": progress, "total": total or 1.0, "message": ""}
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
        
    def configure_failure_mode(self, method_name: str, mode: str = "none") -> None:
        """
        Configure a specific failure mode for testing robustness.
        
        Args:
            method_name: The name of the method to configure ('report_progress', 'complete', or 'error')
            mode: The failure mode to configure:
                - 'none': Set method to None
                - 'non_callable': Set method to a non-callable value
                - 'raise_exception': Configure method to raise an exception
                - 'reset': Reset to normal behavior
        """
        if mode == "none":
            # Set the method to None
            setattr(self, method_name, None)
        elif mode == "non_callable":
            # Replace with a non-callable attribute
            setattr(self, method_name, 123)
        elif mode == "raise_exception":
            # Configure to raise an exception
            method = getattr(self, method_name, None)
            if method is not None and callable(method):
                method.side_effect = Exception(f"Simulated {method_name} failure")
        elif mode == "reset":
            # Reset to normal behavior
            if method_name == "report_progress":
                self.report_progress = AsyncMock(side_effect=self._safe_track_progress)
            elif method_name == "complete":
                self.complete = AsyncMock(side_effect=self._safe_track_completion)
            elif method_name == "error":
                self.error = AsyncMock(side_effect=self._safe_track_error)
        else:
            raise ValueError(f"Unknown failure mode: {mode}")
            
    def reset_all(self) -> None:
        """Reset all methods to their normal behavior and clear tracking lists."""
        self.progress_reports = []
        self.completions = []
        self.errors = []
        
        self.report_progress = AsyncMock(side_effect=self._safe_track_progress)
        self.complete = AsyncMock(side_effect=self._safe_track_completion)
        self.error = AsyncMock(side_effect=self._safe_track_error)
