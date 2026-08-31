from typing import Any


CODEC_PRIORITY = {7: 3, 12: 2, 13: 1}


def select_dash_streams(dash: dict[str, Any], max_height: int):
    videos = [
        item for item in (dash.get("video") or [])
        if (item.get("height") or 0) <= max_height
    ]
    if not videos:
        videos = sorted(
            dash.get("video") or [], key=lambda item: item.get("height") or 0
        )[:1]
    if not videos:
        raise RuntimeError("playurl returned no video stream")

    video = max(
        videos,
        key=lambda item: (
            item.get("height") or 0,
            CODEC_PRIORITY.get(item.get("codecid"), 0),
            item.get("bandwidth") or 0,
        ),
    )
    audios = list(dash.get("audio") or [])
    for key in ("flac", "dolby"):
        extra_audio = (dash.get(key) or {}).get("audio")
        if isinstance(extra_audio, dict):
            audios.append(extra_audio)
        elif isinstance(extra_audio, list):
            audios.extend(extra_audio)
    audio = max(audios, key=lambda item: item.get("bandwidth") or 0) if audios else None
    return video, audio


def stream_urls(stream: dict[str, Any]) -> list[str]:
    urls = []
    for key in ("baseUrl", "base_url", "url"):
        if stream.get(key):
            urls.append(stream[key])
    for key in ("backupUrl", "backup_url"):
        value = stream.get(key) or []
        urls.extend([value] if isinstance(value, str) else value)
    return list(dict.fromkeys(url for url in urls if url))

