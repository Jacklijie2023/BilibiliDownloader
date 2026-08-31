import hashlib
import http.cookiejar
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import queue
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import requests
import yt_dlp

from app.models import VideoInfo
from app.metadata.writer import save_subtitle_tracks, save_video_metadata
from app.jobs.store import TaskStore
from app.parsers.bilibili import BilibiliApiClient
from app.url_parser import (
    canonicalize_bilibili_url as shared_canonicalize_bilibili_url,
    parse_video_url as shared_parse_video_url,
)
from app.media.resolver import (
    select_dash_streams as shared_select_dash_streams,
    stream_urls as shared_stream_urls,
)


# ============================================================
# 项目路径
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

FFMPEG_DIR = BASE_DIR / "ffmpeg"

DEFAULT_DOWNLOAD_DIR = BASE_DIR / "downloads"

DEFAULT_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 可选的 Netscape 格式 Cookie 文件（浏览器插件导出），用于下载 1080P 及以上
COOKIE_FILE = BASE_DIR / "cookies.txt"


# ============================================================
# 常量与工具
# ============================================================

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)

# api.bilibili.com 与视频 CDN 都会校验 UA / Referer，缺失时直链返回 403
API_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}

# 画质 -> playurl 的 qn 参数
QUALITY_QN = {
    "最佳画质": 127,
    "1080P": 80,
    "720P": 64,
    "480P": 32,
    "360P": 16,
}

# 画质 -> 允许的最大高度
QUALITY_HEIGHT = {
    "最佳画质": 100000,
    "1080P": 1080,
    "720P": 720,
    "480P": 480,
    "360P": 360,
}

# 编码优先级：AVC 兼容性最好，其次 HEVC，最后 AV1
CODEC_PRIORITY = {7: 3, 12: 2, 13: 1}

WINDOWS_INVALID_CHARS = '<>:"/\\|?*'


class DownloadStopped(Exception):
    """用户点击了停止。"""


def sanitize_filename(name, max_length=80):
    """去掉 Windows 非法字符并限制长度。"""

    text = str(name or "").strip()
    text = "".join("_" if c in WINDOWS_INVALID_CHARS else c for c in text)
    text = "".join(c for c in text if ord(c) >= 32)
    text = re.sub(r"\s+", " ", text).strip(" .")

    if len(text) > max_length:
        text = text[:max_length].strip(" .")

    return text or "video"


def format_size(num_bytes):
    value = float(num_bytes)

    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.2f}{unit}"
        value /= 1024


def format_eta(seconds):
    if seconds is None or seconds < 0 or seconds > 86400:
        return "--"

    seconds = int(seconds)

    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def parse_video_url(url):
    """从链接里解析出 bvid / aid 与分 P 序号。"""

    bvid = None
    aid = None

    match = re.search(r"/video/(BV[0-9A-Za-z]{8,12})", url)

    if match:
        bvid = match.group(1)
    else:
        match = re.search(r"/video/av(\d+)", url, re.IGNORECASE)
        if match:
            aid = int(match.group(1))

    query = urllib.parse.parse_qs(
        urllib.parse.urlparse(url).query
    )

    page_value = (query.get("p") or ["1"])[0]
    page = int(page_value) if page_value.isdigit() else 1

    return bvid, aid, max(page, 1)


def canonicalize_bilibili_url(url):
    """Return a stable Bilibili URL without tracking parameters.

    Bilibili's web WAF is more likely to return HTTP 412 for copied browser
    URLs containing ``spm_id_from``/``vd_source`` and other tracking fields.
    The API path only needs the BV/AV id and the ``p`` page number, so strip
    everything else before handing a URL to a fallback extractor.
    """

    bvid, aid, page = parse_video_url(url)

    if bvid:
        base = f"https://www.bilibili.com/video/{bvid}"
    elif aid:
        base = f"https://www.bilibili.com/video/av{aid}"
    else:
        return url

    return f"{base}?p={page}" if page > 1 else base


# Keep the legacy names used throughout this file while making the extracted
# module the single source of truth for URL parsing.
parse_video_url = shared_parse_video_url
canonicalize_bilibili_url = shared_canonicalize_bilibili_url
select_dash_streams = shared_select_dash_streams
stream_urls = shared_stream_urls


def select_dash_streams(dash, max_height):
    """挑选画质不超过 max_height 的最佳视频流，以及码率最高的音频流。"""

    videos = [
        v for v in (dash.get("video") or [])
        if (v.get("height") or 0) <= max_height
    ]

    if not videos:
        # 全部都超过上限时退而求其次，取最低画质
        videos = sorted(
            dash.get("video") or [],
            key=lambda v: v.get("height") or 0
        )[:1]

    if not videos:
        raise RuntimeError("playurl 没有返回任何视频流")

    video = max(
        videos,
        key=lambda v: (
            v.get("height") or 0,
            CODEC_PRIORITY.get(v.get("codecid"), 0),
            v.get("bandwidth") or 0,
        )
    )

    audios = list(dash.get("audio") or [])

    # Hi-Res / 杜比音轨放在单独字段里
    for extra_key in ("flac", "dolby"):
        extra = dash.get(extra_key) or {}
        extra_audio = extra.get("audio")
        if isinstance(extra_audio, dict):
            audios.append(extra_audio)
        elif isinstance(extra_audio, list):
            audios.extend(extra_audio)

    audio = None

    if audios:
        audio = max(audios, key=lambda a: a.get("bandwidth") or 0)

    return video, audio


def stream_urls(stream):
    """直链 + 备用镜像，按顺序返回。"""

    urls = []

    for key in ("baseUrl", "base_url", "url"):
        value = stream.get(key)
        if value:
            urls.append(value)

    for key in ("backupUrl", "backup_url"):
        value = stream.get(key) or []
        if isinstance(value, str):
            urls.append(value)
        else:
            urls.extend(value)

    # 去重且保持顺序
    return list(dict.fromkeys(u for u in urls if u))


