# Bilibili 高可靠下载器扩展开发文档

文档版本：v1.0  
项目类型：Windows 桌面应用  
目标平台：Bilibili 公开或用户授权可访问的视频  
开发策略：保留现有功能，逐步重构和扩展，不一次性推倒重写

## 1. 项目目标

将当前 Bilibili 批量下载器升级为一个稳定的本地下载工具，重点解决以下问题：

1. 视频内容尽可能保持原始质量和原始编码。
2. 视频标题、UP 主、分 P 名称、封面和基本信息在本地保留。
3. 音视频使用流复制合并，不进行不必要的重新编码。
4. 网页 HTTP 412 等网页风控不应阻断正常视频下载，普通 BV/AV 链接优先使用官方接口。
5. 支持用户主动提供的登录 Cookie，以访问其有权限访问的画质和视频。
6. 支持断点续传、CDN 备用地址、失败重试和任务恢复。
7. 保留当前批量下载、分 P、画质选择、FFmpeg 合并和 GUI 功能。

本项目不实现验证码破解、会员限制破解、区域限制破解、盗用 Cookie 或其他访问控制绕过功能。

## 2. 当前系统基线

当前项目已经具备：

- Tkinter 图形界面；
- 批量输入和顺序下载；
- Bilibili BV/AV 和分 P 解析；
- `x/web-interface/view` 视频信息接口；
- WBI 播放地址接口；
- DASH 音视频流下载；
- Range 断点续传；
- CDN 备用地址和测速；
- FFmpeg `-c copy` 合并；
- `cookies.txt`、Chrome、Edge、Firefox Cookie 读取；
- yt-dlp 兜底通道。

当前主要问题：

- 下载、API、FFmpeg、GUI 集中在 `main.py`；
- 没有统一的解析结果数据模型；
- 元数据保存不完整；
- 缺少系统化的单元测试和集成测试；
- API 失败与网页抓取失败的错误边界不够清晰；
- 任务状态、缓存和恢复机制不完整。

## 3. 产品范围

### 3.1 第一版本必须实现

- BV/AV 标准链接解析；
- 含追踪参数的链接标准化；
- 分 P 解析和下载；
- 画质选择；
- API 优先获取视频信息和播放地址；
- 音视频原始流下载和无损合并；
- 标题、作者、分 P 名称写入文件名；
- MP4 基础元数据写入；
- 封面和 JSON 信息保存；
- Cookie 登录；
- 断点续传和 CDN 重试；
- 任务停止、失败重试和完成跳过。

### 3.2 第二版本可实现

- 字幕下载；
- 弹幕 XML 保存；
- 下载列表导入和导出；
- SQLite 任务记录；
- 任务历史和搜索；
- 多个 Bilibili 链接批量解析预览。

### 3.3 明确不在范围内

- 破解验证码；
- 破解会员或付费内容；
- 绕过区域限制；
- 伪造平台身份或批量规避风控；
- 未经授权的大规模爬取；
- 首个版本的多平台支持。

## 4. 用户流程

```text
用户粘贴链接
      ↓
URL 标准化和 BV/AV 识别
      ↓
调用 Bilibili 官方接口
      ↓
展示标题、UP主、分P、实际画质
      ↓
用户确认下载
      ↓
获取音视频流和备用地址
      ↓
断点下载临时文件
      ↓
FFmpeg 无损合并
      ↓
写入 MP4 元数据、封面和 JSON
      ↓
校验文件并标记完成
```

## 5. 总体架构

```text
GUI 层
  └─ TaskManager
       ├─ UrlParser
       ├─ BilibiliParser
       │    ├─ Session/Cookie
       │    ├─ View API
       │    └─ PlayURL API
       ├─ MediaResolver
       ├─ StreamDownloader
       ├─ FFmpegProcessor
       ├─ MetadataWriter
       └─ TaskStore
```

推荐目录：

```text
app/
├─ models.py              # VideoInfo、MediaStream、DownloadTask
├─ config.py              # 路径、请求头、默认配置
├─ errors.py              # 统一异常类型
├─ url_parser.py          # URL 标准化和参数解析
├─ parsers/
│  ├─ __init__.py
│  └─ bilibili.py         # Bilibili API、Cookie、WBI
├─ media/
│  ├─ resolver.py         # 画质和音视频流选择
│  ├─ downloader.py       # Range、重试、临时文件
│  └─ ffmpeg.py           # 合并、封装、元数据处理
├─ metadata/
│  └─ writer.py           # JSON、封面、文件名
├─ jobs/
│  ├─ manager.py          # 任务队列和状态
│  └─ store.py            # SQLite 或 JSON 持久化
└─ gui/
   └─ main_window.py      # Tkinter 界面
```

迁移期间可以保留 `main.py` 作为入口，逐步从旧代码导入新模块。

## 6. 核心数据结构

