# Bilibili 批量视频下载器

Tkinter 界面，粘贴一批链接（含 `?p=N` 分 P）后顺序下载，产物为 MP4。

## 运行

```bat
pip install -r requirements.txt
python main.py
```

`ffmpeg/ffmpeg.exe` 已随项目提供，用于合并音视频；也可用系统 PATH 里的 ffmpeg。

## 关于 HTTP 412

B 站的风控网关会拦截对 `https://www.bilibili.com/video/<BV号>` 这个 **网页** 的
非浏览器请求，直接返回 `HTTP 412 Precondition Failed`（响应体是"出错啦"页面）。
yt-dlp 的 BiliBili 提取器第一步就是抓这个网页，所以整条链路都会失败，
换 UA、加 Referer、补 buvid3 Cookie、升级 yt-dlp 都无效。

同一网络下 `api.bilibili.com` 的官方接口仍然正常，因此下载改为：

1. `x/web-interface/view` 拿标题、UP 主、分 P 列表（cid）；
2. `x/player/wbi/playurl`（wbi 签名，失败时退回旧版 `x/player/playurl`）拿 DASH 直链；
3. 自行下载音视频流（带 Range 断点续传、镜像切换、镜像测速）；
4. `ffmpeg -c copy` 合并成 MP4。

普通投稿走上面的接口通道；番剧 / 课程等其他形态的链接仍交给 yt-dlp 兜底。

## 画质与登录

未登录时 B 站只返回 480P 及以下画质，所以"画质"选 1080P 也只会拿到 480P。
需要更高画质就在"登录状态"里选：

- `cookies.txt`：用浏览器插件（Get cookies.txt LOCALLY 之类）导出 bilibili 的
  Netscape 格式 Cookie，放到项目根目录，命名为 `cookies.txt`；
- `Chrome` / `Edge` / `Firefox`：直接读浏览器 Cookie（新版 Chrome/Edge 的加密
  可能读取失败，失败会退回未登录并在日志里提示）。

## 其他说明

- 文件保存为 `下载目录/UP主/标题 pNN 分P名 [BV号_pN].mp4`，已存在则跳过；
- 中途点"停止"会立即中断当前文件，残留的 `.tmp` 下次会断点续传；
- 同一批任务只对各 CDN 镜像测速一次，优先用实测最快的线路（实测过 200KiB/s
  与 1.2MiB/s 的差距）。
