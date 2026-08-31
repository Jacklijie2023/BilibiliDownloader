from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class VideoInfo:
    platform: str
    title: str
    uploader: str
    page_number: int
    page_count: int
    page_title: str
    cid: int
    bvid: str | None = None
    aid: int | None = None
    uploader_id: int | None = None
    duration: int = 0
    cover_url: str | None = None
    description: str = ""
    pubdate: int | None = None
    tags: list[str] = field(default_factory=list)
    original_url: str = ""
    canonical_url: str = ""
    subtitles: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