### 6.1 VideoInfo

```python
class VideoInfo:
    platform: str
    bvid: str | None
    aid: int | None
    cid: int
    title: str
    uploader: str
    uploader_id: int | None
    page_number: int
    page_count: int
    page_title: str
    duration: int
    cover_url: str | None
    description: str
    pubdate: int | None
    tags: list[str]
    original_url: str
    canonical_url: str
```

### 6.2 MediaStreams

```python
class MediaStreams:
    video_urls: list[str]
    audio_urls: list[str]
    video_codec: str
    audio_codec: str
    width: int
    height: int
    video_bandwidth: int
    audio_bandwidth: int
    duration: int
```

### 6.3 DownloadTask

```text
id
canonical_url
video_info
quality
output_path
status
progress
error_message
created_at
updated_at
```

状态必须使用固定值：

```text
PENDING → RESOLVING → READY → DOWNLOADING → MERGING → COMPLETED
                                      └──────→ FAILED
                                      └──────→ STOPPED
```

## 7. API 和解析设计

### 7.1 URL 解析

输入：任意 Bilibili 视频链接。  
输出：`bvid/aid/page/canonical_url`。

标准化规则：

- 保留 BV/AV 标识；
- 保留 `p` 分 P 参数；
- 删除 `spm_id_from`、`vd_source` 等追踪参数；
- 对无法识别的链接返回明确错误。

### 7.2 视频信息接口

调用 `x/web-interface/view`，获得：

- 标题；
- UP 主；
- 分 P 列表；
- CID；
- 封面；
- 发布时间；
- 简介和统计信息。

### 7.3 播放地址接口

优先调用 WBI 签名播放接口，失败时调用兼容接口。

请求必须支持：

- qn 画质参数；
- DASH 参数；
- Cookie；
- User-Agent；
- Referer；
- Origin；
- 3～5 次重试；
- 会话重建。

### 7.4 错误分类

```text
412：网页风控或请求条件不满足，继续使用 API 和会话重试
403：可能缺少权限、Cookie 或有效播放地址
404：视频、分P或接口地址不存在
登录限制：提示用户导入自己的 Cookie
会员/区域/验证码：提示无权访问，不无限重试
网络错误：按退避策略重试
```

## 8. 媒体下载设计

### 8.1 画质选择

- 在用户限制范围内选择最高分辨率；
- 优先 AVC，其次 HEVC，最后 AV1；
- 音频选择最高可用码率；
- 无独立音频时使用视频流自带音频；
- 显示实际分辨率和编码，不只显示用户选择的画质。

### 8.2 临时文件

```text
目标文件.mp4
.BVxxxx_p7.video.tmp
.BVxxxx_p7.audio.tmp
.BVxxxx_p7.json.tmp
```

要求：

- 下载中断保留临时文件；
- 下次运行根据文件大小继续 Range 下载；
- 主地址失败切换备用地址；
- 所有地址失败后进入重试等待；
- 合并成功后删除临时文件；
- 输出文件存在且大小大于 0 才能标记完成。

### 8.3 无损合并

FFmpeg 默认使用：

```text
-c copy -movflags +faststart
```

禁止在默认流程中重新编码。合并后检查音频轨和视频轨是否存在。

## 9. 元数据设计

### 9.1 文件名

```text
下载目录/UP主/标题 p07 分P标题 [BVxxxx_p7].mp4
```

### 9.2 MP4 元数据

写入以下字段：

- title：视频标题；
- artist：UP 主；
- album：Bilibili；
- date：发布时间；
- comment：原始链接、BV 号、分 P 信息；
- description：视频简介。

### 9.3 JSON 文件

与视频同名保存，记录完整解析结果、实际下载画质、编码、下载时间和错误重试记录。

### 9.4 封面

- 下载原始封面；
- 保存为同名 JPG；
- 可选使用 FFmpeg 嵌入 MP4；
- 封面下载失败不应阻断视频下载。

## 10. GUI 功能要求

必须保留：

- 批量链接输入；
- 画质选择；
- Cookie 来源选择；
- 下载目录选择；
- 开始、停止按钮；
- 进度、速度、ETA；
- 实时日志。

新增：

- 解析预览按钮；
- 视频标题和分 P 预览；
- 实际画质显示；
- 任务状态显示；
- 失败任务重新下载；
- 打开输出目录；
- 是否保存封面、JSON、字幕的选项。

## 11. 分阶段开发计划

### Milestone 0：基线保护，0.5 天

交付物：

- 当前版本备份；
- Git 初始提交；
- 现有程序可启动；
- 记录一组可复现测试链接。

完成标准：不修改功能即可启动和运行。

### Milestone 1：URL 和数据模型，1～2 天

交付物：

- `url_parser.py`；
- `models.py`；
- BV/AV、分 P、追踪参数测试。

