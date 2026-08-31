# GitHub 发布检查清单

## 发布前

- [ ] 确认 `git status` 中没有 `cookies.txt`、下载视频、`tasks.db`、`dist/` 或 `build/`。
- [ ] 确认 `ffmpeg/` 只保留说明文件；不要直接提交大型二进制文件，除非已完成许可证审查。
- [ ] 在干净虚拟环境安装 `requirements.txt` 并运行测试。
- [ ] 在 Windows 上关闭正在运行的 `BilibiliDownloader.exe`，执行 `./build.ps1`。
- [ ] 从 `dist/BilibiliDownloader` 启动程序，完成一次真实下载和断点续传验证。
- [ ] 验证输出目录按 UP 主归类，MP4、JSON、封面和字幕/弹幕旁路文件均可打开。

## 推送前

- [ ] 检查 README、LICENSE、SECURITY.md 和版本说明是否齐全。
- [ ] 检查 GitHub Actions 测试通过。
- [ ] 检查提交历史中没有误提交 Cookie、Authorization、个人下载内容或本地数据库。
- [ ] 为公开版本创建 Git tag，例如 `v0.1.0`。

## GitHub Release 建议

源码仓库建议只发布源代码、脚本和文档。PyInstaller 文件夹版本可以作为 Release 附件提供，但应在 Release 说明中注明：

- FFmpeg 的来源和对应许可证；
- Windows 系统要求；
- Cookie 需要用户自行配置；
- 仅下载用户有权访问和保存的内容。