# ============================================================
# Bilibili Web API
# ============================================================

class BilibiliWebApi:
    """直接调用官方 Web 接口获取视频信息与播放直链。

    为什么不再依赖网页：
    www.bilibili.com/video/<BV号> 这个 HTML 页面会被 B 站的风控网关拦截，
    返回 HTTP 412（响应体是"出错啦"提示页，带 x-sec-request-id 头）。
    yt-dlp 的 BiliBili 提取器第一步就是抓这个页面，所以整条链路直接失败。
    同一网络下 api.bilibili.com 的 view / playurl 接口仍然正常返回，
    因此这里改成用接口拿信息和直链，自己下载再交给 ffmpeg 合并。
    """

    # wbi 签名用的字符重排表
    MIXIN_KEY_ENC_TAB = [
        46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43,
        5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16,
        24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59,
        6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
    ]

    def __init__(self, cookie_source="不使用", log=None):

        self._log = log or (lambda message: None)

        self.session = requests.Session()
        self.session.headers.update(API_HEADERS)

        self._mixin_key = None
        self.logged_in = False

        self._load_cookies(cookie_source)
        self._ensure_buvid()
        self._check_login()

    def log(self, message):
        self._log(str(message))

    # --------------------------------------------------------
    # Cookie
    # --------------------------------------------------------

    def _load_cookies(self, cookie_source):

        if cookie_source in (None, "", "不使用"):
            return

        if cookie_source == "cookies.txt":

            if not COOKIE_FILE.exists():
                self.log(f"未找到 {COOKIE_FILE.name}，将以未登录状态下载")
                return

            try:
                jar = http.cookiejar.MozillaCookieJar(str(COOKIE_FILE))
                jar.load(ignore_discard=True, ignore_expires=True)
                self.session.cookies.update(jar)
                self.log(f"已加载 {COOKIE_FILE.name}")
            except Exception as e:
                self.log(f"读取 {COOKIE_FILE.name} 失败：{e}")

            return

        # 从浏览器读取（Chrome / Edge 新版本加密可能失败，失败就退回未登录）
        try:
            from yt_dlp.cookies import extract_cookies_from_browser

            jar = extract_cookies_from_browser(cookie_source.lower())

            for cookie in jar:
                if "bilibili" in (cookie.domain or ""):
                    self.session.cookies.set_cookie(cookie)

            self.log(f"已读取 {cookie_source} 浏览器 Cookie")

        except Exception as e:
            self.log(f"读取 {cookie_source} Cookie 失败：{e}")

    def _ensure_buvid(self):
        """没有 buvid3 时接口容易被风控，先取一个设备指纹。"""

        if self.session.cookies.get("buvid3"):
            return

        try:
            data = self._get_json(
                "https://api.bilibili.com/x/frontend/finger/spi"
            )

            for name, key in (("buvid3", "b_3"), ("buvid4", "b_4")):
                value = data.get(key)
                if value:
                    self.session.cookies.set(
                        name, value, domain=".bilibili.com", path="/"
                    )

        except Exception as e:
            self.log(f"获取设备指纹失败（可忽略）：{e}")

    def _check_login(self):

        try:
            nav = self._request_json(
                "https://api.bilibili.com/x/web-interface/nav"
            )
            self.logged_in = bool((nav.get("data") or {}).get("isLogin"))
        except Exception:
            self.logged_in = False

        if self.logged_in:
            self.log("Cookie 有效，已登录")
        else:
            self.log("未登录：B 站只提供 480P 及以下画质")

    # --------------------------------------------------------
    # 请求
    # --------------------------------------------------------

    def _request_json(self, url, params=None, retries=3):
        """带重试的 GET，返回原始 JSON。"""

        last_error = None

        for attempt in range(retries):
            try:
                response = self.session.get(url, params=params, timeout=20)
                response.raise_for_status()
                return response.json()

            except Exception as e:
                last_error = e
                time.sleep(1.5 * (attempt + 1))

        raise RuntimeError(f"请求失败：{url}（{last_error}）")

    def _get_json(self, url, params=None, signed=False):
        """请求接口并校验业务返回码，返回 data 部分。"""

        if signed:
            params = self._sign(params or {})

        payload = self._request_json(url, params=params)

        if payload.get("code") != 0:
            raise RuntimeError(
                f"接口返回 {payload.get('code')}：{payload.get('message')}"
            )

        return payload.get("data") or {}

    # --------------------------------------------------------
    # wbi 签名
    # --------------------------------------------------------

    def _get_mixin_key(self):

        if self._mixin_key:
            return self._mixin_key

        nav = self._request_json(
            "https://api.bilibili.com/x/web-interface/nav"
        )

        wbi_img = ((nav.get("data") or {}).get("wbi_img")) or {}

        def key_of(url):
            return url.rsplit("/", 1)[-1].split(".")[0]

        raw = key_of(wbi_img.get("img_url", "")) + key_of(
            wbi_img.get("sub_url", "")
        )

        if len(raw) < 64:
            raise RuntimeError("无法获取 wbi 密钥")

        self._mixin_key = "".join(
            raw[i] for i in self.MIXIN_KEY_ENC_TAB
        )[:32]

        return self._mixin_key

    def _sign(self, params):

        signed = dict(params)
        signed["wts"] = int(time.time())

        query = urllib.parse.urlencode(sorted(signed.items()))

        signed["w_rid"] = hashlib.md5(
            (query + self._get_mixin_key()).encode("utf-8")
        ).hexdigest()

        return signed

    # --------------------------------------------------------
    # 业务接口
    # --------------------------------------------------------

    def get_view(self, bvid=None, aid=None):
        """视频基本信息：标题、UP 主、分 P 列表。"""

        params = {"bvid": bvid} if bvid else {"aid": aid}

        return self._get_json(
            "https://api.bilibili.com/x/web-interface/view",
            params=params
        )

    def get_subtitle_tracks(self, aid, cid, bvid=None):
        """Return subtitle descriptors when the account/API exposes them."""

        params = {"aid": aid, "cid": cid}
        if bvid:
            params["bvid"] = bvid
        try:
            data = self._get_json(
                "https://api.bilibili.com/x/player/wbi/v2",
                params=params,
                signed=True,
            )
            subtitle = data.get("subtitle") or {}
            return subtitle.get("list") or []
        except Exception as exc:
            self.log(f"subtitle lookup skipped: {exc}")
            return []

    def get_play_info(self, aid, cid, qn):
        """播放地址。优先走 wbi 签名接口，失败再退回旧接口。"""

        params = {
            "avid": aid,
            "cid": cid,
            "qn": qn,
            "fnval": 4048,   # 请求 DASH（含 8K / HDR / 杜比标记位）
            "fnver": 0,
            "fourk": 1,
        }

        try:
            return self._get_json(
                "https://api.bilibili.com/x/player/wbi/playurl",
                params=params,
                signed=True
            )

        except Exception as e:
            self.log(f"wbi 接口失败（{e}），改用旧版 playurl")

            return self._get_json(
                "https://api.bilibili.com/x/player/playurl",
                params=params
            )


