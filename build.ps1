param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$running = Get-Process -Name "BilibiliDownloader" -ErrorAction SilentlyContinue
if ($running) {
    throw "BilibiliDownloader is currently running. Close it before building."
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment not found: $python"
}

foreach ($binary in @("ffmpeg\ffmpeg.exe", "ffmpeg\ffprobe.exe")) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $binary))) {
        throw "Required FFmpeg binary not found: $(Join-Path $PSScriptRoot $binary)"
    }
}

if ($Clean) {
    Remove-Item -LiteralPath (Join-Path $PSScriptRoot "build") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $PSScriptRoot "dist") -Recurse -Force -ErrorAction SilentlyContinue
}

Set-Location -LiteralPath $projectRoot

& $python -m pip show pyinstaller *> $null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed. Run: $python -m pip install -r requirements-dev.txt"
}

$args = @(
    "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
    "--name", "BilibiliDownloader",
    "--add-binary", "ffmpeg\ffmpeg.exe;ffmpeg",
    "--add-binary", "ffmpeg\ffprobe.exe;ffmpeg",
    "main.py"
)
& $python @args
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Copy-Item -LiteralPath "README.md" -Destination "dist\README.md" -Force
Copy-Item -LiteralPath "DEVELOPMENT_SPEC.md" -Destination "dist\DEVELOPMENT_SPEC.md" -Force
Copy-Item -LiteralPath "LICENSE" -Destination "dist\LICENSE" -Force
Copy-Item -LiteralPath "cookies.txt.example" -Destination "dist\cookies.txt.example" -Force
Write-Host "Build completed: dist\BilibiliDownloader"
