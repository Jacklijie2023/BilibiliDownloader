# Bilibili 批量视频下载器

一个基于 Tkinter 的 Windows 桌面下载工具。输入一批 Bilibili 视频链接后，程序会优先调用官方 API 获取视频信息和媒体直链，再使用 FFmpeg 无损合并为 MP4。
<img width="1920" height="1015" alt="Snipaste_2026-09-01_00-34-57" src="https://github.com/user-attachments/assets/32e88cae-c921-4d7f-95e6-b33bc5600ffe" />


## 功能概览

- 支持 BV、AV 链接和 `?p=N` 分 P 下载；支持批量链接、自动去重。
- 支持最佳画质、1080P、720P、480P、360P。实际画质取决于账号权限和视频可用流。
- 普通 BV/AV 视频优先走 Bilibili 官方 API，避免网页抓取触发 HTTP 412；番剧、课程等非标准链接回退到 yt-dlp。
- 支持 `cookies.txt`、Chrome、Edge、Firefox Cookie，以访问账号授权的画质和内容。
- 支持 DASH 音视频流、AVC/HEVC/AV1 编码优先级、CDN 备用地址和镜像测速。
- 支持 HTTP Range 断点续传、失败重试、临时文件恢复以及手动停止。
- 使用 FFmpeg `-c copy` 和 `-movflags +faststart` 合并，不进行默认重编码。
- 保存标题、UP 主、分 P 名称、简介、来源、日期等 MP4 元数据。
- 保存 JSON 元数据、封面 JPG、可用字幕 SRT，以及弹幕原始 XML 和 ASS/SRT 转换文件。
- SQLite 保存任务历史和状态；GUI 可加载未完成任务、查看历史、显示进度/速度/ETA/日志。

## 输出目录和文件命名

批量下载同一个 UP 主的视频时，程序会自动归类到同一个 UP 主文件夹。API 下载路径使用以下结构：

```text
下载目录/
└── UP主名称/
    ├── 视频标题 [BV号].mp4
    ├── 视频标题 [BV号].json
    ├── 视频标题 [BV号].jpg
    ├── 视频标题 [BV号].xml
    ├── 视频标题 [BV号].danmaku.ass
    └── 视频标题 [BV号].zh-CN.srt
```

多 P 视频会额外保留分 P 信息，例如：

```text
视频标题 p02 分P标题 [BVxxxx_p2].mp4
```

标题、UP 主名称和分 P 名称来自官方 API。Windows 文件名中的非法字符会被替换，过长标题会被截断；因此“基本信息保持一致”，但文件名会经过安全清理。已存在的目标文件会跳过。

非标准链接回退到 yt-dlp 时，模板同样是 `下载目录/UP主/标题 [视频ID].扩展名`，但最终名称取决于 yt-dlp 返回的元数据。

## 运行方式

在项目根目录执行：

```powershell
python -m pip install -r requirements.txt
python main.py
```

如果项目已创建虚拟环境：

```powershell
.\.venv\Scripts\python.exe main.py
```

## Cookie 登录

未登录时，Bilibili 通常只提供 480P 及以下画质。需要更高画质时，可在 GUI 的“登录状态”中选择：

1. `cookies.txt`：使用浏览器 Cookie 导出插件导出 Netscape 格式文件，放在项目根目录并命名为 `cookies.txt`；
2. `Chrome`、`Edge` 或 `Firefox`：直接读取浏览器 Cookie。若浏览器加密导致读取失败，程序会记录提示并退回匿名访问。

Cookie 不会写入普通下载日志，也不应提交到 GitHub。

## GUI 操作

1. 每行粘贴一个视频链接，也可以点击“读取 urls.txt”。
2. 选择画质、Cookie 来源和保存目录。
3. 点击“开始下载”。
4. 下载中可以查看当前文件、进度、速度、ETA 和日志。
5. 中途点击“停止”后，临时文件会保留；下次可点击“Resume pending”继续未完成任务。
6. 点击“Task history”查看 SQLite 中的任务状态和错误信息。

## 依赖和 FFmpeg

依赖见 [requirements.txt](requirements.txt)。项目的 `ffmpeg/` 目录应包含 `ffmpeg.exe` 和 `ffprobe.exe`；如果本地没有，也可以使用系统 PATH 中的 FFmpeg。由于二进制文件体积和许可证因素，FFmpeg 不提交到 GitHub，请按照 [ffmpeg/README.md](ffmpeg/README.md) 准备。

## 测试和编译检查

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

$files = @('main.py') + (
    Get-ChildItem app,tests -Recurse -Filter *.py |
    ForEach-Object { $_.FullName }
)
.\.venv\Scripts\python.exe -m py_compile $files
```

当前测试覆盖 URL 解析、DASH 流选择、元数据、字幕、弹幕转换、任务存储和解析器路由。

## Windows 打包

在具有 Python 和 PyInstaller 的开发环境中执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\build.ps1
```

产物位于 `dist/BilibiliDownloader`。发布时请同时提供说明文档，并提醒用户自行放置授权 Cookie；不要把真实 Cookie、下载视频或本地数据库提交到仓库。

## 项目结构

```text
app/
├── models.py                 # VideoInfo 等数据模型
├── url_parser.py             # BV/AV 和分 P URL 解析
├── parsers/
│   ├── base.py               # 平台解析器接口
│   ├── registry.py           # 解析器注册和平台识别
│   └── bilibili.py           # Bilibili 官方 API 客户端
├── media/resolver.py         # DASH 音视频流选择
├── metadata/writer.py        # JSON、封面、字幕、弹幕转换
└── jobs/store.py             # SQLite 任务历史
main.py                      # Tkinter 入口和下载流程
```

当前实际下载器是 Bilibili 专用；`app/parsers` 已提供多平台扩展接口，但尚未内置其他平台下载实现。

仓库中的 `proxy_server.py` 和 `index.html` 是独立的本地静态页面/代理示例，不是 Bilibili 下载流程的依赖；运行下载器只需要 `main.py` 及 `app/` 目录。

## 已知限制和注意事项

- 实际可用画质、字幕和弹幕取决于视频、账号权限及 Bilibili 接口返回结果。
- 网络异常、Cookie 失效、区域/会员权限限制无法通过无限重试绕过。
- 首次在全新且没有 Python 的 Windows 电脑上使用时，应对 PyInstaller 打包版本进行一次实际启动和下载验收。
- 请仅下载你有权访问和保存的内容，并遵守 Bilibili 服务条款及版权法律。

更多设计背景和验收记录见 [DEVELOPMENT_SPEC.md](DEVELOPMENT_SPEC.md)、[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) 和 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)。

## 许可证

本项目代码采用 [MIT License](LICENSE)。FFmpeg、yt-dlp 和其他第三方组件遵循各自的许可证；再分发打包版本时请同时遵守这些组件的许可要求。
