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
- `build.ps1` Windows PyInstaller 打包脚本；
- 7 项 URL、媒体、元数据和路由测试。

## 暂未完成

- 弹幕格式转换（当前保存原始 XML）；
- 清理 `main.py` 中已废弃的旧 API 类定义；
- 多平台解析器；
- 在全新 Windows 环境执行最终打包验收。

## 当前验证命令

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
$files = @('main.py') + (Get-ChildItem app,tests -Recurse -Filter *.py | ForEach-Object { $_.FullName })
.\.venv\Scripts\python.exe -m py_compile $files
```
