"""Backend implementations.

All backends inherit from `verify.backends.base.Backend` and are registered
via `verify.backends.registry`.
"""

from verify.backends.base import (
    Backend,
    BackendCapabilities,
    DetectionResult,
    LaunchSpec,
)

__all__ = ["Backend", "BackendCapabilities", "DetectionResult", "LaunchSpec"]
