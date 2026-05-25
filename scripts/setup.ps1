$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$PythonCmd = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }

if (!(Test-Path ".venv")) {
  & $PythonCmd -m venv .venv
}

$ActivateScript = Join-Path $RepoRoot ".venv\Scripts\Activate.ps1"
. $ActivateScript

python -m ensurepip --upgrade
python -m pip install --upgrade setuptools wheel
try {
  python -m pip install -e .
} catch {
  Write-Host "pip install -e . failed, retrying with --no-build-isolation (offline-friendly)"
  python -m pip install -e . --no-build-isolation
}

$MvnCmd = if (Test-Path ".\mvnw.cmd") { ".\mvnw.cmd" } else { "mvn" }

$bootstrapScript = @"
from jrxml_mcp_server.server import bootstrap_jasper_deps
res = bootstrap_jasper_deps(clean_target=True, maven_command=r'$MvnCmd')
print('bootstrap_success=', res.get('success'))
print('jar_count=', res.get('jar_count'))
print('exit_code=', res.get('exit_code'))
if not res.get('success'):
    raise SystemExit(res.get('stderr') or 'bootstrap_jasper_deps failed')
"@

$env:PYTHONPATH = "src"
python -c $bootstrapScript

Write-Host "Setup completed."