# ============================================================
# Bilibili 下载器
# ============================================================

class BilibiliDownloader:

    def __init__(
        self,
        download_dir,
        quality,
        browser,
        max_retries=5
    ):

        self.download_dir = Path(
            download_dir
        )

        self.download_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        self.quality = quality

        self.browser = browser

        self.max_retries = max_retries

        # FFmpeg 路径
        self.ffmpeg_location = str(
            FFMPEG_DIR
        )

        self.log_callback = None

        self.progress_callback = None

        # 由 GUI 注入，返回 True 表示用户点了停止
        self.stop_flag = None

        # 延迟创建：第一次真正需要接口时再初始化
        self._api = None

        # 镜像线路测速结果，按主机名缓存，整批任务只测一次
        self._mirror_speed = {}

    # ========================================================
    # 日志
    # ========================================================

    def log(self, message):

        # Windows 控制台常是 GBK，✓ / ✗ 之类字符直接 print 会抛 UnicodeEncodeError
        try:
            print(message)
        except UnicodeEncodeError:
            encoding = sys.stdout.encoding or "utf-8"
            print(str(message).encode(encoding, "replace").decode(encoding))

        if self.log_callback:

            self.log_callback(
                str(message)
            )

    # ========================================================
    # 下载进度
    # ========================================================

    def progress_hook(self, data):

        status = data.get(
            "status"
        )

        if status == "downloading":

            percent = data.get(
                "_percent_str",
                "0%"
            )

            speed = data.get(
                "_speed_str",
                "--"
            )

            eta = data.get(
                "_eta_str",
                "--"
            )

            filename = data.get(
                "filename",
                ""
            )

            filename = Path(
                filename
            ).name

            if self.progress_callback:

                self.progress_callback(
                    percent,
                    speed,
                    eta,
                    filename,
                    status
                )

        elif status == "finished":

            filename = data.get(
                "filename",
                ""
            )

            filename = Path(
                filename
            ).name

            if self.progress_callback:

                self.progress_callback(
                    "100%",
                    "",
                    "",
                    filename,
                    "finished"
                )

    # ========================================================
    # 画质
    # ========================================================

    def get_format(self):

        if self.quality == "最佳画质":

            return "bv*+ba/b"

        if self.quality == "1080P":

            return (
                "bv*[height<=1080]+ba/"
                "bv*[height<=1080]/"
                "bv*+ba/b"
            )

        if self.quality == "720P":

            return (
                "bv*[height<=720]+ba/"
                "bv*[height<=720]/"
                "bv*+ba/b"
            )

        if self.quality == "480P":

            return (
                "bv*[height<=480]+ba/"
                "bv*[height<=480]/"
                "bv*+ba/b"
            )

        if self.quality == "360P":

            return (
                "bv*[height<=360]+ba/"
                "bv*[height<=360]/"
                "bv*+ba/b"
            )

        return "bv*+ba/b"

    # ========================================================
    # 接口客户端（首次使用时才创建，避免空跑也去请求网络）
    # ========================================================

    @property
    def api(self):

        if self._api is None:
            self._api = BilibiliApiClient(
                cookie_source=self.browser,
                cookie_file=COOKIE_FILE,
                headers=API_HEADERS,
                log=self.log
            )

        return self._api

    def _reset_api(self):
        """Drop a possibly rate-limited session before retrying the API."""

        self._api = BilibiliApiClient(
            cookie_source=self.browser,
            cookie_file=COOKIE_FILE,
            headers=API_HEADERS,
            log=self.log
        )

    def check_stop(self):

        if self.stop_flag and self.stop_flag():
            raise DownloadStopped()

    # ========================================================
    # 下载单个视频
    # ========================================================

    def download_one(self, url):

        self.log("")
        self.log("==========================================")
        self.log(f"开始处理：{url}")
        self.log("==========================================")

        is_video_page = bool(
            re.search(r"/video/(BV[0-9A-Za-z]+|av\d+)", url, re.IGNORECASE)
        )

        # Strip browser tracking parameters before any extractor sees the URL.
        # They are unnecessary for the API and can trigger Bilibili's 412 WAF.
        if is_video_page:
            url = canonicalize_bilibili_url(url)

        # 普通投稿走接口通道；番剧 / 课程 / 合集等交给 yt-dlp
        if is_video_page:

            try:
                return self._download_via_api_with_retry(url)

            except DownloadStopped:
                self.log("已中止当前下载")
                return False

            except Exception as e:
                self.log(f"接口通道失败：{e}")
                self.log("改用 yt-dlp 再试一次")

        try:
            return self.download_via_ytdlp(url)

        except DownloadStopped:
            self.log("已中止当前下载")
            return False

    # ========================================================
    # 接口通道：view + playurl + 自行下载 + ffmpeg 合并
    # ========================================================

    def _download_via_api_with_retry(self, url):
        """Download a normal BV/AV page through the API with one clean retry."""

        last_error = None
        for attempt in range(2):
            try:
                return self.download_via_api(url)
            except DownloadStopped:
                raise
            except Exception as exc:
                last_error = exc
                self.log("API channel failed: {} (attempt {}/2)".format(
                    exc, attempt + 1
                ))
                if attempt == 0:
                    self._reset_api()
                    time.sleep(1)

        raise last_error

    def download_via_api(self, url):

        bvid, aid, page = parse_video_url(url)

        if not bvid and not aid:
            raise RuntimeError("链接里找不到 BV 号或 av 号")

        info = self.api.get_view(bvid=bvid, aid=aid)

        pages = info.get("pages") or []

        if not pages:
            raise RuntimeError("接口没有返回分 P 信息")

        if page > len(pages):
            raise RuntimeError(
                f"该视频只有 {len(pages)} 个分 P，链接里却是 p={page}"
            )

        page_info = pages[page - 1]

        aid = info.get("aid") or aid
        bvid = info.get("bvid") or bvid or f"av{aid}"

        video_info = VideoInfo(
            platform="bilibili",
            bvid=bvid if str(bvid).upper().startswith("BV") else None,
            aid=aid,
            cid=page_info.get("cid") or info.get("cid") or 0,
            title=str(info.get("title") or "video"),
            uploader=str((info.get("owner") or {}).get("name") or "unknown"),
            uploader_id=(info.get("owner") or {}).get("mid"),
            page_number=page,
            page_count=len(pages),
            page_title=str(page_info.get("part") or ""),
            duration=int(page_info.get("duration") or info.get("duration") or 0),
            cover_url=info.get("pic"),
            description=str(info.get("desc") or ""),
            pubdate=info.get("pubdate"),
            original_url=url,
            canonical_url=canonicalize_bilibili_url(url),
        )

        uploader = sanitize_filename((info.get("owner") or {}).get("name"), 40)
        title = sanitize_filename(info.get("title"), 60)
        part = sanitize_filename(page_info.get("part"), 50)

        if len(pages) > 1:
            stem = f"{title} p{page:02d} {part}".strip()
            stem = f"{stem} [{bvid}_p{page}]"
        else:
            stem = f"{title} [{bvid}]"

        target_dir = self.download_dir / uploader
        target_dir.mkdir(parents=True, exist_ok=True)

        output_path = target_dir / f"{stem}.mp4"

        if output_path.exists():
            self.log(f"已存在，跳过：{output_path.name}")
            return True

        self.log(f"标题：{title}")

        if len(pages) > 1:
            self.log(f"分 P：p{page}/{len(pages)}　{part}")

        self.check_stop()

        play = self.api.get_play_info(
            aid,
            page_info["cid"],
            QUALITY_QN.get(self.quality, 80)
        )

        max_height = QUALITY_HEIGHT.get(self.quality, 1080)

        temp_video = target_dir / f".{bvid}_p{page}.video.tmp"
        temp_audio = target_dir / f".{bvid}_p{page}.audio.tmp"

        dash = play.get("dash")

        if dash:

            video, audio = select_dash_streams(dash, max_height)

            height = video.get("height") or 0

            self.log(f"选中画质：{height}P（qn={video.get('id')}）")

            if height < max_height and not self.api.logged_in:
                self.log(
                    "提示：更高画质需要登录，"
                    "可在“登录状态”里选浏览器，或把 cookies.txt 放到项目根目录"
                )

            self._download_stream(
                self._rank_urls(stream_urls(video)), temp_video, "视频"
            )

            if audio:
                self._download_stream(
                    self._rank_urls(stream_urls(audio)), temp_audio, "音频"
                )
                self._merge(temp_video, temp_audio, output_path)
            else:
                self.log("该视频没有独立音轨")
                self._remux(temp_video, output_path)

        else:

            durl = play.get("durl") or []

            if not durl:
                raise RuntimeError("playurl 既没有返回 dash 也没有 durl")

            self.log(f"该视频只提供整段文件（qn={play.get('quality')}）")

            self._download_stream(
                self._rank_urls(stream_urls(durl[0])), temp_video, "视频"
            )
            self._remux(temp_video, output_path)

        for temp in (temp_video, temp_audio):
            temp.unlink(missing_ok=True)

        # Metadata and cover are sidecar artifacts. They are best effort and
        # must not turn a valid media download into a failed task.
        metadata_json, cover_path = save_video_metadata(
            output_path,
            video_info,
            self.quality,
            play,
            session=self.api.session,
            headers=API_HEADERS,
        )
        self.log(f"metadata saved: {metadata_json.name}")
        if cover_path:
            self.log(f"cover saved: {cover_path.name}")
        subtitle_tracks = self.api.get_subtitle_tracks(aid, page_info["cid"], bvid)
        for subtitle_path in save_subtitle_tracks(
            output_path, subtitle_tracks, self.api.session, API_HEADERS
        ):
            self.log(f"subtitle saved: {subtitle_path.name}")
        danmaku_path = output_path.with_suffix(".xml")
        if self.api.download_danmaku(page_info["cid"], danmaku_path):
            self.log(f"danmaku saved: {danmaku_path.name}")
        self._embed_metadata(output_path, video_info)

        self.log(
            f"✓ 下载完成：{output_path.name}"
            f"（{format_size(output_path.stat().st_size)}）"
        )

        if self.progress_callback:
            self.progress_callback(
                "100%", "", "", output_path.name, "finished"
            )

        return True

    # ========================================================
    # 镜像线路测速
    # ========================================================

    def _rank_urls(self, urls, probe_seconds=1.5):
        """按实测速度给镜像排序。

        playurl 默认给的第一条线路不一定最快（实测过 200KiB/s 与 1.2MiB/s 的差距），
        所以每批任务开始时对各镜像短测一次，结果按主机名缓存复用。
        """

        if len(urls) < 2:
            return urls

        scored = []

        for url in urls:

            host = urllib.parse.urlparse(url).netloc

            if host not in self._mirror_speed:

                rate = self._probe_speed(url, probe_seconds)
                self._mirror_speed[host] = rate

                self.log(f"  线路 {host}：{format_size(rate)}/s")

            scored.append((self._mirror_speed[host], url))

        scored.sort(key=lambda item: item[0], reverse=True)

        return [url for _, url in scored]

    def _probe_speed(self, url, seconds):

        headers = dict(API_HEADERS)
        headers["Accept"] = "*/*"
        headers["Range"] = "bytes=0-"

        got = 0
        started_at = time.time()

        try:
            with self.api.session.get(
                url, headers=headers, stream=True, timeout=(10, 20)
            ) as response:

                response.raise_for_status()

                for chunk in response.iter_content(128 * 1024):
                    self.check_stop()
                    got += len(chunk)
                    if time.time() - started_at >= seconds:
                        break

        except DownloadStopped:
            raise

        except Exception:
            return 0.0

        return got / max(time.time() - started_at, 0.001)

    # ========================================================
    # 单流下载（断点续传 + 镜像切换）
    # ========================================================

    def _download_stream(self, urls, dest, label):

        if not urls:
            raise RuntimeError(f"{label}没有可用直链")

        last_error = None

        for attempt in range(self.max_retries):

            for url in urls:

                try:
                    self._download_stream_once(url, dest, label)
                    return

                except DownloadStopped:
                    raise

                except Exception as e:
                    last_error = e
                    self.log(f"  {label}下载中断：{e}")

            if attempt < self.max_retries - 1:
                wait = min(2 ** attempt, 10)
                self.log(
                    f"  {wait} 秒后重试"
                    f"（第 {attempt + 1}/{self.max_retries} 轮）"
                )
                time.sleep(wait)

        raise RuntimeError(f"{label}下载失败：{last_error}")

    def _download_stream_once(self, url, dest, label):

        done = dest.stat().st_size if dest.exists() else 0

        headers = dict(API_HEADERS)
        headers["Accept"] = "*/*"

        if done:
            headers["Range"] = f"bytes={done}-"

        with self.api.session.get(
            url,
            headers=headers,
            stream=True,
            timeout=(15, 60)
        ) as response:

            # 416 说明请求的起点已经超过文件末尾，即上次已经下完
            if done and response.status_code == 416:
                return

            if done and response.status_code == 200:
                self.log(f"  {label}服务器不支持续传，从头下载")
                done = 0

            response.raise_for_status()

            total = int(response.headers.get("Content-Length") or 0) + done

            with open(dest, "ab" if done else "wb") as f:

                started_at = time.time()
                started_bytes = done
                last_report = 0.0

                for chunk in response.iter_content(256 * 1024):

                    self.check_stop()

                    if not chunk:
                        continue

                    f.write(chunk)
                    done += len(chunk)

                    now = time.time()

                    if now - last_report < 0.3:
                        continue

                    last_report = now

                    elapsed = max(now - started_at, 0.001)
                    rate = (done - started_bytes) / elapsed

                    if not self.progress_callback:
                        continue

                    percent = f"{done / total * 100:.1f}%" if total else "0%"
                    eta = (
                        format_eta((total - done) / rate)
                        if total and rate > 0 else "--"
                    )

                    self.progress_callback(
                        percent,
                        f"{format_size(rate)}/s",
                        eta,
                        f"{label}　{dest.name}",
                        "downloading"
                    )

        actual = dest.stat().st_size

        if total and actual < total:
            raise RuntimeError(f"文件不完整（{actual}/{total} 字节）")

    # ========================================================
    # ffmpeg
    # ========================================================

    def _ffmpeg_path(self):

        candidate = FFMPEG_DIR / (
            "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        )

        if candidate.exists():
            return str(candidate)

        found = shutil.which("ffmpeg")

        if found:
            return found

        raise RuntimeError(
            "找不到 ffmpeg，请把 ffmpeg.exe 放到项目的 ffmpeg 目录下"
        )

    def _run_ffmpeg(self, args, output_path):

        cmd = [self._ffmpeg_path(), "-y", "-loglevel", "error", *args]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )

        if result.returncode != 0 or not output_path.exists():
            raise RuntimeError(
                f"ffmpeg 处理失败：{(result.stderr or '').strip()[:300]}"
            )

    def _merge(self, video_path, audio_path, output_path):

        self.log("正在用 ffmpeg 合并音视频...")

        self._run_ffmpeg(
            [
                "-i", str(video_path),
                "-i", str(audio_path),
                "-c", "copy",
                "-movflags", "+faststart",
                str(output_path),
            ],
            output_path
        )

    def _remux(self, media_path, output_path):

        self.log("正在封装为 MP4...")

        self._run_ffmpeg(
            [
                "-i", str(media_path),
                "-c", "copy",
                "-movflags", "+faststart",
                str(output_path),
            ],
            output_path
        )

    def _embed_metadata(self, output_path, video_info):
        """Write descriptive MP4 tags without re-encoding the streams."""

        temp_path = output_path.with_name(
            output_path.stem + ".metadata.tmp.mp4"
        )
        comment = (
            f"Bilibili {video_info.bvid or ('av' + str(video_info.aid))} "
            f"p{video_info.page_number} | {video_info.canonical_url}"
        )
        try:
            self._run_ffmpeg(
                [
                    "-i", str(output_path),
                    "-map", "0",
                    "-c", "copy",
                    "-metadata", f"title={video_info.title}",
                    "-metadata", f"artist={video_info.uploader}",
                    "-metadata", "album=Bilibili",
                    "-metadata", f"comment={comment}",
                    "-metadata", f"description={video_info.description}",
                    str(temp_path),
                ],
                temp_path,
            )
            os.replace(temp_path, output_path)
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            self.log(f"metadata embedding skipped: {exc}")

    # ========================================================
    # yt-dlp 兜底通道
    # ========================================================

    def download_via_ytdlp(self, url):

        options = {

            # 视频 + 音频
            "format": self.get_format(),

            # 自动合并 MP4
            "merge_output_format": "mp4",

            # FFmpeg
            "ffmpeg_location":
                self.ffmpeg_location,

            # 保存位置
            "paths": {
                "home":
                    str(self.download_dir)
            },

            # 文件名
            "outtmpl": {
                "default":
                    "%(uploader)s/%(title)s [%(id)s].%(ext)s"
            },

            # Windows 文件名兼容
            "windowsfilenames":
                True,

            # 自动重试
            "retries":
                self.max_retries,

            # Fragment 并发
            "concurrent_fragment_downloads":
                4,

            # 断点续传
            "continuedl":
                True,

            # 不覆盖
            "nooverwrites":
                True,

            # 显示进度
            "noprogress":
                False,

            # 不静默
            "quiet":
                False,

            # 日志
            "progress_hooks": [
                self.progress_hook
            ],

            # Explicit browser headers reduce false-positive 412 responses
            # when yt-dlp is used for non-standard Bilibili URLs.
            "http_headers": dict(API_HEADERS),

            # Retry extractor requests separately from media downloads.
            "extractor_retries": self.max_retries
        }

        # ----------------------------------------------------
        # 浏览器 Cookie
        # ----------------------------------------------------

        if self.browser == "cookies.txt":

            if COOKIE_FILE.exists():

                options["cookiefile"] = str(COOKIE_FILE)

                self.log(f"使用 {COOKIE_FILE.name}")

        elif self.browser != "不使用":

            browser_name = (
                self.browser.lower()
            )

            options[
                "cookiesfrombrowser"
            ] = (
                browser_name,
                None,
                None,
                None
            )

            self.log(
                f"使用 {self.browser} 浏览器 Cookie"
            )

        try:

            with yt_dlp.YoutubeDL(
                options
            ) as ydl:

                ydl.download(
                    [url]
                )

            self.log(
                "✓ 下载完成"
            )

            return True

        except Exception as e:

            self.log(
                "✗ 下载失败"
            )

            self.log(
                str(e)
            )

            return False


