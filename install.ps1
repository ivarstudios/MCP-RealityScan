# realityscan-mcp installer for Windows (run in PowerShell from this folder)
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
# Creates .venv, installs the package, verifies RealityScan.exe, prints the
# claude_desktop_config.json snippet with resolved paths.
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) { Write-Error "Python 3.10+ not found on PATH. Install from python.org (tick 'Add to PATH') and re-run."; exit 1 }
$ver = & $py.Source -c "import sys;print('%d.%d'%sys.version_info[:2])"
Write-Host "Python $ver at $($py.Source)"
if ([version]$ver -lt [version]"3.10") { Write-Error "Python 3.10+ required"; exit 1 }

if (-not (Test-Path ".venv")) { & $py.Source -m venv .venv }
$venvPy = Join-Path $Here ".venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip -q
& $venvPy -m pip install -e . -q
Write-Host "Installed realityscan-mcp into .venv"

# Locate RealityScan
$exe = $env:REALITYSCAN_EXE
if (-not $exe) {
  $cands = Get-ChildItem "C:\Program Files\Epic Games\RealityScan*\RealityScan.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending
  if ($cands) { $exe = $cands[0].FullName }
}
if ($exe -and (Test-Path $exe)) {
  $v = (Get-Item $exe).VersionInfo.ProductVersion
  Write-Host "RealityScan: $exe  (version $v)"
} else {
  Write-Warning "RealityScan.exe not found under C:\Program Files\Epic Games\. Set REALITYSCAN_EXE in the config below."
  $exe = "C:\Program Files\Epic Games\RealityScan_2.2\RealityScan.exe"
}

$jobs  = Join-Path $Here "jobs"
$cfg = [ordered]@{ mcpServers = [ordered]@{ RealityScan = [ordered]@{
  command = $venvPy
  args    = @("-m", "realityscan_mcp")
  env     = [ordered]@{ REALITYSCAN_EXE = $exe; RS_MCP_JOBS = $jobs }  # RS_MCP_ROOTS unset: every local data drive
} } }
$snippet = $cfg | ConvertTo-Json -Depth 6
Write-Host ""
Write-Host "Add this to %APPDATA%\Claude\claude_desktop_config.json (merge into the existing mcpServers block):"
Write-Host $snippet
[System.IO.File]::WriteAllText((Join-Path $Here "claude_desktop_config.generated.json"), $snippet)  # no BOM
# was: $snippet | Out-File -Encoding utf8 (Join-Path $Here "claude_desktop_config.generated.json")
Write-Host "Also written to claude_desktop_config.generated.json"
Write-Host ""
Write-Host "Self-test (stub, no RealityScan launched):"
& $venvPy tests\test_server.py
