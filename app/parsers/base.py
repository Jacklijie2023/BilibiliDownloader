"""Common parser interfaces used by platform-specific integrations.

Only Bilibili is implemented by the downloader today.  Keeping a tiny,
dependency-free interface makes adding another provider explicit without
coupling the GUI or media pipeline to a particular site's response format.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ParserError(RuntimeError):
    """Base error raised when a parser cannot process a URL or payload."""


class UnsupportedPlatformError(ParserError):
    """Raised when no registered parser supports a URL."""


class PlatformParser(ABC):
    platform: str = "unknown"

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Return whether this parser recognizes *url*."""

    @abstractmethod
    def parse(self, url: str, **kwargs: Any) -> Any:
        """Parse *url* and return a provider-specific or normalized result."""