# ============================================================
# GUI
# ============================================================

class BilibiliApp:

    def __init__(
        self,
        root
    ):

        self.root = root

        self.root.title(
            "Bilibili 批量视频下载器"
        )

        self.root.geometry(
            "1000x760"
        )

        self.root.minsize(
            900,
            650
        )

        # 下载状态
        self.downloading = False

        self.stop_requested = False

        # Persistent task history allows diagnostics and future resume UI
        # without changing the existing Tkinter workflow.
        self.task_store = TaskStore(BASE_DIR / "tasks.db")

        # UI
        self.create_ui()

    # ========================================================
    # UI
    # ========================================================

    def create_ui(self):

        # ----------------------------------------------------
        # 标题
        # ----------------------------------------------------

        title = ttk.Label(
            self.root,
            text="Bilibili 批量视频下载器",
            font=(
                "Microsoft YaHei",
                20,
                "bold"
            )
        )

        title.pack(
            pady=15
        )

        # ----------------------------------------------------
        # URL 区域
        # ----------------------------------------------------

        url_frame = ttk.LabelFrame(
            self.root,
            text="视频链接（一行一个）"
        )

        url_frame.pack(
            fill="both",
            expand=True,
            padx=20,
            pady=5
        )

        self.url_text = tk.Text(
            url_frame,
            font=(
                "Consolas",
                10
            ),
            wrap="none"
        )

        self.url_text.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=10
        )

        # ----------------------------------------------------
        # 设置区域
        # ----------------------------------------------------

        settings = ttk.LabelFrame(
            self.root,
            text="下载设置"
        )

        settings.pack(
            fill="x",
            padx=20,
            pady=10
        )

        # 画质
        ttk.Label(
            settings,
            text="画质："
        ).grid(
            row=0,
            column=0,
            padx=10,
            pady=10
        )

        self.quality_var = tk.StringVar(
            value="1080P"
        )

        self.quality_box = ttk.Combobox(
            settings,
            textvariable=
                self.quality_var,
            state="readonly",
            values=[
                "最佳画质",
                "1080P",
                "720P",
                "480P",
                "360P"
            ],
            width=15
        )

        self.quality_box.grid(
            row=0,
            column=1,
            padx=10
        )

        # 并发
        ttk.Label(
            settings,
            text="同时下载："
        ).grid(
            row=0,
            column=2,
            padx=10
        )

        self.worker_var = tk.StringVar(
            value="1"
        )

        self.worker_box = ttk.Combobox(
            settings,
            textvariable=
                self.worker_var,
            state="readonly",
            values=[
                "1",
                "2",
                "3"
            ],
            width=10
        )

        self.worker_box.grid(
            row=0,
            column=3,
            padx=10
        )

        # Cookie
        ttk.Label(
            settings,
            text="登录状态："
        ).grid(
            row=1,
            column=0,
            padx=10,
            pady=10
        )

        self.browser_var = tk.StringVar(
            value="不使用"
        )

        self.browser_box = ttk.Combobox(
            settings,
            textvariable=
                self.browser_var,
            state="readonly",
            values=[
                "不使用",
                "cookies.txt",
                "Chrome",
                "Edge",
                "Firefox"
            ],
            width=15
        )

        self.browser_box.grid(
            row=1,
            column=1,
            padx=10
        )

        # 下载目录
        ttk.Label(
            settings,
            text="保存位置："
        ).grid(
            row=1,
            column=2,
            padx=10
        )

        self.path_var = tk.StringVar(
            value=str(
                DEFAULT_DOWNLOAD_DIR
            )
        )

        self.path_entry = ttk.Entry(
            settings,
            textvariable=
                self.path_var,
            width=35
        )

        self.path_entry.grid(
            row=1,
            column=3,
            padx=10
        )

        ttk.Button(
            settings,
            text="选择",
            command=
                self.choose_directory
        ).grid(
            row=1,
            column=4,
            padx=5
        )

        # ----------------------------------------------------
        # 进度区域
        # ----------------------------------------------------

        progress_frame = ttk.LabelFrame(
            self.root,
            text="当前任务"
        )

        progress_frame.pack(
            fill="x",
            padx=20,
            pady=5
        )

        self.current_file_var = tk.StringVar(
            value="等待下载..."
        )

        ttk.Label(
            progress_frame,
            textvariable=
                self.current_file_var
        ).pack(
            anchor="w",
            padx=10,
            pady=5
        )

        self.progress = ttk.Progressbar(
            progress_frame,
            orient="horizontal",
            mode="determinate"
        )

        self.progress.pack(
            fill="x",
            padx=10,
            pady=5
        )

        self.progress_info_var = tk.StringVar(
            value="0% | 速度：-- | ETA：--"
        )

        ttk.Label(
            progress_frame,
            textvariable=
                self.progress_info_var
        ).pack(
            anchor="w",
            padx=10,
            pady=5
        )

        # ----------------------------------------------------
        # 按钮
        # ----------------------------------------------------

        button_frame = ttk.Frame(
            self.root
        )

        button_frame.pack(
            fill="x",
            padx=20,
            pady=10
        )

        ttk.Button(
            button_frame,
            text="清空链接",
            command=
                self.clear_urls
        ).pack(
            side="left",
            padx=5
        )

        ttk.Button(
            button_frame,
            text="读取 urls.txt",
            command=
                self.load_urls
        ).pack(
            side="left",
            padx=5
        )

        ttk.Button(
            button_frame,
            text="Resume pending",
            command=self.load_pending_tasks,
        ).pack(side="left", padx=5)

        ttk.Button(
            button_frame,
            text="Task history",
            command=self.show_task_history,
        ).pack(side="left", padx=5)

        self.start_button = ttk.Button(
            button_frame,
            text="开始下载",
            command=
                self.start_download
        )

        self.start_button.pack(
            side="right",
            padx=5
        )

        self.stop_button = ttk.Button(
            button_frame,
            text="停止",
            command=
                self.stop_download,
            state="disabled"
        )

        self.stop_button.pack(
            side="right",
            padx=5
        )

        # ----------------------------------------------------
        # 日志
        # ----------------------------------------------------

        log_frame = ttk.LabelFrame(
            self.root,
            text="运行日志"
        )

        log_frame.pack(
            fill="both",
            expand=True,
            padx=20,
            pady=5
        )

        self.log_text = tk.Text(
            log_frame,
            height=8,
            font=(
                "Consolas",
                9
            )
        )

        self.log_text.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=10
        )

    # ========================================================
    # 日志
    # ========================================================

    def log(
        self,
        message
    ):

        self.root.after(
            0,
            self._append_log,
            message
        )

    def _append_log(
        self,
        message
    ):

        self.log_text.insert(
            tk.END,
            message + "\n"
        )

        self.log_text.see(
            tk.END
        )

    # ========================================================
    # URL
    # ========================================================

    def get_urls(self):

        text = self.url_text.get(
            "1.0",
            tk.END
        )

        urls = []

        for line in text.splitlines():

            line = line.strip()

            if not line:
                continue

            if line.startswith("#"):
                continue

            if (
                line.startswith(
                    "http://"
                )
                or
                line.startswith(
                    "https://"
                )
            ):

                urls.append(
                    line
                )

        # 去重
        return list(
            dict.fromkeys(
                urls
            )
        )

    # ========================================================
    # 清空
    # ========================================================

    def clear_urls(self):

        self.url_text.delete(
            "1.0",
            tk.END
        )

    # ========================================================
    # 读取 urls.txt
    # ========================================================

    def load_urls(self):

        file_path = (
            BASE_DIR /
            "urls.txt"
        )

        if not file_path.exists():

            messagebox.showwarning(
                "提示",
                "当前项目中没有 urls.txt"
            )

            return

        with open(
            file_path,
            "r",
            encoding="utf-8"
        ) as f:

            content = f.read()

        self.url_text.delete(
            "1.0",
            tk.END
        )

        self.url_text.insert(
            tk.END,
            content
        )

        self.log(
            "已读取 urls.txt"
        )

    # ========================================================
    # 选择目录
    # ========================================================

    def choose_directory(self):

        directory = (
            filedialog.askdirectory()
        )

        if directory:

            self.path_var.set(
                directory
            )

    # ========================================================
    # 开始下载
    # ========================================================

    def load_pending_tasks(self):
        """Load unfinished tasks from SQLite into the URL editor."""

        pending = self.task_store.pending()
        if not pending:
            self.log("No unfinished tasks found")
            return
        self.url_text.delete("1.0", tk.END)
        self.url_text.insert(tk.END, "\n".join(item["url"] for item in pending))
        self.log(f"Loaded {len(pending)} unfinished tasks")

    def show_task_history(self):
        """Display recent persisted tasks without blocking the downloader."""

        window = tk.Toplevel(self.root)
        window.title("Task history")
        window.geometry("900x360")
        columns = ("status", "quality", "url", "updated", "error")
        tree = ttk.Treeview(window, columns=columns, show="headings")
        headings = {
            "status": "Status", "quality": "Quality", "url": "URL",
            "updated": "Updated", "error": "Error",
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=120 if column != "url" else 360)
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        for item in self.task_store.recent():
            tree.insert(
                "", tk.END,
                values=(item["status"], item["quality"], item["url"],
                        item["updated_at"], item["error_message"] or ""),
            )

    def start_download(self):

        if self.downloading:

            return

        urls = self.get_urls()

        if not urls:

            messagebox.showwarning(
                "提示",
                "请先输入 Bilibili 视频链接。"
            )

            return

        self.downloading = True

        self.stop_requested = False

        self.start_button.config(
            state="disabled"
        )

        self.stop_button.config(
            state="normal"
        )

        self.log(
            f"发现 {len(urls)} 个视频链接。"
        )

        thread = threading.Thread(
            target=self.download_worker,
            args=(urls,),
            daemon=True
        )

        thread.start()

    # ========================================================
    # 下载线程
    # ========================================================

    def download_worker(
        self,
        urls
    ):

        download_dir = (
            self.path_var.get()
        )

        quality = (
            self.quality_var.get()
        )

        browser = (
            self.browser_var.get()
        )

        workers = int(
            self.worker_var.get()
        )

        self.log(
            f"画质：{quality}"
        )

        self.log(
            f"同时下载：{workers}"
        )

        self.log(
            f"保存位置：{download_dir}"
        )

        # ----------------------------------------------------
        # 当前版本采用顺序下载
        # ----------------------------------------------------
        # 这样更稳定。
        # 后续 V2 再加入真正的并发队列。
        # ----------------------------------------------------

        downloader = BilibiliDownloader(

            download_dir=
                download_dir,

            quality=
                quality,

            browser=
                browser,

            max_retries=
                5
        )

        downloader.log_callback = (
            self.log
        )

        downloader.progress_callback = (
            self.update_progress
        )

        # 让下载中的文件也能被"停止"打断
        downloader.stop_flag = (
            lambda: self.stop_requested
        )

        success = 0

        failed = 0

        total = len(
            urls
        )

        for index, url in enumerate(
            urls,
            start=1
        ):

            canonical_url = canonicalize_bilibili_url(url)
            task_id = self.task_store.upsert(
                canonical_url, quality, "PENDING"
            )

            if self.stop_requested:

                self.log(
                    "用户请求停止。"
                )

                break

            self.log(
                ""
            )

            self.log(
                f"========== "
                f"{index}/{total} "
                f"=========="
            )

            self.task_store.upsert(canonical_url, quality, "RESOLVING")

            result = (
                downloader.download_one(
                    url
                )
            )

            if result:

                success += 1
                self.task_store.upsert(
                    canonical_url, quality, "COMPLETED"
                )

            else:

                failed += 1
                self.task_store.upsert(
                    canonical_url, quality, "FAILED"
                )

        self.log(
            ""
        )

        self.log(
            "=========================================="
        )

        self.log(
            "任务结束"
        )

        self.log(
            f"成功：{success}"
        )

        self.log(
            f"失败：{failed}"
        )

        self.log(
            f"总数：{total}"
        )

        self.log(
            "=========================================="
        )

        self.root.after(
            0,
            self.download_finished
        )

    # ========================================================
    # 进度
    # ========================================================

    def update_progress(
        self,
        percent,
        speed,
        eta,
        filename,
        status
    ):

        self.root.after(
            0,
            self._update_progress_ui,
            percent,
            speed,
            eta,
            filename,
            status
        )

    def _update_progress_ui(
        self,
        percent,
        speed,
        eta,
        filename,
        status
    ):

        try:

            value = float(
                percent.replace(
                    "%",
                    ""
                )
            )

        except Exception:

            value = 0

        self.progress["value"] = value

        self.current_file_var.set(
            filename
        )

        self.progress_info_var.set(
            f"{percent} | "
            f"速度：{speed} | "
            f"ETA：{eta}"
        )

        if status == "finished":

            self.progress["value"] = 100

    # ========================================================
    # 停止
    # ========================================================

    def stop_download(self):

        if not self.downloading:

            return

        self.stop_requested = True

        self.log(
            "正在停止下载队列..."
        )

        self.stop_button.config(
            state="disabled"
        )

    # ========================================================
    # 完成
    # ========================================================

    def download_finished(self):

        self.downloading = False

        self.start_button.config(
            state="normal"
        )

        self.stop_button.config(
            state="disabled"
        )

        self.current_file_var.set(
            "下载任务结束"
        )


# ============================================================
# 主程序
# ============================================================

def main():

    root = tk.Tk()

    try:

        style = ttk.Style()

        if "vista" in style.theme_names():

            style.theme_use(
                "vista"
            )

    except Exception:

        pass

    app = BilibiliApp(
        root
    )

    root.mainloop()


if __name__ == "__main__":

    main()
