import hashlib
import http.cookiejar
import time
from pathlib import Path
from typing import Any, Callable
import urllib.parse

import requests


BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43,
    5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16,
    24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59,
    6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


class BilibiliApiClient:
    """Official-interface client for authorized/public Bilibili content."""

    def __init__(
        self,
        cookie_source: str | None = None,
        cookie_file: Path | None = None,
        log: Callable[[str], None] | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.log = log or (lambda message: None)
        self.cookie_file = Path(cookie_file) if cookie_file else None
        self.session = requests.Session()
        self.session.headers.update(headers or DEFAULT_HEADERS)
        self._mixin_key = None
        self.logged_in = False
        self._load_cookies(cookie_source)
        self._ensure_buvid()
        self._check_login()

    def _load_cookies(self, cookie_source):
        if not cookie_source or cookie_source in {"不使用", "涓嶄娇鐢?"}:
            return
        if cookie_source == "cookies.txt":
            if not self.cookie_file or not self.cookie_file.exists():
                self.log("cookies.txt not found; using anonymous access")
                return
            try:
                jar = http.cookiejar.MozillaCookieJar(str(self.cookie_file))
                jar.load(ignore_discard=True, ignore_expires=True)
                self.session.cookies.update(jar)
                self.log(f"loaded {self.cookie_file.name}")
            except Exception as exc:
                self.log(f"cookie file failed: {exc}")
            return
        try:
            from yt_dlp.cookies import extract_cookies_from_browser
            jar = extract_cookies_from_browser(cookie_source.lower())
            for cookie in jar:
                if "bilibili" in (cookie.domain or ""):
                    self.session.cookies.set_cookie(cookie)
            self.log(f"loaded {cookie_source} browser cookies")
        except Exception as exc:
            self.log(f"browser cookie failed: {exc}")

    def _request_json(self, url, params=None, retries=3):
        last_error = None
        for attempt in range(retries):
            try:
                response = self.session.get(url, params=params, timeout=(10, 20))
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last_error = exc
                if attempt + 1 < retries:
                    time.sleep(min(1.5 * (attempt + 1), 5))
        raise RuntimeError(f"request failed: {url}: {last_error}")

    def _get_json(self, url, params=None, signed=False):
        if signed:
            params = self._sign(params or {})
        payload = self._request_json(url, params=params)
        if payload.get("code") != 0:
            raise RuntimeError(
                f"API {payload.get('code')}: {payload.get('message')}"
            )
        return payload.get("data") or {}

    def _ensure_buvid(self):
        if self.session.cookies.get("buvid3"):
            return
        try:
            data = self._get_json("https://api.bilibili.com/x/frontend/finger/spi")
            for name, key in (("buvid3", "b_3"), ("buvid4", "b_4")):
                if data.get(key):
                    self.session.cookies.set(
                        name, data[key], domain=".bilibili.com", path="/"
                    )
        except Exception as exc:
            self.log(f"device fingerprint skipped: {exc}")

    def _check_login(self):
        try:
            nav = self._request_json("https://api.bilibili.com/x/web-interface/nav")
            self.logged_in = bool((nav.get("data") or {}).get("isLogin"))
        except Exception:
            self.logged_in = False

    def _get_mixin_key(self):
        if self._mixin_key:
            return self._mixin_key
        nav = self._request_json("https://api.bilibili.com/x/web-interface/nav")
        image = (nav.get("data") or {}).get("wbi_img") or {}

        def key_of(value):
            return value.rsplit("/", 1)[-1].split(".")[0]

        raw = key_of(image.get("img_url", "")) + key_of(image.get("sub_url", ""))
        if len(raw) < 64:
            raise RuntimeError("WBI key unavailable")
        self._mixin_key = "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]
        return self._mixin_key

    def _sign(self, params):
        signed = dict(params)
        signed["wts"] = int(time.time())
        query = urllib.parse.urlencode(sorted(signed.items()))
        signed["w_rid"] = hashlib.md5(
            (query + self._get_mixin_key()).encode()
        ).hexdigest()
        return signed

    def get_view(self, bvid=None, aid=None):
        params = {"bvid": bvid} if bvid else {"aid": aid}
        return self._get_json("https://api.bilibili.com/x/web-interface/view", params)

    def get_play_info(self, aid, cid, qn):
        params = {
            "avid": aid, "cid": cid, "qn": qn,
            "fnval": 4048, "fnver": 0, "fourk": 1,
        }
        try:
            return self._get_json(
                "https://api.bilibili.com/x/player/wbi/playurl",
                params, signed=True,
            )
        except Exception as exc:
            self.log(f"WBI playurl fallback: {exc}")
            return self._get_json(
                "https://api.bilibili.com/x/player/playurl", params
            )

    def get_subtitle_tracks(self, aid, cid, bvid=None):
        params = {"aid": aid, "cid": cid}
        if bvid:
            params["bvid"] = bvid
        try:
            data = self._get_json(
                "https://api.bilibili.com/x/player/wbi/v2", params, signed=True
            )
            return ((data.get("subtitle") or {}).get("list") or [])
        except Exception as exc:
            self.log(f"subtitle lookup skipped: {exc}")
            return []

    def download_danmaku(self, cid, output_path: Path) -> bool:
        """Save the public XML danmaku endpoint when it is available."""
        try:
            response = self.session.get(
                "https://api.bilibili.com/x/v1/dm/list.so",
                params={"oid": cid},
                headers={**DEFAULT_HEADERS, "Accept": "application/xml, text/xml, */*"},
                timeout=(10, 30),
            )
            response.raise_for_status()
            if not response.content:
                return False
            output_path.write_bytes(response.content)
            return True
        except Exception as exc:
            self.log(f"danmaku skipped: {exc}")
            return False

