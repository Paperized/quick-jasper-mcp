#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m ensurepip --upgrade
python -m pip install --upgrade setuptools wheel
if ! python -m pip install -e .; then
  echo "pip install -e . failed, retrying with --no-build-isolation (offline-friendly)"
  python -m pip install -e . --no-build-isolation
fi

if [ -x "./mvnw" ]; then
  MVN_CMD="./mvnw"
else
  MVN_CMD="mvn"
fi

PYTHONPATH=src python - <<PY
from jrxml_mcp_server.server import bootstrap_jasper_deps
res = bootstrap_jasper_deps(clean_target=True, maven_command="${MVN_CMD}")
print("bootstrap_success=", res.get("success"))
print("jar_count=", res.get("jar_count"))
print("exit_code=", res.get("exit_code"))
if not res.get("success"):
    raise SystemExit(res.get("stderr") or "bootstrap_jasper_deps failed")
PY

echo "Setup completed."
