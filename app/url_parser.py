import re
import urllib.parse


def parse_video_url(url: str) -> tuple[str | None, int | None, int]:
    """Extract BVID/AID and page number from a Bilibili URL."""

    url = str(url or "").strip()
    bvid = None
    aid = None
    # BVIDs are currently 12 characters, but accepting the complete
    # alphanumeric path segment keeps this parser forward compatible with
    # future identifier lengths and test fixtures using shortened IDs.
    match = re.search(r"/video/(BV[0-9A-Za-z]+)(?:[/?#]|$)", url, re.IGNORECASE)
    if match:
        bvid = "BV" + match.group(1)[2:]
    else:
        match = re.search(r"/video/av(\d+)(?:[/?#]|$)", url, re.IGNORECASE)
        if match:
            aid = int(match.group(1))

    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    raw_page = (query.get("p") or ["1"])[0]
    page = int(raw_page) if raw_page.isdigit() else 1
    return bvid, aid, max(page, 1)


def canonicalize_bilibili_url(url: str) -> str:
    """Remove tracking parameters while preserving the selected page."""

    bvid, aid, page = parse_video_url(url)
    if bvid:
        base = f"https://www.bilibili.com/video/{bvid}"
    elif aid:
        base = f"https://www.bilibili.com/video/av{aid}"
    else:
        return url
    return f"{base}?p={page}" if page > 1 else base
