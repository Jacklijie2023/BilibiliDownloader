import json
import re
import time
import xml.etree.ElementTree as ET
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
        "subtitles": info.subtitles,
        "extra": info.extra,
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


def parse_danmaku_xml(source: str | bytes | Path) -> list[dict[str, Any]]:
    """Parse Bilibili ``list.so`` XML into normalized danmaku events.

    The ``p`` attribute contains comma-separated values where the first value
    is the timestamp in seconds and the second value is the display mode.  We
    intentionally ignore malformed entries instead of failing a video task.
    ``source`` may be XML text/bytes or a path to an XML file.
    """

    if isinstance(source, Path):
        payload: str | bytes = source.read_bytes()
    elif isinstance(source, str) and not source.lstrip().startswith("<"):
        # Accept both ``Path`` and string paths while avoiding an existence
        # check for ordinary XML text.
        try:
            candidate = Path(source)
            payload = candidate.read_bytes() if candidate.is_file() else source
        except (OSError, ValueError):
            payload = source
    else:
        payload = source
    try:
        root = ET.fromstring(payload)
    except (ET.ParseError, ValueError, TypeError):
        return []

    events: list[dict[str, Any]] = []
    for node in root.iter("d"):
        values = (node.get("p") or "").split(",")
        try:
            timestamp = max(float(values[0]), 0.0)
        except (IndexError, TypeError, ValueError):
            continue
        try:
            mode = int(values[1])
        except (IndexError, TypeError, ValueError):
            mode = 1
        text = "".join(node.itertext()).strip()
        if not text:
            continue
        events.append({"timestamp": timestamp, "mode": mode, "text": text})
    events.sort(key=lambda item: item["timestamp"])
    return events


def _ass_time(seconds: Any) -> str:
    value = max(float(seconds or 0), 0.0)
    centiseconds = int(round(value * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, centis = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _ass_escape(text: str) -> str:
    return str(text).replace("\\", r"\\").replace("{", "\\{").replace("}", "\\}").replace("\n", r"\\N")


def danmaku_to_ass(events: list[dict[str, Any]], duration: float = 5.0) -> str:
    """Render normalized events as a portable ASS subtitle document."""

    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\n"
        "PlayResY: 1080\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,42,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
        "0,0,0,0,100,100,0,0,1,2,0,8,20,20,20,1\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    span = max(float(duration), 0.1)
    for event in events:
        start = float(event.get("timestamp", 0))
        mode = int(event.get("mode", 1) or 1)
        # Modes 4/5 are bottom/top comments; ASS alignment 2/8 matches that
        # convention while scrolling comments use centered alignment 8.
        alignment = 2 if mode == 4 else 8 if mode == 5 else 8
        text = _ass_escape(str(event.get("text", "")))
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(start + span)},"
            f"Default,,0,0,0,,{{\\an{alignment}}}{text}"
        )
    return header + "\n".join(lines) + ("\n" if lines else "")


def danmaku_to_srt(events: list[dict[str, Any]], duration: float = 5.0) -> str:
    """Render normalized events as SRT (useful for players without ASS)."""

    span = max(float(duration), 0.1)
    lines: list[str] = []
    for number, event in enumerate(events, start=1):
        start = float(event.get("timestamp", 0))
        lines.extend((str(number), f"{_srt_time(start)} --> {_srt_time(start + span)}", str(event.get("text", "")), ""))
    return "\n".join(lines)


def convert_danmaku_xml(
    xml_source: str | bytes | Path,
    output_path: Path,
    format: str = "ass",
    duration: float = 5.0,
) -> Path:
    """Convert a downloaded danmaku XML file to ``.ass`` or ``.srt``."""

    fmt = format.lower().lstrip(".")
    if fmt not in {"ass", "srt"}:
        raise ValueError("danmaku format must be 'ass' or 'srt'")
    events = parse_danmaku_xml(xml_source)
    content = danmaku_to_ass(events, duration) if fmt == "ass" else danmaku_to_srt(events, duration)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    return target


# Descriptive aliases used by integrations and older callers.
def convert_danmaku_xml_to_ass(
    source: str | bytes | Path, output_path: Path, duration: float = 5.0
) -> Path:
    return convert_danmaku_xml(source, output_path, "ass", duration)


def convert_danmaku_xml_to_srt(
    source: str | bytes | Path, output_path: Path, duration: float = 5.0
) -> Path:
    return convert_danmaku_xml(source, output_path, "srt", duration)


def _srt_time(seconds: Any) -> str:
    value = max(float(seconds or 0), 0.0)
    millis = int(round(value * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
