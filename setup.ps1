$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PreviousCache = $env:UV_CACHE_DIR
Push-Location -LiteralPath $ProjectRoot
try {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ and rerun setup.ps1.'
    }
    $env:UV_CACHE_DIR = Join-Path $ProjectRoot '.uv-cache'
    uv python install 3.11
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 preparation failed.' }
    uv sync --locked
    if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed.' }
    & '.\.venv\Scripts\python.exe' -c 'import joblib, numpy, pandas, sklearn; print("Base imports: OK")'
    if ($LASTEXITCODE -ne 0) { throw 'Base imports failed.' }
    & '.\.venv\Scripts\python.exe' production_session.py check-host
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Codex CLI is unavailable. Configure your existing Codex CLI/login before a research run.' }
    Write-Host 'Dependencies ready. Read README.md before prepare/run. No fitting or model call was started.'
} finally {
    $env:UV_CACHE_DIR = $PreviousCache
    Pop-Location
}
