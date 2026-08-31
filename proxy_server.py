"""
本地静态服务器 + API 反向代理

为什么需要它
------------
gorouter.app 之类的中转站放在 Cloudflare 后面，对浏览器的 CORS 预检（OPTIONS）
直接返回 403，且响应里没有 Access-Control-Allow-Origin。
因此网页端 fetch 必然失败（表现为 Failed to fetch），这不是前端代码的 bug。

解决办法：让浏览器请求同源的本机地址，由本机 Python 转发到中转站。
服务端之间的请求不受浏览器同源策略约束。

用法
----
    python proxy_server.py

然后浏览器打开：      http://127.0.0.1:8000/index.html
页面里 API Base URL： http://127.0.0.1:8000/api/v1

可选参数：
    python proxy_server.py --port 8000 --upstream https://gorouter.app

安全说明
--------
* 只监听 127.0.0.1，不对局域网/公网开放，因此代理本身不做鉴权。
  不要改成 0.0.0.0 暴露出去，否则同网段任何人都能借用你的转发通道。
* 本程序不保存、不记录 API Key，只是把浏览器发来的 Authorization 头原样转发。
"""

import argparse
import sys
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 代理前缀：/api/v1/chat/completions -> {UPSTREAM}/v1/chat/completions
API_PREFIX = "/api/"

# 需要转发给上游的请求头（其余一律丢弃，避免把 Origin/Referer 等带过去触发风控）
FORWARD_REQUEST_HEADERS = ("authorization", "content-type", "accept")

# 从上游响应中保留的头
KEEP_RESPONSE_HEADERS = ("content-type",)

# 伪装成常见浏览器 UA，减少被 Cloudflare 机器人规则拦截的概率
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

UPSTREAM = "https://gorouter.app"
TIMEOUT = 300


class ProxyHandler(SimpleHTTPRequestHandler):
    """静态文件 + /api/ 反向代理。"""

    protocol_version = "HTTP/1.1"

    # ---------------- 工具 ----------------

    def _is_api(self):
        return self.path.startswith(API_PREFIX)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "600")

    def _send_bytes(self, status, body, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    # ---------------- 请求分发 ----------------

    def do_OPTIONS(self):
        if self._is_api():
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self._cors()
            self.end_headers()
        else:
            self.send_error(405, "Method Not Allowed")

    def do_GET(self):
        if self._is_api():
            self._proxy("GET")
        else:
            super().do_GET()

    def do_HEAD(self):
        if self._is_api():
            self._proxy("HEAD")
        else:
            super().do_HEAD()

    def do_POST(self):
        if self._is_api():
            self._proxy("POST")
        else:
            self.send_error(405, "Method Not Allowed")

    # ---------------- 代理实现 ----------------

    def _proxy(self, method):
        target = UPSTREAM.rstrip("/") + "/" + self.path[len(API_PREFIX):].lstrip("/")

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        headers = {"User-Agent": BROWSER_UA, "Accept": "application/json"}
        for name in FORWARD_REQUEST_HEADERS:
            value = self.headers.get(name)
            if value:
                headers[name.title()] = value

        req = urllib.request.Request(target, data=body, headers=headers, method=method)

        print(f"[proxy] {method} {self.path} -> {target}", flush=True)

        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                payload = resp.read()
                ctype = resp.headers.get("Content-Type", "application/json")
                status = resp.status
        except urllib.error.HTTPError as e:
            payload = e.read() or b""
            ctype = e.headers.get("Content-Type", "text/plain") if e.headers else "text/plain"
            status = e.code
            print(f"[proxy] 上游返回 HTTP {status}", flush=True)
            if status == 403 and b"Cloudflare" in payload:
                hint = (
                    "上游 Cloudflare 拦截了本次请求（HTTP 403，返回的是人机校验页面）。\n"
                    "这不是本地代理的问题，可能原因：当前 IP 被风控、需要换出口网络，"
                    "或该中转站禁止此类直连。请用中转站官方给出的调用方式核对一次。"
                )
                self._send_bytes(
                    502,
                    _json_error(hint),
                )
                return
        except urllib.error.URLError as e:
            print(f"[proxy] 连接失败：{e.reason}", flush=True)
            self._send_bytes(
                502, _json_error(f"无法连接上游 {target}：{e.reason}")
            )
            return
        except Exception as e:  # noqa: BLE001 - 兜底，避免代理进程被单个请求打挂
            print(f"[proxy] 异常：{e}", flush=True)
            self._send_bytes(502, _json_error(f"代理内部错误：{e}"))
            return

        self.send_response(status)
        for name in KEEP_RESPONSE_HEADERS:
            if name == "content-type":
                self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self._cors()
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(payload)

    # 让日志安静一些，只保留代理相关输出
    def log_message(self, fmt, *args):
        if self._is_api():
            super().log_message(fmt, *args)


def _json_error(message):
    import json

    return json.dumps({"error": {"message": message}}, ensure_ascii=False).encode("utf-8")


def main():
    global UPSTREAM

    parser = argparse.ArgumentParser(description="本地静态服务器 + API 反向代理")
    parser.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")
    parser.add_argument(
        "--upstream",
        default=UPSTREAM,
        help="上游 API 根地址，默认 https://gorouter.app",
    )
    args = parser.parse_args()
    UPSTREAM = args.upstream.rstrip("/")

    handler = partial(ProxyHandler, directory=str(BASE_DIR))
    # 只绑定本机回环地址，不对外暴露
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)

    print("=" * 62)
    print("本地服务已启动（仅本机可访问）")
    print(f"  页面地址      : http://127.0.0.1:{args.port}/index.html")
    print(f"  API Base URL  : http://127.0.0.1:{args.port}/api/v1")
    print(f"  转发到        : {UPSTREAM}")
    print("按 Ctrl+C 停止")
    print("=" * 62)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
