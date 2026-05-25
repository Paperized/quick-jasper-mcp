---
name: jasper-mcp-usage
description: Use when validating JRXML or rendering Jasper previews via MCP; includes first-time setup, cross-platform flow (Windows/WSL/Linux), known fixes, and output conventions.
---

# Jasper MCP Usage

Use this skill when the user asks to validate JRXML templates or render previews/PDF with the `jrxml_validation` MCP server.

## Initial Setup (First Import)

### Quick Setup Scripts

- Linux/WSL: `./scripts/setup.sh`
- Windows PowerShell: `./scripts/setup.ps1`

These scripts run the full setup (venv, python deps, fresh Jasper bootstrap).

### Prerequisites

- Python 3.11+
- Java JDK 8+ (`java` and `javac` in PATH)
- Maven (`mvn`) or wrapper (`mvnw`/`mvnw.cmd`)

### Python Environment

Linux / WSL:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Windows:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

### Bootstrap Jasper Jars

Run MCP tool:

```text
bootstrap_jasper_deps()
```

Expected jar location: `vendor/jasper-lib`.
MCP bootstrap is always clean (target is reset each run).

### MCP Configuration

Same flow on all platforms; only command/path format differs:

- Linux/WSL: `python3` + POSIX paths
- Windows native: `python` + Windows paths

Always set:

- `JRXML_MCP_WORKSPACE`
- `JRXML_MCP_STORAGE`
- `JASPER_LIB_DIR`
- `JRXML_MCP_RETAIN_RUNS`

## Standard Execution Flow

1. Run `validate_jrxml` first.
2. If failed, inspect `stderr/stack_trace`, fix JRXML/resources, retry validate.
3. Once validate passes, run `render_preview`.
4. For template/resource layouts, pass:
   - `jrxml_relative_path` (e.g. `templates/report.jrxml`)
   - `resource_paths` (e.g. resources folder)
5. If JRXML defines parameters, pass `report_parameters`.

## Output Policy

Unless the user explicitly requests another destination, save final output artifacts in:

- `.quick-jasper-mcp/` inside the current workspace.

If tool output lands in a temporary run directory, copy final artifacts into `.quick-jasper-mcp/`.

## Known Problems and Fixes

- `UnsupportedClassVersionError`
  - Cause: Java/runtime mismatch or stale helper class cache.
  - Fix: use a compatible JDK (baseline JDK 8+); rerun (helper class is auto-recompiled when incompatible).

- `Can't connect to X11 window server`
  - Cause: non-headless Java rendering in WSL/server.
  - Fix: run current tool version (JVM runs with headless mode enabled).

- `No JasperReports jars found`
  - Cause: jars not bootstrapped or wrong lib path.
  - Fix: run `bootstrap_jasper_deps()` and/or set `JASPER_LIB_DIR` correctly.

- Missing images/bundles/fonts at render time
  - Cause: resources not provided with expected relative structure.
  - Fix: pass `jrxml_relative_path` + `resource_paths` preserving template/resources layout.

## Response Template (Agent)

When reporting results, include:

- `validate`: success/failure + short error summary
- `render`: success/failure + short error summary
- `run_dir` and `output_path`
- final artifact path under `.quick-jasper-mcp/`

## Practical Examples (Verified)

### Validate template

```bash
source .venv/bin/activate
PYTHONPATH=src python - <<'PY'
from jrxml_mcp_server.server import validate_jrxml

res = validate_jrxml(
    jrxml_path="/path/to/project/templates/report.jrxml",
    jrxml_relative_path="templates/report.jrxml",
    resource_paths=["/path/to/project/resources"],
    keep_files=True,
)
print(res.get("success"), res.get("exit_code"))
PY
```

### Render PDF and copy final artifact to `.quick-jasper-mcp/`

```bash
source .venv/bin/activate
PYTHONPATH=src python - <<'PY'
from pathlib import Path
from jrxml_mcp_server.server import render_preview

mock_json = Path("/path/to/project/resources/sample-data/sample.json").read_text(encoding="utf-8")

res = render_preview(
    jrxml_path="/path/to/project/templates/report.jrxml",
    jrxml_relative_path="templates/report.jrxml",
    resource_paths=["/path/to/project/resources"],
    output_format="pdf",
    output_name="example-report.pdf",
    mock_data=mock_json,
    mock_data_type="json",
    locale="it_IT",
    keep_files=True,
)

if res.get("success") and res.get("output_path"):
    out = Path(".quick-jasper-mcp")
    out.mkdir(exist_ok=True)
    target = out / "example-report.pdf"
    target.write_bytes(Path(res["output_path"]).read_bytes())
    print(target)
PY
```
