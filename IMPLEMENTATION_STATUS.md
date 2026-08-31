# 实施状态

最后更新：2026-08-31

## 已完成

- Git 基线和 `.gitignore`；
- BV/AV 链接标准化和分 P 解析模块；
- 官方 API 优先和 API 会话重试；
- DASH 音视频选择模块；
- Range 断点续传和 CDN 备用地址（原有功能）；
- MP4 流复制合并（原有功能）；
- MP4 标题、作者、简介和来源标签；
- JSON 元数据保存；
- 封面保存；
- 可用字幕自动保存为 SRT；
- SQLite 任务历史和状态记录；
- GUI 一键载入未完成任务；
- GUI 最近任务历史窗口；
- 独立 `app/parsers/bilibili.py` 官方 API 客户端；
- 可用弹幕 XML 自动保存；
- 弹幕 XML 转换为 ASS/SRT 字幕旁路文件（保留原始 XML）；
- `app/parsers` 平台解析器接口、注册表和 Bilibili 实现；
- `main.py` 旧 `BilibiliWebApi` 定义已降级为共享客户端兼容别名；
- `build.ps1` Windows PyInstaller 打包脚本；
- 已在 Windows 11 / Python 3.12 环境成功生成 `dist/BilibiliDownloader`；
- 11 项 URL、媒体、元数据、弹幕转换和解析器路由测试。

## 暂未完成

- 在全新、无 Python 环境的 Windows 电脑执行最终启动和下载验收；

## 发布审计备注

- 已补充 GitHub Actions 测试、MIT 许可证、安全说明和发布检查清单；
- `ffmpeg/` 二进制文件、下载内容、Cookie、任务数据库和打包产物均由 `.gitignore` 排除；
- `build.ps1` 会在检测到正在运行的 `BilibiliDownloader.exe` 时提前终止并给出提示；
- 已使用 PyInstaller 独立输出目录完成一次干净打包验证（包含 FFmpeg 运行文件）；
- 当前工作区未配置 Git 远程仓库，推送前需要由维护者添加自己的 GitHub remote。

## 当前验证命令

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
$files = @('main.py') + (Get-ChildItem app,tests -Recurse -Filter *.py | ForEach-Object { $_.FullName })
.\.venv\Scripts\python.exe -m py_compile $files
```
