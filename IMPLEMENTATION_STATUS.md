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
- 7 项 URL、媒体、元数据和路由测试。

## 暂未完成

- GUI 中的完整任务历史列表；
- 弹幕下载和格式转换；
- 完整的 Bilibili Parser 类独立化（当前 API 类仍在 `main.py`，共享 URL/媒体模块已建立）；
- 多平台解析器；
- Windows 最终打包脚本。

## 当前验证命令

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
$files = @('main.py') + (Get-ChildItem app,tests -Recurse -Filter *.py | ForEach-Object { $_.FullName })
.\.venv\Scripts\python.exe -m py_compile $files
```