完成标准：所有输入链接能转换为统一 `VideoInfo` 基础结构。

### Milestone 2：Bilibili API 模块，2～3 天

交付物：

- 独立的 Bilibili Parser；
- Cookie 和 WBI 管理；
- API 重试和错误分类。

完成标准：普通视频不访问网页 HTML，能够解析标题、分 P、CID 和播放地址。

### Milestone 3：媒体下载模块，2～3 天

交付物：

- 独立 StreamDownloader；
- Range 续传；
- CDN 切换；
- FFmpeg 合并校验。

完成标准：下载中断后重新启动可以继续，生成的 MP4 可正常播放。

### Milestone 4：元数据模块，1～2 天

交付物：

- 文件名生成；
- MP4 元数据写入；
- 封面保存；
- JSON 保存。

完成标准：视频文件、封面和 JSON 信息一一对应。

### Milestone 5：任务管理和 GUI，2～3 天

交付物：

- 任务状态机；
- 失败重试；
- 任务恢复；
- 解析预览。

完成标准：用户能看到每个任务当前阶段和明确的失败原因。

### Milestone 6：测试、打包和发布，2～3 天

交付物：

- 单元测试；
- API 集成测试；
- 全新电脑测试；
- Windows 打包版本；
- 使用说明和故障排查文档。

完成标准：在没有 Python 开发环境的电脑上可以完成一次完整下载。

## 12. 测试方案

### 12.1 单元测试

- URL 标准化；
- 分 P 参数；
- 文件名清理；
- 画质选择；
- CDN 排序；
- 重试计数；
- 任务状态迁移；
- 元数据 JSON 写入。

### 12.2 集成测试

使用以下类型的测试链接：

- 单 P 视频；
- 多 P 视频；
- 带 `spm_id_from` 的复制链接；
- 需要 Cookie 的高画质视频；
- 存在备用 CDN 的视频；
- 中途中断后继续下载的视频。

### 12.3 HTTP 412 验收

对于普通 BV/AV 链接：

- 日志中不应出现网页 `Downloading webpage` 作为首选流程；
- 应先调用官方 API；
- API 临时失败时应重建会话并重试；
- 不能把 HTTP 412 简单重复重试数十次；
- 无权限时应显示权限错误，而不是伪装成网络错误。

## 13. 配置和发布

配置项应集中在 `config.py`：

- 下载目录；
- 默认画质；
- 最大重试次数；
- 请求超时；
- 并发数；
- 是否保存封面；
- 是否保存 JSON；
- FFmpeg 路径。

发布目录：

```text
BilibiliDownloader/
├─ BilibiliDownloader.exe
├─ ffmpeg/ffmpeg.exe
├─ ffmpeg/ffprobe.exe
├─ downloads/
├─ cookies.txt.example
├─ README.md
└─ requirements.txt
```

Cookie 文件不能写入 Git，也不能进入普通日志。

## 14. 风险和处理策略

| 风险 | 影响 | 处理方式 |
|---|---|---|
| Bilibili 接口变更 | API 解析失败 | 独立 Parser、接口版本隔离、集成测试 |
| HTTP 412 | 网页提取失败 | 普通视频优先官方 API，不依赖网页 HTML |
| Cookie 失效 | 高画质或登录视频失败 | 明确提示重新导入 Cookie |
| CDN 地址过期 | 下载中断 | 获取地址后尽快下载，失败时重新解析 |
| FFmpeg 缺失 | 无法生成 MP4 | 启动时检查并提示路径 |
| 标题含非法字符 | 文件保存失败 | 统一文件名清理和长度限制 |
| 平台权限限制 | 无法访问内容 | 显示权限错误，不无限重试 |
| 页面结构变化 | 元数据缺失 | API 字段缺失时使用默认值 |

## 15. 开发完成定义

只有以下条件全部满足，才认为第一版本完成：

- 原有批量和分 P 下载功能正常；
- 普通 BV/AV 链接不依赖网页抓取；
- HTTP 412 不会立即导致下载失败；
- API 有会话重建和有限重试；
- 支持用户 Cookie；
- 音视频保持原始编码并使用流复制合并；
- 标题、作者、分 P 信息保留；
- MP4 元数据、封面和 JSON 可保存；
- 支持断点续传和 CDN 切换；
- 任务可停止、重试和恢复；
- 打包版本可在新电脑运行；
- 测试链接全部通过验收。

## 16. 推荐执行顺序

```text
基线备份
  ↓
URL 和数据模型
  ↓
Bilibili API 独立化
  ↓
媒体下载和续传
  ↓
FFmpeg 和元数据
  ↓
任务管理和 GUI
  ↓
测试、打包、发布
```

不要在第一阶段同时加入多平台解析、复杂爬虫或高并发代理池。先完成一个稳定、可验证、可恢复的 Bilibili 专用版本，再根据实际需求扩展。
