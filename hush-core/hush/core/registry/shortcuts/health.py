"""Health check utilities for ResourceHub."""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class HealthCheckResult:
    """Result of health check operation.

    Attributes:
        results: Dict mapping resource key to health status (True = healthy)
        errors: Dict mapping failed resource key to error message
    """

    results: Dict[str, bool]
    errors: Dict[str, str]

    @property
    def healthy(self) -> bool:
        """True if all resources are healthy."""
        return all(self.results.values())

    @property
    def passed(self) -> List[str]:
        """List of healthy resource keys."""
        return [k for k, v in self.results.items() if v]

    @property
    def failed(self) -> List[str]:
        """List of unhealthy resource keys."""
        return [k for k, v in self.results.items() if not v]

    def __repr__(self) -> str:
        total = len(self.results)
        passed = len(self.passed)
        status = "HEALTHY" if self.healthy else "UNHEALTHY"
        return f"<HealthCheckResult {status} {passed}/{total}>"
