#requires -Version 5.1
# ky_ppt setup: install locked Python dependencies and write runtime-profile.json
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$py = Get-Command py -ErrorAction SilentlyContinue
$prefix = @('-3', '-X', 'utf8')
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue; $prefix = @('-X', 'utf8') }
if (-not $py) { throw 'Python 3.11+ not found. Install Python first.' }
& $py.Source @prefix -c "import sys; assert sys.version_info[:2] >= (3,11)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ required.' }
& $py.Source @prefix -m pip install --disable-pip-version-check vtracer rapidocr-onnxruntime pymupdf opencv-python numpy pillow python-pptx==1.0.2 fonttools==4.61.1 shapely==2.1.2 pypinyin==0.55.0 pywin32==311
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $py.Source @prefix (Join-Path $root 'scripts\configure_runtime.py') --output (Join-Path $root 'runtime-profile.json')
if ($LASTEXITCODE -ne 0) { throw 'Runtime configuration failed.' }
# Optional quality-gate renderers (non-fatal): resvg-js preferred, svglib fallback.
$npm = Get-Command npm -ErrorAction SilentlyContinue
if ($npm) {
    & npm install --prefix $root '@resvg/resvg-js' --no-fund --no-audit --loglevel=error
}
& $py.Source @prefix -m pip install --disable-pip-version-check -q svglib rlPyCairo pycairo
# Optional semantics layer: local Ollama + a small VLM (only needed for --vlm-manifest).
$ollama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
if (Test-Path $ollama) {
    & $ollama pull qwen3-vl:2b
} else {
    Write-Output 'NOTE: Ollama not installed. For --vlm-manifest, install from https://ollama.com/download then: ollama pull qwen3-vl:2b'
}
Write-Output 'KY_PPT_SETUP_OK'