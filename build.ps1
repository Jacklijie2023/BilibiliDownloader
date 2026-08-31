param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment not found: $python"
}

if ($Clean) {
    Remove-Item -LiteralPath (Join-Path $PSScriptRoot "build") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $PSScriptRoot "dist") -Recurse -Force -ErrorAction SilentlyContinue
}

& $python -m pip show pyinstaller *> $null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed. Run: $python -m pip install -r requirements-dev.txt"
}

$args = @(
    "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
    "--name", "BilibiliDownloader",
    "--add-binary", "ffmpeg\ffmpeg.exe;ffmpeg",
    "--add-binary", "ffmpeg\ffprobe.exe;ffmpeg",
    "--add-data", "index.html;.",
    "main.py"
)
& $python @args
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Copy-Item -LiteralPath "README.md" -Destination "dist\README.md" -Force
Copy-Item -LiteralPath "DEVELOPMENT_SPEC.md" -Destination "dist\DEVELOPMENT_SPEC.md" -Force
Write-Host "Build completed: dist\BilibiliDownloader"
