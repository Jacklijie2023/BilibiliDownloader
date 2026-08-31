import json
import re
import time
from pathlib import Path
from typing import Any

import requests

from app.models import VideoInfo


def metadata_paths(output_path: Path) -> tuple[Path, Path]:
    """Return sidecar JSON and cover paths for a video output."""

    return output_path.with_suffix(".json"), output_path.with_suffix(".jpg")


def _json_value(info: VideoInfo, quality: str, play: dict[str, Any]) -> dict[str, Any]:
    return {
        "platform": info.platform,
        "bvid": info.bvid,
        "aid": info.aid,
        "cid": info.cid,
        "title": info.title,
        "uploader": info.uploader,
        "uploader_id": info.uploader_id,
        "page": info.page_number,
        "page_count": info.page_count,
        "page_title": info.page_title,
        "duration": info.duration,
        "cover_url": info.cover_url,
        "description": info.description,
        "pubdate": info.pubdate,
        "tags": info.tags,
        "original_url": info.original_url,
        "canonical_url": info.canonical_url,
        "quality_requested": quality,
        "quality_actual": play.get("quality"),
        "format": play.get("format"),
        "video_codec_id": play.get("video_codecid"),
        "downloaded_at": int(time.time()),
    }


def save_video_metadata(
    output_path: Path,
    info: VideoInfo,
    quality: str,
    play: dict[str, Any],
    session: requests.Session | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[Path, Path | None]:
    """Write a JSON sidecar and best-effort cover image.

    Metadata failures are intentionally isolated from the media download: a
    missing cover must never turn a successfully downloaded video into a
    failed task.
    """

    json_path, cover_path = metadata_paths(output_path)
    payload = _json_value(info, quality, play)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not info.cover_url:
        return json_path, None

    try:
        client = session or requests.Session()
        response = client.get(
            info.cover_url,
            headers=headers,
            timeout=(10, 30),
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "image" not in content_type and not re.search(
            rb"^\xff\xd8|^\x89PNG", response.content
        ):
            return json_path, None
        cover_path.write_bytes(response.content)
        return json_path, cover_path
    except Exception:
        return json_path, None


def save_subtitle_tracks(
    output_path: Path,
    tracks: list[dict[str, Any]],
    session: requests.Session,
    headers: dict[str, str] | None = None,
) -> list[Path]:
    """Download exposed subtitle tracks and write them as UTF-8 SRT files."""

    saved: list[Path] = []
    for index, track in enumerate(tracks, start=1):
        url = track.get("subtitle_url") or track.get("url")
        if not url:
            continue
        if url.startswith("//"):
            url = "https:" + url
        try:
            response = session.get(url, headers=headers, timeout=(10, 30))
            response.raise_for_status()
            payload = response.json()
            body = payload.get("body") or []
            if not body:
                continue
            language = re.sub(
                r"[^A-Za-z0-9_-]+", "_",
                str(track.get("lan_doc") or track.get("lan") or index),
            ).strip("_") or str(index)
            path = output_path.with_name(
                output_path.stem + f".{language}.srt"
            )
            lines = []
            for number, item in enumerate(body, start=1):
                start = _srt_time(item.get("from", 0))
                end = _srt_time(item.get("to", item.get("from", 0)))
                lines.extend((str(number), f"{start} --> {end}",
                              str(item.get("content") or ""), ""))
            path.write_text("\n".join(lines), encoding="utf-8")
            saved.append(path)
        except Exception:
            continue
    return saved


def _srt_time(seconds: Any) -> str:
    value = max(float(seconds or 0), 0.0)
    millis = int(round(value * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
