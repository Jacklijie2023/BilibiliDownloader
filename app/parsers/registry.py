"""Parser registry and platform detection helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .base import PlatformParser, UnsupportedPlatformError


class ParserRegistry:
    def __init__(self, parsers: Iterable[PlatformParser] = ()):
        self._parsers: list[PlatformParser] = []
        for parser in parsers:
            self.register(parser)

    def register(self, parser: PlatformParser) -> PlatformParser:
        # Structural validation keeps third-party integrations lightweight;
        # subclassing PlatformParser remains the recommended option.
        if not callable(getattr(parser, "can_handle", None)) or not callable(getattr(parser, "parse", None)):
            raise TypeError("parser must provide can_handle() and parse()")
        self._parsers.append(parser)
        return parser

    def parsers(self) -> tuple[PlatformParser, ...]:
        return tuple(self._parsers)

    def for_url(self, url: str) -> PlatformParser:
        for parser in self._parsers:
            try:
                if parser.can_handle(url):
                    return parser
            except (TypeError, ValueError):
                continue
        raise UnsupportedPlatformError(f"no parser registered for URL: {url}")

    def parse(self, url: str, **kwargs: Any) -> Any:
        return self.for_url(url).parse(url, **kwargs)


def default_registry() -> ParserRegistry:
    # Imported lazily to avoid requests/yt-dlp side effects for callers that
    # only need to inspect the registry.
    from .bilibili import BilibiliParser

    return ParserRegistry([BilibiliParser()])


def detect_platform(url: str, registry: ParserRegistry | None = None) -> str:
    """Return the registered platform name or ``"unknown"``."""

    try:
        return (registry or default_registry()).for_url(url).platform
    except UnsupportedPlatformError:
        return "unknown"


def get_parser(url: str, registry: ParserRegistry | None = None) -> PlatformParser:
    """Convenience accessor for applications that need the parser object."""

    return (registry or default_registry()).for_url(url)


def parse_url(url: str, registry: ParserRegistry | None = None, **kwargs: Any) -> Any:
    """Parse a URL using the default (or supplied) registry."""

    return get_parser(url, registry).parse(url, **kwargs)
